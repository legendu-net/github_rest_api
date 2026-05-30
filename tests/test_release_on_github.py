from pathlib import Path
from unittest.mock import patch
import pytest
from github_rest_api.scripts.github.release_on_github import _get_release_tag

ROOT = Path(".")


def test_tag_already_normalized():
    assert _get_release_tag("v1.2.3", ROOT) == "v1.2.3"


def test_tag_bare_gets_v_prefix(capsys):
    assert _get_release_tag("1.2.3", ROOT) == "v1.2.3"
    assert "normalized" in capsys.readouterr().out


def test_tag_with_surrounding_whitespace():
    assert _get_release_tag("  v1.2.3  ", ROOT) == "v1.2.3"


def test_empty_tag_falls_back_to_version():
    with patch(
        "github_rest_api.scripts.github.release_on_github.get_project_version",
        return_value="1.2.3",
    ):
        assert _get_release_tag("", ROOT) == "v1.2.3"


def test_whitespace_tag_falls_back_to_version():
    with patch(
        "github_rest_api.scripts.github.release_on_github.get_project_version",
        return_value="1.2.3",
    ):
        assert _get_release_tag("   ", ROOT) == "v1.2.3"


def test_empty_tag_no_version_exits():
    with patch(
        "github_rest_api.scripts.github.release_on_github.get_project_version",
        return_value="",
    ):
        with pytest.raises(ValueError):
            _get_release_tag("", ROOT)


def test_invalid_tag_format_exits():
    with pytest.raises(ValueError):
        _get_release_tag("invalid", ROOT)


def test_no_validate_skips_validation_and_normalization():
    assert _get_release_tag("invalid", ROOT, validate=False) == "invalid"
    assert _get_release_tag("1.2.3", ROOT, validate=False) == "1.2.3"


def test_prerelease_tag():
    assert _get_release_tag("v1.2.3-alpha.1", ROOT) == "v1.2.3-alpha.1"


def test_build_metadata_tag():
    assert _get_release_tag("1.2.3+build", ROOT) == "v1.2.3+build"
