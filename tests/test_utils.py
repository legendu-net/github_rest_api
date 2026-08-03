from unittest.mock import patch

import pytest

from github_rest_api.scripts.utils import resolve_github_token, validate_repo
from github_rest_api.utils import (
    as_str_sequence,
    next_minor_or_strip_patch,
    strip_patch_version,
)


def test_resolve_github_token_prefers_the_explicit_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from_env")
    assert resolve_github_token("explicit") == "explicit"


def test_resolve_github_token_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from_env")
    assert resolve_github_token() == "from_env"


def test_resolve_github_token_falls_back_to_a_prompt(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch("github_rest_api.scripts.utils.getpass.getpass", return_value="typed"):
        assert resolve_github_token() == "typed"


def test_resolve_github_token_rejects_an_empty_prompt(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch("github_rest_api.scripts.utils.getpass.getpass", return_value=""):
        with pytest.raises(ValueError, match="No GitHub token"):
            resolve_github_token()


@pytest.mark.parametrize("repo", ["tasks", "a/b/c", "/b", "a/", ""])
def test_validate_repo_rejects_malformed_repositories(repo):
    with pytest.raises(ValueError, match="Invalid repo format"):
        validate_repo(repo)


def test_validate_repo_accepts_owner_slash_repo():
    validate_repo("dclong/tasks")


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
