import os
from base64 import b64decode
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from nacl import encoding, public

from github_rest_api.github import (
    DEFAULT_TIMEOUT,
    IssueState,
    IssueStateReason,
    MergeMethod,
    Organization,
    Repository,
    User,
    _approver_review_states,
    _conventional_type,
    _encrypt_secret,
    _field_gate_failure,
    _login,
    _marker_approved,
    _old_enough,
    _title_type_allowed,
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


_HTTP_HELPERS = [
    ("_get", "get"),
    ("_post", "post"),
    ("_put", "put"),
    ("_patch", "patch"),
    ("_delete", "delete"),
]


@pytest.mark.parametrize(("method", "verb"), _HTTP_HELPERS)
def test_http_helper_defaults_timeout(method, verb):
    repo = Repository("token", "owner/name")
    with patch(f"github_rest_api.github.requests.{verb}") as mock_request:
        getattr(repo, method)(url="https://api.github.com/x")
    assert mock_request.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


@pytest.mark.parametrize(("method", "verb"), _HTTP_HELPERS)
def test_http_helper_honors_caller_timeout(method, verb):
    """A caller-supplied `timeout` overrides the default instead of colliding."""
    repo = Repository("token", "owner/name")
    with patch(f"github_rest_api.github.requests.{verb}") as mock_request:
        getattr(repo, method)(url="https://api.github.com/x", timeout=30)
    assert mock_request.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize(("method", "verb"), _HTTP_HELPERS)
def test_http_helper_rejects_timeout_none(method, verb):
    """`timeout=None` is rejected before the request is sent, never forwarded.

    `requests` reads None as "wait forever", so it is refused rather than
    silently replaced by the default.
    """
    repo = Repository("token", "owner/name")
    with patch(f"github_rest_api.github.requests.{verb}") as mock_request:
        with pytest.raises(ValueError, match="timeout=None"):
            getattr(repo, method)(url="https://api.github.com/x", timeout=None)
    mock_request.assert_not_called()


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


def test_create_issue_passes_url_and_payload():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.json.return_value = {"number": 7}
    with patch.object(repo, "_post", return_value=response) as mock_post:
        assert repo.create_issue("a title", body="a body") == {"number": 7}
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues"
    )
    assert mock_post.call_args.kwargs["json"] == {"title": "a title", "body": "a body"}


def test_create_issue_omits_empty_optional_fields():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_post", return_value=MagicMock()) as mock_post:
        repo.create_issue("a title")
    assert mock_post.call_args.kwargs["json"] == {"title": "a title"}


def test_create_issue_accepts_bare_string_labels_and_assignees():
    """A bare string is one label/assignee, not one per character."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_post", return_value=MagicMock()) as mock_post:
        repo.create_issue("a title", labels="bug", assignees="dclong")
    assert mock_post.call_args.kwargs["json"] == {
        "title": "a title",
        "labels": ["bug"],
        "assignees": ["dclong"],
    }


def test_create_issue_passes_labels_and_assignees():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_post", return_value=MagicMock()) as mock_post:
        repo.create_issue("a title", labels=("bug",), assignees=("dclong",))
    assert mock_post.call_args.kwargs["json"] == {
        "title": "a title",
        "labels": ["bug"],
        "assignees": ["dclong"],
    }


def test_create_issue_reaches_requests_post():
    """Check the request reaching `requests.post`, not just the `_post` call.

    The other tests patch `_post`, so they cannot catch a malformed call to
    `requests.post` itself.
    """
    repo = Repository("token", "owner/name")
    with patch("github_rest_api.github.requests.post") as mock_post:
        repo.create_issue("a title")
    assert mock_post.call_args.kwargs["json"] == {"title": "a title"}


def test_create_issue_comment_reaches_requests_post():
    """See `test_create_issue_reaches_requests_post` for why this layer."""
    repo = Repository("token", "owner/name")
    with patch("github_rest_api.github.requests.post") as mock_post:
        repo.create_issue_comment(7, "a comment")
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/comments"
    )
    assert mock_post.call_args.kwargs["json"] == {"body": "a comment"}


def test_create_issue_comment_image_builds_markdown():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/a.png", alt="a shot")
    assert mock_comment.call_args.args == (7, "![a shot](<https://h.test/a.png>)")


def test_create_issue_comment_image_defaults_to_empty_alt():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/a.png")
    assert mock_comment.call_args.args == (7, "![](<https://h.test/a.png>)")


def test_create_issue_comment_image_places_body_above_image():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/a.png", body="See below.")
    assert mock_comment.call_args.args == (
        7,
        "See below.\n\n![](<https://h.test/a.png>)",
    )


def test_create_issue_comment_image_url_with_space_stays_intact():
    """The angle brackets are what keep such a URL from ending the link early."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/shot (1).png")
    assert mock_comment.call_args.args == (7, "![](<https://h.test/shot (1).png>)")


