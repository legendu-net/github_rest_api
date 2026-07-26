import pytest

from github_rest_api.utils import (
    as_str_sequence,
    next_minor_or_strip_patch,
    strip_patch_version,
)


def test_as_str_sequence_wraps_bare_string():
    assert as_str_sequence("bug") == ["bug"]


def test_as_str_sequence_treats_empty_string_as_unset():
    # `""` must keep meaning "nothing", not "one empty entry", so that callers
    # forwarding an unset optional behave as they did before the coercion.
    assert as_str_sequence("") == []


def test_as_str_sequence_passes_through_other_sequences():
    assert as_str_sequence(["bug", "p1"]) == ["bug", "p1"]
    assert as_str_sequence(("bug",)) == ("bug",)
    assert as_str_sequence(()) == ()


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
