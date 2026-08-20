"""
Shim for global config access.
"""

from enum import IntEnum

from gi.repository import Gio

from . import APP_ID, TEST_SUITE


class DLCoverSize(IntEnum):
    NO_DOWNLOAD = 0
    _250PX = 250
    _500PX = 500
    _1200PX = 1200
    MAX_SIZE = 2000

    @classmethod
    def index_to_item(cls, i):
        if i == 0:
            return cls.NO_DOWNLOAD
        elif i == 1:
            return cls._250PX
        elif i == 2:
            return cls._500PX
        elif i == 3:
            return cls._1200PX
        elif i == 4:
            return cls.MAX_SIZE
        raise ValueError

    @classmethod
    def item_to_index(cls, o):
        o = int(o)

        if o == 0:
            return 0
        elif o == 250:
            return 1
        elif o == 500:
            return 2
        elif o == 1200:
            return 3
        elif o == 2000:
            return 4
        raise ValueError


class MockConfig(dict):
    """Fake config-like class used by the test suite."""

    def get_enum(self, name):
        if name == "musicbrainz-cover-size":
            return 250


if TEST_SUITE:
    # We need to use a fake config for the test suite, as the real config schema is not guaranteed
    # to be available when running tests.
    config = MockConfig()
    config["acoustid-confidence-treshold"] = 85
    config["musicbrainz-confidence-treshold"] = 85
else:
    config = Gio.Settings.new(APP_ID)