def test_create_issue_comment_image_escapes_brackets_in_alt():
    """An unescaped bracket closes the label and kills the image silently."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/a.png", alt="chart [v2]")
    assert mock_comment.call_args.args == (
        7,
        "![chart \\[v2\\]](<https://h.test/a.png>)",
    )


def test_create_issue_comment_image_escapes_backslash_in_alt_first():
    """A literal backslash must not turn into an escape for the next character.

    The alt text needs both a backslash and a bracket to pin the ordering:
    without the bracket, escaping in either order yields the same string.
    """
    repo = Repository("token", "owner/name")
    with patch.object(repo, "create_issue_comment") as mock_comment:
        repo.create_issue_comment_image(7, "https://h.test/a.png", alt="a\\[b")
    assert mock_comment.call_args.args == (7, "![a\\\\\\[b](<https://h.test/a.png>)")


def test_create_issue_comment_image_reaches_requests_post():
    """See `test_create_issue_reaches_requests_post` for why this layer."""
    repo = Repository("token", "owner/name")
    with patch("github_rest_api.github.requests.post") as mock_post:
        repo.create_issue_comment_image(7, "https://h.test/a.png", alt="a shot")
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/comments"
    )
    assert mock_post.call_args.kwargs["json"] == {
        "body": "![a shot](<https://h.test/a.png>)"
    }


def test_create_issue_passes_milestone():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_post", return_value=MagicMock()) as mock_post:
        repo.create_issue("a title", milestone=3)
    assert mock_post.call_args.kwargs["json"] == {"title": "a title", "milestone": 3}


def test_get_issue_passes_url():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.json.return_value = {"number": 7}
    with patch.object(repo, "_get", return_value=response) as mock_get:
        assert repo.get_issue(7) == {"number": 7}
    assert mock_get.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7"
    )


def _mock_helper(repo, method, payload=None):
    """Patch an HTTP helper of `repo` so its response decodes to `payload`."""
    response = MagicMock()
    response.json.return_value = payload
    return patch.object(repo, method, return_value=response)


def test_update_issue_sends_only_given_fields_and_returns_json():
    """An unmentioned field is left out entirely, never nulled or re-sent."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch", {"number": 7}) as mock_patch:
        assert repo.update_issue(7, title="new title") == {"number": 7}
    assert mock_patch.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7"
    )
    assert mock_patch.call_args.kwargs["json"] == {"title": "new title"}


def test_update_issue_sends_empty_body():
    """`body=""` empties the body; it must not be dropped as falsy."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, body="")
    assert mock_patch.call_args.kwargs["json"] == {"body": ""}


def test_update_issue_clears_milestone_with_none():
    """`milestone=None` detaches the milestone, distinct from omitting it."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, milestone=None)
    assert mock_patch.call_args.kwargs["json"] == {"milestone": None}


def test_update_issue_clears_labels_and_assignees_with_empty_sequence():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, labels=(), assignees=())
    assert mock_patch.call_args.kwargs["json"] == {"labels": [], "assignees": []}


def test_update_issue_accepts_bare_string_labels_and_assignees():
    """A bare string is one label/assignee, not one per character."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, labels="bug", assignees="dclong")
    assert mock_patch.call_args.kwargs["json"] == {
        "labels": ["bug"],
        "assignees": ["dclong"],
    }


def test_update_issue_serializes_enum_values():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(
            7, state=IssueState.CLOSED, state_reason=IssueStateReason.NOT_PLANNED
        )
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "not_planned",
    }


def test_update_issue_sends_null_state_reason():
    """`state_reason=None` reaches GitHub as JSON null, not as an omitted key."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, state=IssueState.OPEN, state_reason=None)
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "open",
        "state_reason": None,
    }


@pytest.mark.parametrize("state_reason", [None, IssueStateReason.NOT_PLANNED])
def test_update_issue_rejects_state_reason_without_state(state_reason):
    """GitHub ignores `state_reason` unless `state` changes, so it is refused.

    Sending it alone would look like an update but silently do nothing.
    """
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="state_reason is ignored"):
            repo.update_issue(7, state_reason=state_reason)
    mock_patch.assert_not_called()


def test_update_issue_passes_duplicate_issue_id():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(
            7,
            state=IssueState.CLOSED,
            state_reason=IssueStateReason.DUPLICATE,
            duplicate_issue_id=98765,
        )
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "duplicate",
        "duplicate_issue_id": 98765,
    }


