import sys
from unittest.mock import MagicMock, patch

from github_rest_api.scripts.github import create_pull_request


def test_parse_args_defaults():
    args = create_pull_request.parse_args(
        ["--token", "t", "--head-branch", "dev", "--base-branch", "main"]
    )
    assert args.title == ""
    assert args.body == ""
    # No model by default, so generation is deterministic.
    assert args.model == ""


def _run_main(monkeypatch, extra_argv):
    argv = [
        "create_pull_request",
        "--token",
        "t",
        "--head-branch",
        "dev",
        "--base-branch",
        "main",
        *extra_argv,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    repo = MagicMock()
    with patch.object(create_pull_request, "Repository", return_value=repo):
        assert create_pull_request.main() == 0
    return repo.create_pull_request.call_args


def test_main_passes_title_and_body(monkeypatch):
    call = _run_main(monkeypatch, ["--title", "My Title", "--body", "My Body"])
    sent = call.args[0]
    assert sent["title"] == "My Title"
    assert sent["body"] == "My Body"


def test_main_without_options_defaults_to_deterministic(monkeypatch):
    call = _run_main(monkeypatch, [])
    sent = call.args[0]
    # No title/body are set here; create_pull_request generates them
    # deterministically (empty model).
    assert "title" not in sent
    assert "body" not in sent
    assert call.kwargs["model"] == ""


def test_main_with_model_uses_llm(monkeypatch):
    call = _run_main(monkeypatch, ["--model", "gemini/g"])
    assert call.kwargs["model"] == "gemini/g"
