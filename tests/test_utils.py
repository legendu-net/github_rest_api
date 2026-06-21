import pytest

from github_rest_api.utils import next_minor_or_strip_patch, strip_patch_version


def test_next_minor_or_strip_patch():
    # Examples from docstring
    assert next_minor_or_strip_patch("5.4.6", 4) == "5.5.0"
    assert next_minor_or_strip_patch("5.4.6", 8) == "5.4.0"

    # Edge cases
    assert next_minor_or_strip_patch("1.0.0", 0) == "1.1.0"
    assert next_minor_or_strip_patch("1.0.0", 1) == "1.0.0"
    assert next_minor_or_strip_patch("1.2.3", 3) == "1.3.0"
    assert next_minor_or_strip_patch("1.2.3", 4) == "1.2.0"

    # Different lengths
    assert next_minor_or_strip_patch("1", 0) == "1.1.0"
    assert next_minor_or_strip_patch("1", 1) == "1.0.0"
    assert next_minor_or_strip_patch("1.2", 0) == "1.3.0"
    assert next_minor_or_strip_patch("1.2", 1) == "1.2.0"

    with pytest.raises(ValueError):
        next_minor_or_strip_patch("1.2.3.4", 1)
    with pytest.raises(ValueError):
        next_minor_or_strip_patch("", 1)


def test_strip_patch_version():
    assert strip_patch_version("1") == "1.0.0"
    assert strip_patch_version("1.2") == "1.2.0"
    assert strip_patch_version("1.2.3") == "1.2.0"
    with pytest.raises(ValueError):
        strip_patch_version("1.2.3.4")