def test_update_issue_accepts_duplicate_as_bare_string():
    """The pairing check compares by value, so a plain string works too.

    The signature names the enums, but a state read from JSON or a command line
    arrives as a plain string; the checks stay value-based so it still works.
    Hence the deliberately untyped arguments below.
    """
    repo = Repository("token", "owner/name")
    # Passed as untyped keywords, the way such a value reaches the call in
    # practice; spelling them out inline would just be a type error.
    untyped: dict[str, Any] = {"state": "closed", "state_reason": "duplicate"}
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, duplicate_issue_id=98765, **untyped)
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "duplicate",
        "duplicate_issue_id": 98765,
    }


def test_update_issue_omits_duplicate_issue_id_by_default():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue(7, title="t")
    assert "duplicate_issue_id" not in mock_patch.call_args.kwargs["json"]


def test_update_issue_requires_duplicate_issue_id_for_duplicate():
    """GitHub 422s on `duplicate` without the id, so it is caught early."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="requires duplicate_issue_id"):
            repo.update_issue(
                7, state=IssueState.CLOSED, state_reason=IssueStateReason.DUPLICATE
            )
    mock_patch.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "t"},
        {"state": IssueState.CLOSED, "state_reason": IssueStateReason.NOT_PLANNED},
        {"state": IssueState.OPEN, "state_reason": None},
    ],
)
def test_update_issue_rejects_duplicate_issue_id_without_duplicate(kwargs):
    """GitHub drops the id unless the reason is `duplicate`, so it is refused.

    Sending it regardless would look like it took effect when it did not.
    """
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="duplicate_issue_id is ignored"):
            repo.update_issue(7, duplicate_issue_id=98765, **kwargs)
    mock_patch.assert_not_called()


def test_close_issue_as_duplicate_without_id_raises():
    """`close_issue` inherits the pairing checks from `update_issue`."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="requires duplicate_issue_id"):
            repo.close_issue(7, IssueStateReason.DUPLICATE)
    mock_patch.assert_not_called()


def test_update_issue_without_fields_raises():
    """A field-less update would be a wasted request, so it is refused."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="No field to update"):
            repo.update_issue(7)
    mock_patch.assert_not_called()


@pytest.mark.parametrize("state", [IssueState.ALL, "reopened", ""])
def test_update_issue_rejects_invalid_state(state):
    """`all` is a listing filter, not a state an issue can be set to."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="Invalid issue state"):
            repo.update_issue(7, state=state)
    mock_patch.assert_not_called()


def test_close_issue_defaults_to_completed():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.close_issue(7)
    assert mock_patch.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7"
    )
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "completed",
    }


def test_close_issue_passes_state_reason():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.close_issue(7, IssueStateReason.NOT_PLANNED)
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "not_planned",
    }


def test_close_issue_as_duplicate_passes_duplicate_issue_id():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.close_issue(7, IssueStateReason.DUPLICATE, duplicate_issue_id=98765)
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "closed",
        "state_reason": "duplicate",
        "duplicate_issue_id": 98765,
    }


def test_reopen_issue_payload():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.reopen_issue(7)
    assert mock_patch.call_args.kwargs["json"] == {
        "state": "open",
        "state_reason": "reopened",
    }


def test_update_issue_comment_targets_comment_id():
    """Comments are edited by comment id, not by the issue number."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_issue_comment(4321, "edited")
    assert mock_patch.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/comments/4321"
    )
    assert mock_patch.call_args.kwargs["json"] == {"body": "edited"}


def test_delete_issue_comment_targets_comment_id():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_delete", return_value=MagicMock()) as mock_delete:
        repo.delete_issue_comment(4321)
    assert mock_delete.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/comments/4321"
    )


def test_get_issue_labels_passes_url_and_limit():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_extract_all", return_value=[]) as mock_extract:
        repo.get_issue_labels(7, n=5)
    assert mock_extract.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/labels"
    )
    assert mock_extract.call_args.kwargs["n"] == 5


def test_add_issue_labels_posts_to_issue_labels():
    repo = Repository("token", "owner/name")
    labels = [{"name": "bug"}, {"name": "wontfix"}]
    with _mock_helper(repo, "_post", labels) as mock_post:
        assert repo.add_issue_labels(7, ("bug", "wontfix")) == labels
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/labels"
    )
    assert mock_post.call_args.kwargs["json"] == {"labels": ["bug", "wontfix"]}


@pytest.mark.parametrize("labels", [(), "", []])
def test_add_issue_labels_rejects_empty(labels):
    """GitHub 422s on an empty `labels` array, so it is caught before the request.

    A bare `""` (an unset env var or config field) reaches here as an empty
    sequence, which is the case most likely to hit this in practice.
    """
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_post") as mock_post:
        with pytest.raises(ValueError, match="No label to add"):
            repo.add_issue_labels(7, labels)
    mock_post.assert_not_called()


def test_set_issue_labels_puts_to_issue_labels():
    """PUT replaces the label set, so an empty list clears every label."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_put") as mock_put:
        repo.set_issue_labels(7, ())
    assert mock_put.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/labels"
    )
    assert mock_put.call_args.kwargs["json"] == {"labels": []}


