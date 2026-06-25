import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from github_rest_api.pr_content import (
    deterministic_body,
    deterministic_title,
    generate_pr_content,
    parse_conventional,
    summarize_for_ai,
)


def _commit(sha: str, message: str) -> dict:
    return {"sha": sha, "commit": {"message": message}}


def _file(filename: str, status: str = "modified", add: int = 1, dele: int = 0) -> dict:
    return {
        "filename": filename,
        "status": status,
        "additions": add,
        "deletions": dele,
        "patch": f"@@ patch for {filename} @@",
    }


COMPARE = {
    "commits": [
        _commit("abc1234def", "feat(github): add compare method\n\nDetails."),
        _commit("def5678abc", "test(github): cover pr content"),
    ],
    "files": [
        _file("github_rest_api/github.py", "modified", 42, 3),
        _file("github_rest_api/pr_content.py", "added", 100, 0),
    ],
}


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("feat: add x", ("feat", None, False, "add x")),
        ("fix(api): bug", ("fix", "api", False, "bug")),
        ("refactor!: drop y", ("refactor", None, True, "drop y")),
        ("feat(core)!: z", ("feat", "core", True, "z")),
    ],
)
def test_parse_conventional_valid(subject, expected):
    assert parse_conventional(subject) == expected


@pytest.mark.parametrize(
    "subject",
    ["just a message", "Merge branch main", "feature: missing s", "feat:no space"],
)
def test_parse_conventional_invalid(subject):
    assert parse_conventional(subject) is None


def test_deterministic_title_feat_precedence():
    compare = {
        "commits": [
            _commit("1", "fix: a bug"),
            _commit("2", "feat: a feature"),
            _commit("3", "docs: a doc"),
        ],
        "files": [_file("pkg/a.py")],
    }
    # feat outranks fix/docs; scope comes from the common top-level directory.
    assert deterministic_title(compare) == "feat(pkg): a feature"


def test_deterministic_title_single_commit_reuses_description():
    compare = {
        "commits": [_commit("1", "fix(github): handle 422")],
        "files": [_file("github_rest_api/github.py")],
    }
    assert deterministic_title(compare) == "fix(github_rest_api): handle 422"


def test_deterministic_title_breaking_bang():
    compare = {
        "commits": [_commit("1", "feat!: drop old api")],
        "files": [_file("a.py")],
    }
    assert deterministic_title(compare) == "feat!: drop old api"


@pytest.mark.parametrize("token", ["BREAKING CHANGE", "BREAKING-CHANGE"])
def test_deterministic_title_breaking_footer(token):
    compare = {
        "commits": [_commit("1", f"feat: new api\n\n{token}: removed old")],
        "files": [_file("a.py")],
    }
    assert deterministic_title(compare) == "feat!: new api"


def test_deterministic_title_skips_merge_commits():
    compare = {
        "commits": [
            _commit("1", "Merge branch 'main' into dev"),
            _commit("2", "chore: bump version"),
        ],
        "files": [_file("a.py")],
    }
    assert deterministic_title(compare) == "chore: bump version"


def test_deterministic_title_no_commits():
    assert deterministic_title({"commits": [], "files": []}) == "chore: update"


def test_deterministic_title_non_conventional_single():
    compare = {"commits": [_commit("1", "did some work")], "files": [_file("a.py")]}
    assert deterministic_title(compare) == "chore: did some work"


def test_deterministic_title_no_common_scope_across_dirs():
    compare = {
        "commits": [_commit("1", "feat: x")],
        "files": [_file("a/x.py"), _file("b/y.py")],
    }
    # Files in different top-level dirs share no scope.
    assert deterministic_title(compare) == "feat: x"


def test_deterministic_title_no_scope_for_root_files():
    compare = {
        "commits": [_commit("1", "chore: bump")],
        "files": [_file("pyproject.toml")],
    }
    # A repository-root file (no slash) yields no scope.
    assert deterministic_title(compare) == "chore: bump"


def test_deterministic_body_sections():
    body = deterministic_body(COMPARE)
    assert "## Summary" in body
    assert "## Changed files" in body
    assert "## Commits" in body
    assert "- feat(github): add compare method" in body
    assert "**Added**" in body
    assert "`github_rest_api/pr_content.py` (+100/-0)" in body
    assert "abc1234 feat(github): add compare method" in body


def test_summarize_for_ai_truncates():
    big = {
        "commits": [_commit("1", "feat: big")],
        "files": [{"filename": "a.py", "status": "modified", "patch": "x" * 1000}],
    }
    summary = summarize_for_ai(big, max_chars=200)
    assert "size limit reached" in summary
    assert len(summary) < 1000


def test_summarize_for_ai_includes_patch_within_budget():
    summary = summarize_for_ai(COMPARE, max_chars=12000)
    assert "@@ patch for github_rest_api/github.py @@" in summary


def test_generate_pr_content_parses_reply():
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"title": "feat: x", "body": "## Summary\n- x"}
                    )
                }
            }
        ]
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        title, body = generate_pr_content(
            COMPARE, model="anthropic/claude-haiku-4-5-20251001"
        )
    assert title == "feat: x"
    assert body == "## Summary\n- x"
    fake_litellm.completion.assert_called_once()
    # JSON mode is requested, with unsupported params dropped per provider.
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["drop_params"] is True


def _generate_with_reply(content: str) -> tuple[str, str]:
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        return generate_pr_content(COMPARE, model="anthropic/claude-haiku-4-5-20251001")


def test_generate_pr_content_empty_raises():
    with pytest.raises(ValueError):
        _generate_with_reply(json.dumps({"title": "", "body": ""}))


def test_generate_pr_content_missing_key_raises():
    with pytest.raises(ValueError):
        _generate_with_reply(json.dumps({"title": "feat: x"}))


def test_generate_pr_content_non_dict_raises():
    with pytest.raises(ValueError):
        _generate_with_reply(json.dumps(["not", "a", "dict"]))
