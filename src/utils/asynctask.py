# SPDX-License-Identifier: MIT
# (c) 2023 knuxify and Ear Tag contributors

import asyncio
import time
import traceback
from typing import Any, Awaitable, Callable

try:
    from asyncio.queues import Queue, QueueShutDown
except ImportError:
    from backports.asyncio.queues import Queue, QueueShutDown

from gi.repository import GLib, GObject

from .._async import event_loop
from ..logger import logger


class EartagAsyncTask(GObject.Object):
    """
    Convenience class for creating async tasks.

    Provides a "progress" property that can be used by target functions
    to signify a progress change. This is a float from 0 to 1 and is
    passed directly to GtkProgressBar.

    The passed target function must be an async function/coroutine.
    """

    def __init__(self, target: Callable[..., Awaitable[Any]], *args, **kwargs):
        super().__init__()
        self._progress = 0
        self.target = target
        self.set_args(args, kwargs)
        self.task = None
        self.progress_lock = asyncio.Lock()

    def set_args(self, args=None, kwargs=None):
        if args:
            self.args = args
        else:
            self.args = []
        if kwargs:
            self.kwargs = kwargs
        else:
            self.kwargs = {}

    def wait_for_completion(self):
        while self.is_running:
            time.sleep(0.25)

    def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()

    async def _run(self):
        try:
            return await self.target(*self.args, **self.kwargs)
        except Exception as e:
            # HACK: We do exception handling here since for some reason it
            # doesn't print the exceptions otherwise
            traceback.print_exc()
            raise e from e

    def run(self):
        self.task = event_loop.create_task(self._run())
        self.emit("task-started")
        self.task.add_done_callback(self.emit_task_done)

    @GObject.Property(type=float, minimum=0, maximum=1.0000001)
    def progress(self):
        """
        Float from 0 to 1 signifying the current progress of the operation.
        When the task is done, this automatically resets to 0.

        This value is set by the target function.
        """
        return self._progress

    @progress.setter
    def progress(self, value):
        self._progress = value

    @GObject.Signal
    def task_started(self):
        self.notify("is-running")

    @GObject.Signal
    def task_done(self):
        self.reset_progress()
        self.notify("is-running")

    @GObject.Property(type=bool, default=False)
    def is_running(self):
        if not self.task:
            return False
        return not self.task.done()

    def reset_progress(self):
        self.props.progress = 0

    def set_progress(self, value):
        """
        Wrapper around self.props.progress that updates the progress.
        """
        self.props.progress = value

    def increment_progress(self, value):
        """
        Wrapper around self.props.progress that increments the progress.
        """
        self.props.progress = self.props.progress + value

    async def set_progress_threadsafe(self, value):
        """
        Wrapper around self.props.progress that updates the progress,
        with a lock to prevent multiple threads incremeting it at the same time.
        """
        async with self.progress_lock:
            self.props.progress = value

    async def increment_progress_threadsafe(self, value):
        """
        Wrapper around self.props.progress that increments the progress,
        with a lock to prevent multiple threads incremeting it at the same time.
        """
        async with self.progress_lock:
            self.props.progress = self.props.progress + value

    progress_pulse = GObject.Signal()

    def emit_progress_pulse(self):
        """
        Send a progress pulse, for processes where the exact progress isn't known.
        """
        self.emit("progress-pulse")

    def emit_task_done(self, *args):
        """
        Wrapper around self.emit('task-done').
        """
        self.emit("task-done")