@pytest.mark.parametrize(
    ("labels", "method", "helper"),
    [
        ("bug", "add_issue_labels", "_post"),
        ("bug", "set_issue_labels", "_put"),
    ],
)
def test_issue_labels_accept_bare_string(labels, method, helper):
    """A bare string is one label, not one per character."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, helper) as mock_request:
        getattr(repo, method)(7, labels)
    assert mock_request.call_args.kwargs["json"] == {"labels": ["bug"]}


def test_remove_issue_label_url_encodes_name_and_returns_remaining():
    """A label name may contain spaces or slashes, so it is encoded."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_delete", [{"name": "bug"}]) as mock_delete:
        assert repo.remove_issue_label(7, "good first issue") == [{"name": "bug"}]
    assert mock_delete.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/labels/good%20first%20issue"
    )


def test_add_issue_assignees_posts_to_assignees():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_post") as mock_post:
        repo.add_issue_assignees(7, "dclong")
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/assignees"
    )
    assert mock_post.call_args.kwargs["json"] == {"assignees": ["dclong"]}


def test_remove_issue_assignees_deletes_with_body():
    """The unassign endpoint takes the logins in the DELETE body."""
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_delete", {"number": 7}) as mock_delete:
        assert repo.remove_issue_assignees(7, ("dclong", "other")) == {"number": 7}
    assert mock_delete.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/issues/7/assignees"
    )
    assert mock_delete.call_args.kwargs["json"] == {"assignees": ["dclong", "other"]}


def test_get_milestones_passes_url_state_and_limit():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_extract_all", return_value=[]) as mock_extract:
        repo.get_milestones(n=5)
    assert mock_extract.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/milestones"
    )
    assert mock_extract.call_args.kwargs["params"] == {"state": "open"}
    assert mock_extract.call_args.kwargs["n"] == 5


def test_create_milestone_omits_empty_optional_fields():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_post") as mock_post:
        repo.create_milestone("v1.0")
    assert mock_post.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/milestones"
    )
    assert mock_post.call_args.kwargs["json"] == {"title": "v1.0"}


def test_create_milestone_passes_optional_fields():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_post") as mock_post:
        repo.create_milestone(
            "v1.0", description="first", due_on="2026-12-31T00:00:00Z"
        )
    assert mock_post.call_args.kwargs["json"] == {
        "title": "v1.0",
        "description": "first",
        "due_on": "2026-12-31T00:00:00Z",
    }


def test_update_milestone_sends_only_given_fields():
    repo = Repository("token", "owner/name")
    with _mock_helper(repo, "_patch") as mock_patch:
        repo.update_milestone(3, state=IssueState.CLOSED)
    assert mock_patch.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/milestones/3"
    )
    assert mock_patch.call_args.kwargs["json"] == {"state": "closed"}


def test_update_milestone_without_fields_raises():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="No field to update"):
            repo.update_milestone(3)
    mock_patch.assert_not_called()


def test_update_milestone_rejects_invalid_state():
    """The error names the milestone, and renders the enum as its plain value."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="Invalid milestone state 'all'"):
            repo.update_milestone(3, state=IssueState.ALL)
    mock_patch.assert_not_called()


def test_update_milestone_rejects_empty_due_on():
    """GitHub takes no null due date, so an empty one is a 422 caught early."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_patch") as mock_patch:
        with pytest.raises(ValueError, match="due_on must be"):
            repo.update_milestone(3, due_on="")
    mock_patch.assert_not_called()


def test_delete_milestone_passes_url():
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_delete", return_value=MagicMock()) as mock_delete:
        repo.delete_milestone(3)
    assert mock_delete.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/milestones/3"
    )


def test_get_pull_request_review_comments_passes_url_and_limit():
    """The line-anchored comments live under /pulls/{n}/comments, not /issues."""
    repo = Repository("token", "owner/name")
    with patch.object(repo, "_extract_all", return_value=[]) as mock_extract:
        repo.get_pull_request_review_comments(7, n=5)
    assert mock_extract.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/pulls/7/comments"
    )
    assert mock_extract.call_args.kwargs["n"] == 5


