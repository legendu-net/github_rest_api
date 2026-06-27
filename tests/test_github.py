import os
from base64 import b64decode
from unittest.mock import MagicMock, patch

import pytest
from nacl import encoding, public

from github_rest_api.github import (
    Organization,
    Repository,
    User,
    _encrypt_secret,
    _validate_secret_name,
)
from github_rest_api.pr_content import deterministic_body, deterministic_title

_COMPARE = {
    "commits": [{"sha": "abc1234", "commit": {"message": "feat: a feature"}}],
    "files": [
        {"filename": "pkg/a.py", "status": "modified", "additions": 1, "deletions": 0}
    ],
}

TOKEN = os.environ.get("GITHUB_TOKEN", "")


def test_encrypt_secret_roundtrip():
    private_key = public.PrivateKey.generate()
    public_key = private_key.public_key.encode(encoding.Base64Encoder).decode()
    encrypted = _encrypt_secret(public_key, "s3cret-value")
    decrypted = public.SealedBox(private_key).decrypt(b64decode(encrypted))
    assert decrypted == b"s3cret-value"


@pytest.mark.parametrize(
    "name",
    ["MY_SECRET", "_underscore", "Token123", "a"],
)
def test_validate_secret_name_valid(name):
    _validate_secret_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "GITHUB_ACTIONS",
        "GITHUB_TOKEN",
        "github_token",
        "GitHub_Token",
        "1SECRET",
        "MY-SECRET",
        "MY SECRET",
    ],
)
def test_validate_secret_name_invalid(name):
    with pytest.raises(ValueError):
        _validate_secret_name(name)


def test_user_get_repositories():
    user = User(TOKEN, "dclong")
    repos = user.get_repositories()
    assert len(repos) > 0


def test_organization_get_repositories():
    org = Organization(TOKEN, "legendu-net")
    repos = org.get_repositories()
    assert len(repos) > 0


def test_repository_get_branch():
    repo = Repository(TOKEN, "legendu-net/github_rest_api")
    branch = repo.get_branch("main")
    assert branch["name"] == "main"


def test_get_issues_passes_url_and_state():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_extract_all", return_value=[]) as mock_extract:
        repo.get_issues()
    assert mock_extract.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues"
    )
    assert mock_extract.call_args.kwargs["params"] == {"state": "open"}


def test_get_issue_comments_passes_url():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_extract_all", return_value=[]) as mock_extract:
        repo.get_issue_comments(7)
    assert mock_extract.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/comments"
    )


def test_compare_url_encodes_branch_names():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.json.return_value = {"commits": [], "files": []}
    with patch.object(repo, "_get", return_value=response) as mock_get:
        repo.compare(base="feature/a b", head="dev/x")
    assert (
        mock_get.call_args.kwargs["url"]
        == "https://api.github.com/repos/owner/name/compare/feature%2Fa%20b...dev%2Fx"
    )


def test_create_pull_request_returns_existing():
    repo = Repository("token", "owner/name")
    existing = {"head": {"ref": "dev"}, "base": {"ref": "main"}, "number": 7}
    with (
        patch.object(repo, "get_pull_requests", return_value=[existing]),
        patch.object(repo, "_post") as mock_post,
    ):
        result = repo.create_pull_request(
            {"head": "dev", "base": "main", "title": "t"}, model="anthropic/x"
        )
    # An existing PR is returned without creating or generating anything.
    assert result == existing
    mock_post.assert_not_called()


def test_create_pull_request_generates_description():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"number": 1}
    with (
        patch.object(repo, "get_pull_requests", return_value=[]),
        patch.object(
            repo, "_generate_pull_request_content", return_value=("feat: x", "body")
        ) as mock_gen,
        patch.object(repo, "_post", return_value=response) as mock_post,
    ):
        repo.create_pull_request({"head": "dev", "base": "main"}, model="anthropic/x")
    mock_gen.assert_called_once()
    sent = mock_post.call_args.kwargs["json"]
    assert sent["title"] == "feat: x"
    assert sent["body"] == "body"


def test_create_pull_request_respects_user_title_and_body():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"number": 1}
    with (
        patch.object(repo, "get_pull_requests", return_value=[]),
        patch.object(repo, "_generate_pull_request_content") as mock_gen,
        patch.object(repo, "_post", return_value=response) as mock_post,
    ):
        repo.create_pull_request(
            {"head": "dev", "base": "main", "title": "my title", "body": "my body"},
            model="anthropic/x",
        )
    # Both fields provided: nothing is generated and the caller's values are used.
    mock_gen.assert_not_called()
    sent = mock_post.call_args.kwargs["json"]
    assert sent["title"] == "my title"
    assert sent["body"] == "my body"


def test_create_pull_request_generates_only_missing_field():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"number": 1}
    with (
        patch.object(repo, "get_pull_requests", return_value=[]),
        patch.object(
            repo, "_generate_pull_request_content", return_value=("feat: x", "body")
        ),
        patch.object(repo, "_post", return_value=response) as mock_post,
    ):
        repo.create_pull_request(
            {"head": "dev", "base": "main", "title": "my title"},
            model="anthropic/x",
        )
    # The provided title is kept; only the missing body is generated.
    sent = mock_post.call_args.kwargs["json"]
    assert sent["title"] == "my title"
    assert sent["body"] == "body"


def test_create_pull_request_skips_generation_when_both_provided():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"number": 1}
    with (
        patch.object(repo, "get_pull_requests", return_value=[]),
        patch.object(repo, "_generate_pull_request_content") as mock_gen,
        patch.object(repo, "_post", return_value=response) as mock_post,
    ):
        repo.create_pull_request(
            {"head": "dev", "base": "main", "title": "t", "body": "b"}
        )
    mock_gen.assert_not_called()
    sent = mock_post.call_args.kwargs["json"]
    assert sent["title"] == "t"
    assert sent["body"] == "b"


def test_create_pull_request_generates_deterministically_by_default():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"number": 1}
    with (
        patch.object(repo, "get_pull_requests", return_value=[]),
        patch.object(
            repo, "_generate_pull_request_content", return_value=("chore: x", "body")
        ) as mock_gen,
        patch.object(repo, "_post", return_value=response) as mock_post,
    ):
        repo.create_pull_request({"head": "dev", "base": "main"})
    mock_gen.assert_called_once()
    # No model means deterministic generation.
    assert mock_gen.call_args.kwargs["model"] == ""
    assert mock_post.call_args.kwargs["json"]["title"] == "chore: x"


def test_generate_pull_request_content_empty_model_skips_llm():
    repo = Repository("token", "owner/name")
    with (
        patch.object(repo, "compare", return_value=_COMPARE),
        patch("github_rest_api.github.generate_pr_content") as mock_llm,
    ):
        content = repo._generate_pull_request_content(base="main", head="dev", model="")
    mock_llm.assert_not_called()
    assert content == (deterministic_title(_COMPARE), deterministic_body(_COMPARE))


def test_generate_pull_request_content_falls_back_on_error():
    repo = Repository("token", "owner/name")
    with (
        patch.object(repo, "compare", return_value=_COMPARE),
        patch(
            "github_rest_api.github.generate_pr_content",
            side_effect=RuntimeError("no key"),
        ),
    ):
        content = repo._generate_pull_request_content(
            base="main", head="dev", model="m"
        )
    assert content == (deterministic_title(_COMPARE), deterministic_body(_COMPARE))


def test_generate_pull_request_content_empty_returns_none():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "compare", return_value={"commits": [], "files": []}):
        assert (
            repo._generate_pull_request_content(base="main", head="dev", model="m")
            is None
        )
