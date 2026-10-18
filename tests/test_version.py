from importlib.metadata import version

import meow_now


def test_installed_version_matches_package_version():
    assert version("meow-now") == meow_now.__version__