def test_get_pull_request_review_comments_paginates():
    """All pages are collected, so a long review is not truncated at 100."""
    repo = Repository("token", "owner/name")
    full_page, last_page = MagicMock(), MagicMock()
    full_page.json.return_value = [{"id": i} for i in range(100)]
    last_page.json.return_value = [{"id": 100}]
    with patch.object(repo, "_get", side_effect=[full_page, last_page]) as mock_get:
        comments = repo.get_pull_request_review_comments(7)
    assert len(comments) == 101
    assert mock_get.call_count == 2


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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("chore: bump deps", "chore"),
        ("docs(readme): tweak", "docs"),
        ("feat!: breaking change", "feat"),
        ("Chore: capitalized", "chore"),
        ("no conventional prefix", None),
        ("", None),
    ],
)
def test_conventional_type(title, expected):
    assert _conventional_type(title) == expected


@pytest.mark.parametrize(
    ("title", "allowed", "expected"),
    [
        ("chore: x", ("chore", "docs"), True),
        ("docs(scope): x", ("chore", "docs"), True),
        ("feat: x", ("chore", "docs"), False),
        ("no prefix", ("chore",), False),
    ],
)
def test_title_type_allowed(title, allowed, expected):
    assert _title_type_allowed(title, allowed) is expected


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"user": {"login": "bot"}}, "bot"),
        ({"user": None}, None),
        ({}, None),
    ],
)
def test_login(obj, expected):
    assert _login(obj) == expected


def test_field_gate_failure_passes_eligible_pr():
    pr = {"draft": False, "user": {"login": "bot"}, "title": "chore: bump"}
    assert _field_gate_failure(pr, ["bot"], ("chore",)) is None


@pytest.mark.parametrize(
    "pr",
    [
        {"draft": True, "user": {"login": "bot"}, "title": "chore: x"},
        {"draft": False, "user": {"login": "stranger"}, "title": "chore: x"},
        {"draft": False, "user": {"login": "bot"}, "title": "feat: x"},
    ],
)
def test_field_gate_failure_rejects(pr):
    assert _field_gate_failure(pr, ["bot"], ("chore",)) is not None


def _review(login, state):
    return {"user": {"login": login}, "state": state}


def test_approver_review_states_approved():
    assert _approver_review_states([_review("bot", "APPROVED")], ["bot"]) == {
        "APPROVED"
    }


def test_approver_review_states_ignores_non_approvers():
    assert _approver_review_states([_review("stranger", "APPROVED")], ["bot"]) == set()


def test_approver_review_states_uses_latest_state_per_login():
    # A later APPROVED overrides an earlier CHANGES_REQUESTED for the same login.
    reviews = [_review("bot", "CHANGES_REQUESTED"), _review("bot", "APPROVED")]
    assert _approver_review_states(reviews, ["bot"]) == {"APPROVED"}


def test_approver_review_states_latest_changes_requested():
    reviews = [_review("bot", "APPROVED"), _review("bot", "CHANGES_REQUESTED")]
    assert _approver_review_states(reviews, ["bot"]) == {"CHANGES_REQUESTED"}


def test_approver_review_states_combines_multiple_approvers():
    reviews = [_review("bot", "APPROVED"), _review("human", "CHANGES_REQUESTED")]
    assert _approver_review_states(reviews, ["bot", "human"]) == {
        "APPROVED",
        "CHANGES_REQUESTED",
    }


def test_approver_review_states_ignores_commented_reviews():
    assert _approver_review_states([_review("bot", "COMMENTED")], ["bot"]) == set()


def test_approver_review_states_dismissed_clears_earlier_approval():
    reviews = [_review("bot", "APPROVED"), _review("bot", "DISMISSED")]
    assert _approver_review_states(reviews, ["bot"]) == {"DISMISSED"}


def test_approver_review_states_comment_does_not_clear_approval():
    reviews = [_review("bot", "APPROVED"), _review("bot", "COMMENTED")]
    assert _approver_review_states(reviews, ["bot"]) == {"APPROVED"}


_HEAD_COMMITTED = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
_AFTER_HEAD = "2026-06-28T12:05:00Z"
_BEFORE_HEAD = "2026-06-28T11:55:00Z"


def test_marker_approved_true():
    comments = [
        {
            "user": {"login": "bot"},
            "body": "looks good <!-- ok -->",
            "created_at": _AFTER_HEAD,
        }
    ]
    assert _marker_approved(comments, ["bot"], "<!-- ok -->", _HEAD_COMMITTED) is True