class EartagAsyncMultitasker(EartagAsyncTask):
    """
    Subclass of AsyncTask which runs a task using multiple workers with queued data.

    The Multitasker class provides a FIFO queue - this is a standard Python asyncio queue,
    and items can be picked up with the `.queue.get()` method.

    The typical loop of operations for the Multitasker looks like this:

    - Create the Multitasker object, set the target function
        - The target function must accept an EartagAsyncMultitasker object in the
          `tasker` parameter
    - In your run function:
        - Call the `.run()` method to spawn the workers
        - Start adding items to the queue with `.queue_put(item)`
        - Once all items are added, call `.queue_done()`
    - In your worker function:
        - Accept a parameter named "item"; this will contain the item to work on

    All AsyncTask properties/signals (like progress and task-done) apply here as well.
    task-done is only emitted once all workers finish execution.
    """

    def __init__(self, target: Callable[..., Awaitable[Any]], workers: int, *args, **kwargs):
        super().__init__(target, *args, **kwargs)
        assert target
        self.queue = Queue()
        self.queue_done_event = asyncio.Event()
        self.workers = workers
        self.tasks = set()
        self._is_running = False
        self.running_lock = asyncio.Lock()

        #: If you want the multitasker to execute your task directly as the worker
        #: task, rather than managing the queue internally, set `.run_raw` to True.
        #: The "tasker" argument will be provided to your function with the Multitasker
        #: objects, so that you can write your own queue handling implementation.
        #: See _worker function for an example of how to use the APIs.
        self.run_raw: bool = False

        #: GLib priority to use for the worker tasks.
        self.priority: int = GLib.PRIORITY_DEFAULT_IDLE

        self.errors = []

        self.n_items = 0
        self.n_done = 0

    async def _worker(self):
        while True:
            try:
                item = await self.queue.get()
            except QueueShutDown:
                break
            try:
                await self.target(item, *self.args, **self.kwargs)
            except:  # noqa: E722
                self.errors.append(
                    f"{self.target}: Error while processing {item}:\n\n{traceback.format_exc()}"
                )
                logger.error(self.errors[-1])
                pass

            self.n_done += 1
            if self.queue_done_event.is_set():
                _progress_task = event_loop.create_task(
                    self.set_progress_threadsafe(self.n_done / self.n_items)
                )
                try:
                    _progress_task.set_priority(GLib.PRIORITY_LOW)
                except AttributeError:
                    # PyGObject <3.51.0 does not have set_priority
                    pass
            else:
                self.emit_progress_pulse()

    async def _run_multitasker(self):
        async with self.running_lock:
            self._is_running = True
            self.emit("task-started")

            async with asyncio.TaskGroup() as tg:
                for _i in range(self.workers):
                    _task = tg.create_task(self._worker())
                    try:
                        _task.set_priority(self.priority)
                    except AttributeError:
                        # PyGObject <3.51.0 does not have set_priority
                        pass
                    self.tasks.add(_task)

            # The task group will block until all tasks are done
            self._is_running = False
            self.emit_task_done()

    def spawn_workers(self):
        """Start the task by spawning workers."""
        self.n_items = 0
        self.n_done = 0
        self.queue = Queue()
        self.queue_done_event.clear()
        self.clear_errors()
        self.task = event_loop.create_task(self._run_multitasker())

    def run(self):
        """Compatibility shim, please use spawn_workers() instead."""
        return self.spawn_workers()

    def stop(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()

        while self._is_running:
            time.sleep(0.1)

        del self.tasks
        self.tasks = set()

        self.queue_done_event.set()
        self.queue.shutdown(immediate=True)

    def wait_for_completion(self):
        while self.task and not self.task.done():
            time.sleep(0.25)

    async def wait_for_completion_async(self):
        async with self.running_lock:
            pass

    @GObject.Property(type=bool, default=False)
    def is_running(self):
        return self._is_running

    def queue_put(self, item):
        """Put an item in the queue."""
        self.queue.put_nowait(item)
        self.n_items += 1

    def queue_put_multiple(self, items, mark_as_done: bool = False):
        """Put all items from the list in the queue."""
        for item in items:
            self.queue_put(item)
        if mark_as_done:
            self.queue_done()

    def queue_done(self):
        """Indicate to the workers that the queue is done being filled."""
        self.queue_done_event.set()
        self.queue.shutdown()

    def clear_errors(self):
        """Clear error information from the tasker."""
        del self.errors
        self.errors = []
