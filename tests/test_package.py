"""The scaffolding works: the package imports and reports a version."""

import aiosolarfocus


def test_version_is_reported() -> None:
    assert isinstance(aiosolarfocus.__version__, str)
    assert aiosolarfocus.__version__