def test_marker_approved_requires_approver_author():
    comments = [
        {
            "user": {"login": "stranger"},
            "body": "<!-- ok -->",
            "created_at": _AFTER_HEAD,
        }
    ]
    assert _marker_approved(comments, ["bot"], "<!-- ok -->", _HEAD_COMMITTED) is False


def test_marker_approved_requires_marker():
    comments = [{"user": {"login": "bot"}, "body": "nice", "created_at": _AFTER_HEAD}]
    assert _marker_approved(comments, ["bot"], "<!-- ok -->", _HEAD_COMMITTED) is False


def test_marker_approved_ignores_comment_before_head_commit():
    comments = [
        {"user": {"login": "bot"}, "body": "<!-- ok -->", "created_at": _BEFORE_HEAD}
    ]
    assert _marker_approved(comments, ["bot"], "<!-- ok -->", _HEAD_COMMITTED) is False


def test_marker_approved_ignores_comment_at_head_commit():
    # A marker posted in the same second as the head commit is rejected: the gate
    # is strict ("after"), erring toward not merging unreviewed changes.
    comments = [
        {
            "user": {"login": "bot"},
            "body": "<!-- ok -->",
            "created_at": _HEAD_COMMITTED.isoformat(),
        }
    ]
    assert _marker_approved(comments, ["bot"], "<!-- ok -->", _HEAD_COMMITTED) is False


def test_old_enough_true_for_old_commit():
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert _old_enough(old, 10) is True


def test_old_enough_false_for_recent_commit():
    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    assert _old_enough(recent, 10) is False


def test_old_enough_parses_zulu_suffix():
    old = "2000-01-01T00:00:00Z"
    assert _old_enough(old, 10) is True


def test_merge_pull_request_forwards_merge_method():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.json.return_value = {"merged": True}
    with patch.object(repo, "_put", return_value=response) as mock_put:
        repo.merge_pull_request(7)
        # No sha given: the body omits it so GitHub does not pin the merge.
        assert mock_put.call_args.kwargs["json"] == {"merge_method": "merge"}
        repo.merge_pull_request(7, MergeMethod.SQUASH)
        assert mock_put.call_args.kwargs["json"] == {"merge_method": "squash"}
        repo.merge_pull_request(7, MergeMethod.SQUASH, sha="abc123")
        assert mock_put.call_args.kwargs["json"] == {
            "merge_method": "squash",
            "sha": "abc123",
        }


def test_head_commit_date_uses_list_endpoint_without_files():
    repo = Repository("token", "owner/name")
    response = MagicMock()
    response.json.return_value = [
        {"commit": {"committer": {"date": "2026-06-27T00:00:00Z"}}}
    ]
    with patch.object(repo, "_get", return_value=response) as mock_get:
        date = repo._head_commit_date("abc123")
    assert date == "2026-06-27T00:00:00Z"
    # The list-commits endpoint (not /commits/{sha}) is used so no file diff is
    # downloaded, limited to the single head commit.
    assert mock_get.call_args.kwargs["url"] == (
        "https://api.github.com/repos/owner/name/commits"
    )
    assert mock_get.call_args.kwargs["params"] == {"sha": "abc123", "per_page": 1}


def _auto_merge_repo(pr_overrides=None, *, reviews=None, comments=None, commit_age=30):
    """Build a Repository with all should_auto_merge dependencies patched to pass.

    Individual tests override one field to exercise a single failing gate.
    """
    repo = Repository("token", "owner/name")
    pr = {
        "number": 1,
        "draft": False,
        "user": {"login": "bot"},
        "title": "chore: bump",
        "mergeable_state": "clean",
        "head": {"sha": "abc123"},
    }
    pr.update(pr_overrides or {})
    committed = (datetime.now(timezone.utc) - timedelta(minutes=commit_age)).isoformat()
    patches = [
        patch.object(repo, "get_pull_request", return_value=pr),
        patch.object(repo, "_head_commit_date", return_value=committed),
        patch.object(
            repo,
            "get_pull_request_reviews",
            return_value=[_review("bot", "APPROVED")] if reviews is None else reviews,
        ),
        patch.object(
            repo, "get_issue_comments", return_value=comments if comments else []
        ),
    ]
    return repo, patches


def _run_should_auto_merge(repo, patches, **kwargs):
    params = {"authors": ["bot"], "approvers": ["bot"]}
    params.update(kwargs)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return repo.should_auto_merge(1, **params)


def test_should_auto_merge_all_pass():
    repo, patches = _auto_merge_repo()
    # On eligibility, the validated head SHA is returned (head.sha in _auto_merge_repo).
    assert _run_should_auto_merge(repo, patches) == "abc123"


