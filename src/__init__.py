# SPDX-License-Identifier: MIT
# (c) 2026 Ear Tag contributors

import os

# The following variables are set in the main eartag "binary" (/usr/bin/eartag,
# eartag.in in the repo). These default values are provided for type checking
# convenience.

TEST_SUITE: bool = "PYTEST_VERSION" in os.environ
ACOUSTID_API_KEY: str = ""
APP_ID: str = "app.drey.EarTag"
APP_GRESOURCE_PATH: str = "/app/drey/EarTag"
DEVEL: bool = False
VERSION: str = "0.0.0"
