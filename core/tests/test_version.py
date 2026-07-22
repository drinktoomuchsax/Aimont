"""Guard against __version__ drifting from the packaged metadata version."""

from __future__ import annotations

from importlib.metadata import version

import aimont


def test_dunder_version_matches_package_metadata():
    # aimont.__version__ is hand-maintained in __init__.py; the packaged
    # version comes from pyproject.toml. If someone bumps one and forgets
    # the other, aimont.__version__ silently lies — this catches that.
    assert aimont.__version__ == version("aimont")