def test_should_auto_merge_accepts_bare_string_allowlists():
    """A bare-string allowlist is one login, not one entry per character.

    Without coercion the allowlist would become a set of characters, which both
    rejects the intended login and accepts any single-character one.
    """
    repo, patches = _auto_merge_repo()
    assert (
        _run_should_auto_merge(repo, patches, authors="bot", approvers="bot")
        == "abc123"
    )


def test_should_auto_merge_bare_string_authors_rejects_single_char_login():
    """Uncoerced, `authors="bot"` is the character set {b, o, t}, which admits `b`.

    The approver allowlist is a list here so the author gate is the only one that
    can fail.
    """
    repo, patches = _auto_merge_repo(
        pr_overrides={"user": {"login": "b"}},
        reviews=[_review("b", "APPROVED")],
    )
    assert _run_should_auto_merge(repo, patches, authors="bot", approvers=["b"]) is None


def test_should_auto_merge_bare_string_approvers_rejects_single_char_login():
    """Uncoerced, `approvers="bot"` would let `b` grant an approving review.

    The author allowlist is a list here so the PR reaches the approver gate.
    """
    repo, patches = _auto_merge_repo(reviews=[_review("b", "APPROVED")])
    assert (
        _run_should_auto_merge(repo, patches, authors=["bot"], approvers="bot") is None
    )


