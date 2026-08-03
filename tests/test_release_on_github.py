from pathlib import Path
from unittest.mock import patch

import pytest

from github_rest_api.scripts.github import release_on_github as module
from github_rest_api.scripts.github.release_on_github import (
    _get_release_tag,
    parse_args,
)

ROOT = Path(".")
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


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


def test_tag_holding_a_token_is_refused_before_it_is_echoed():
    """-t is the tag here, not the token, so this slip is easy to make."""
    with pytest.raises(ValueError, match="looks like a GitHub token") as excinfo:
        _get_release_tag(TOKEN, ROOT)
    # The semver failure would otherwise quote the tag back at the user.
    assert TOKEN not in str(excinfo.value)


def test_tag_holding_a_token_is_refused_even_without_validation():
    """--no-validate skips the semver check, so it cannot be the only guard."""
    with pytest.raises(ValueError, match="looks like a GitHub token"):
        _get_release_tag(TOKEN, ROOT, validate=False)


def test_release_refuses_a_token_in_the_branch():
    with patch.object(module, "Repository") as repository:
        with pytest.raises(ValueError, match="looks like a GitHub token"):
            module.release_on_github(token="", branch=TOKEN)
    repository.assert_not_called()


def test_release_refuses_a_token_in_the_notes():
    with patch.object(module, "Repository") as repository:
        with pytest.raises(ValueError, match="looks like a GitHub token"):
            module.release_on_github(token="", branch="main", notes=TOKEN)
    repository.assert_not_called()


def test_parse_args_has_no_short_option_for_the_tag(capsys):
    """-t reads as the token, and the tag becomes the public release name."""
    with pytest.raises(SystemExit):
        parse_args(["-t", "v1.2.3"])
    assert parse_args(["--tag", "v1.2.3"]).tag == "v1.2.3"


def test_parse_args_redacts_a_token_passed_to_the_removed_short_option(capsys):
    """The slip this removal targets must not print the token either."""
    with pytest.raises(SystemExit):
        parse_args(["-t", TOKEN])
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "<redacted>" in err


def test_parse_args_redacts_a_token_from_argparse_errors(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--bogus", TOKEN])
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "<redacted>" in err