def test_should_auto_merge_marker_comment_path():
    repo, patches = _auto_merge_repo(
        reviews=[],
        comments=[
            {
                "user": {"login": "bot"},
                "body": "AUTO_MERGE_APPROVED",
                # Posted after the head commit (which _auto_merge_repo dates 30 min ago).
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    # On eligibility, the validated head SHA is returned (head.sha in _auto_merge_repo).
    assert _run_should_auto_merge(repo, patches) == "abc123"


def test_should_auto_merge_skips_marker_comment_before_head_commit():
    # A marker left before the current head commit approved an older revision and
    # must not auto-merge the newer, unreviewed changes.
    repo, patches = _auto_merge_repo(
        reviews=[],
        comments=[
            {
                "user": {"login": "bot"},
                "body": "AUTO_MERGE_APPROVED",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }
        ],
    )
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_draft():
    repo, patches = _auto_merge_repo({"draft": True})
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_untrusted_author():
    repo, patches = _auto_merge_repo({"user": {"login": "stranger"}})
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_disallowed_title_type():
    repo, patches = _auto_merge_repo({"title": "feat: shiny"})
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_unclean_state():
    repo, patches = _auto_merge_repo({"mergeable_state": "blocked"})
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_too_new():
    repo, patches = _auto_merge_repo(commit_age=0)
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_without_approval():
    repo, patches = _auto_merge_repo(reviews=[], comments=[])
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_skips_when_changes_requested():
    repo, patches = _auto_merge_repo(reviews=[_review("bot", "CHANGES_REQUESTED")])
    assert _run_should_auto_merge(repo, patches) is None


def test_should_auto_merge_changes_requested_vetoes_marker_comment():
    # A standing change request must block the merge even when an approver also
    # left an approving marker comment.
    repo, patches = _auto_merge_repo(
        reviews=[_review("bot", "CHANGES_REQUESTED")],
        comments=[{"user": {"login": "bot"}, "body": "AUTO_MERGE_APPROVED"}],
    )
    assert _run_should_auto_merge(repo, patches) is None


def test_settle_mergeable_state_retries_until_settled():
    # GitHub returns "unknown" until it finishes computing mergeable_state; the
    # PR is re-fetched (sleeping between tries) until the state settles.
    repo = Repository("token", "owner/name")
    pr = {"number": 1, "mergeable_state": "unknown"}
    fetched = [
        {"number": 1, "mergeable_state": "unknown"},
        {"number": 1, "mergeable_state": "clean"},
    ]
    with (
        patch("github_rest_api.github.time.sleep") as mock_sleep,
        patch.object(repo, "get_pull_request", side_effect=fetched) as mock_get,
    ):
        settled = repo._settle_mergeable_state(pr)
    assert settled["mergeable_state"] == "clean"
    assert mock_get.call_count == 2
    assert mock_sleep.call_count == 2


def test_settle_mergeable_state_returns_immediately_when_settled():
    # An already-settled state needs no re-fetch and no sleep.
    repo = Repository("token", "owner/name")
    pr = {"number": 1, "mergeable_state": "clean"}
    with (
        patch("github_rest_api.github.time.sleep") as mock_sleep,
        patch.object(repo, "get_pull_request") as mock_get,
    ):
        settled = repo._settle_mergeable_state(pr)
    assert settled is pr
    mock_get.assert_not_called()
    mock_sleep.assert_not_called()


def test_settle_mergeable_state_gives_up_after_attempts():
    # When the state never settles, it gives up after ``attempts`` fetches and
    # returns the last (still-unknown) PR rather than looping forever.
    repo = Repository("token", "owner/name")
    pr = {"number": 1, "mergeable_state": "unknown"}
    with (
        patch("github_rest_api.github.time.sleep") as mock_sleep,
        patch.object(repo, "get_pull_request", return_value=dict(pr)) as mock_get,
    ):
        settled = repo._settle_mergeable_state(pr, attempts=3)
    assert settled["mergeable_state"] == "unknown"
    assert mock_get.call_count == 3
    assert mock_sleep.call_count == 3


def _pr_item(number):
    """A list-endpoint PR payload that passes the field-only pre-filter gate."""
    return {
        "number": number,
        "draft": False,
        "user": {"login": "bot"},
        "title": "chore: bump",
    }


def test_auto_merge_pull_requests_merges_only_eligible():
    repo = Repository("token", "owner/name")
    with (
        patch.object(
            repo, "get_pull_requests", return_value=[_pr_item(1), _pr_item(2)]
        ),
        # should_auto_merge returns the validated SHA for #1, None for #2.
        patch.object(repo, "should_auto_merge", side_effect=["sha1", None]),
        patch.object(repo, "merge_pull_request") as mock_merge,
    ):
        merged = repo.auto_merge_pull_requests(
            authors=["bot"], approvers=["bot"], merge_method=MergeMethod.SQUASH
        )
    assert merged == [1]
    # The merge is pinned to the SHA the gate validated.
    mock_merge.assert_called_once_with(1, MergeMethod.SQUASH, sha="sha1")


def test_auto_merge_pull_requests_prefilters_without_fetching_detail():
    repo = Repository("token", "owner/name")
    # PR #1 fails the field gate (untrusted author); PR #2 passes it.
    item_1 = {"number": 1, "draft": False, "user": {"login": "x"}, "title": "chore: a"}
    with (
        patch.object(repo, "get_pull_requests", return_value=[item_1, _pr_item(2)]),
        patch.object(repo, "should_auto_merge", return_value="sha2") as mock_eval,
        patch.object(repo, "merge_pull_request") as mock_merge,
    ):
        merged = repo.auto_merge_pull_requests(authors=["bot"], approvers=["bot"])
    # PR #1 is dropped by the pre-filter, so should_auto_merge is only called for #2.
    assert merged == [2]
    mock_eval.assert_called_once()
    assert mock_eval.call_args.args == (2,)
    mock_merge.assert_called_once_with(2, MergeMethod.MERGE, sha="sha2")


def test_auto_merge_pull_requests_dry_run_merges_nothing():
    repo = Repository("token", "owner/name")
    with (
        patch.object(
            repo, "get_pull_requests", return_value=[_pr_item(1), _pr_item(2)]
        ),
        patch.object(repo, "should_auto_merge", return_value="sha"),
        patch.object(repo, "merge_pull_request") as mock_merge,
    ):
        merged = repo.auto_merge_pull_requests(
            authors=["bot"], approvers=["bot"], dry_run=True
        )
    assert merged == [1, 2]
    mock_merge.assert_not_called()


def test_auto_merge_pull_requests_continues_after_merge_failure():
    repo = Repository("token", "owner/name")
    with (
        patch.object(
            repo, "get_pull_requests", return_value=[_pr_item(1), _pr_item(2)]
        ),
        patch.object(repo, "should_auto_merge", return_value="sha"),
        patch.object(
            repo,
            "merge_pull_request",
            side_effect=[requests.HTTPError("409 conflict"), {"merged": True}],
        ) as mock_merge,
    ):
        merged = repo.auto_merge_pull_requests(authors=["bot"], approvers=["bot"])
    # PR #1 fails to merge but the batch continues; only PR #2 is reported merged.
    assert merged == [2]
    assert mock_merge.call_count == 2


def test_auto_merge_pull_requests_continues_after_evaluation_failure():
    repo = Repository("token", "owner/name")
    with (
        patch.object(
            repo, "get_pull_requests", return_value=[_pr_item(1), _pr_item(2)]
        ),
        patch.object(
            repo,
            "should_auto_merge",
            side_effect=[requests.HTTPError("404 head gone"), "sha2"],
        ),
        patch.object(repo, "merge_pull_request") as mock_merge,
    ):
        merged = repo.auto_merge_pull_requests(authors=["bot"], approvers=["bot"])
    # Evaluating PR #1 raises (e.g. its head was force-pushed); the batch still
    # evaluates and merges PR #2.
    assert merged == [2]
    mock_merge.assert_called_once_with(2, MergeMethod.MERGE, sha="sha2")
