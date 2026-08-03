import json
import logging
import subprocess as sp
import sys
from unittest.mock import MagicMock, patch

import pytest

from github_rest_api import IssueState
from github_rest_api.scripts.github import upload_image_to_issue as module
from github_rest_api.scripts.github.upload_image_to_issue import (
    build_cld_command,
    cloudinary_folder,
    extract_secure_url,
    issue_choices,
    parse_args,
    parse_fzf_issue,
    parse_fzf_repo,
    parse_tags,
)

URL = "https://res.cloudinary.com/demo/image/upload/v1/tasks/a.png"


def test_parse_tags_splits_on_commas():
    assert parse_tags(["a,b,c"]) == ["a", "b", "c"]


def test_parse_tags_keeps_space_delimited_values():
    assert parse_tags(["a", "b", "c"]) == ["a", "b", "c"]


def test_parse_tags_mixes_both_delimiters():
    assert parse_tags(["a, b", "c"]) == ["a", "b", "c"]


def test_parse_tags_accepts_a_bare_string():
    """`as_str_sequence` keeps a bare string from exploding into characters."""
    assert parse_tags("a,b") == ["a", "b"]


def test_parse_tags_drops_empty_parts_and_duplicates():
    assert parse_tags(["a,,b", " ", "b", "a"]) == ["a", "b"]


def test_parse_tags_keeps_spaces_inside_a_tag():
    """A quoted value is one tag, so that 'my son' survives as written."""
    assert parse_tags(["my son", "beach"]) == ["my son", "beach"]


def test_parse_tags_splits_a_comma_separated_tag_with_spaces():
    assert parse_tags(["my son, my daughter"]) == ["my son", "my daughter"]


def test_parse_tags_of_nothing_is_empty():
    assert parse_tags([]) == []


CLASSIC_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FINE_GRAINED_TOKEN = "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1" + "_" + "z" * 59


@pytest.mark.parametrize(
    "value",
    [
        CLASSIC_TOKEN,
        FINE_GRAINED_TOKEN,
        CLASSIC_TOKEN.replace("ghp_", "gho_"),
        CLASSIC_TOKEN.replace("ghp_", "ghu_"),
        CLASSIC_TOKEN.replace("ghp_", "ghs_"),
        CLASSIC_TOKEN.replace("ghp_", "ghr_"),
        f"my token is {CLASSIC_TOKEN} ok",
    ],
)
def test_reject_github_token_catches_every_token_format(value):
    with pytest.raises(ValueError, match="looks like a GitHub token"):
        module.reject_github_token("--body", value)


def test_reject_github_token_keeps_the_value_out_of_the_message():
    """The message reaches stderr and logs, so it must not carry the secret."""
    with pytest.raises(ValueError) as excinfo:
        module.reject_github_token("--body", CLASSIC_TOKEN)
    assert CLASSIC_TOKEN not in str(excinfo.value)
    assert "--body" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a normal issue title",
        "ghp_short",
        "see github_pat for details",
        "https://res.cloudinary.com/demo/image/upload/v1/tasks/a.png",
        "fix the ghp_ prefix handling",
        # A legacy 40-hex PAT is indistinguishable from a commit SHA, and
        # SHAs are common in issue bodies, so that format is deliberately
        # not covered.
        "regressed in 8007e96f5537e85d23fadc389f353e2b4a96614a",
    ],
)
def test_reject_github_token_allows_ordinary_values(value):
    module.reject_github_token("--body", value)


def test_reject_github_token_catches_a_token_glued_to_a_word():
    """No leading word boundary, so an embedded token is still caught."""
    with pytest.raises(ValueError, match="looks like a GitHub token"):
        module.reject_github_token("--body", f"MY_{CLASSIC_TOKEN}")


def test_cloudinary_folder_uses_the_repository_name():
    assert cloudinary_folder("dclong/tasks") == "tasks"
    assert cloudinary_folder("legendu-net/blog") == "blog"


def test_issue_choices_drops_pull_requests():
    """GitHub's issues endpoint returns pull requests too."""
    issues = [
        {"number": 1, "title": "an issue"},
        {"number": 2, "title": "a PR", "pull_request": {"url": "..."}},
    ]
    assert issue_choices(issues) == ["1\tan issue"]


def test_issue_choices_squeezes_whitespace_in_titles():
    """A tab or newline in a title would otherwise break the record layout."""
    issues = [{"number": 3, "title": "a\tb\nc  d"}]
    assert issue_choices(issues) == ["3\ta b c d"]


def test_issue_choices_of_nothing_is_empty():
    assert issue_choices([]) == []


def test_parse_fzf_repo_prefers_the_selection():
    assert parse_fzf_repo(0, "das\ndclong/tasks\n") == "dclong/tasks"


def test_parse_fzf_repo_falls_back_to_the_query_when_nothing_matched():
    assert parse_fzf_repo(1, "owner/other\n") == "owner/other"


def test_parse_fzf_repo_handles_an_empty_query_line():
    """Nothing typed, an entry highlighted: the query line is empty."""
    assert parse_fzf_repo(0, "\ndclong/tasks\n") == "dclong/tasks"


def test_parse_fzf_repo_rejects_a_cancelled_picker():
    with pytest.raises(ValueError, match="cancelled"):
        parse_fzf_repo(130, "")


def test_parse_fzf_repo_rejects_a_blank_query():
    with pytest.raises(ValueError, match="No repository"):
        parse_fzf_repo(1, "\n")


def test_parse_fzf_issue_takes_the_leading_number():
    assert parse_fzf_issue(0, "12\ta title\n") == 12


def test_parse_fzf_issue_rejects_a_cancelled_picker():
    with pytest.raises(ValueError, match="cancelled"):
        parse_fzf_issue(130, "")


def test_parse_fzf_issue_rejects_empty_output():
    with pytest.raises(ValueError, match="No issue"):
        parse_fzf_issue(0, "")


def test_parse_fzf_issue_rejects_a_non_numeric_field():
    with pytest.raises(ValueError, match="Could not parse"):
        parse_fzf_issue(0, "oops\ta title")


def test_extract_secure_url_parses_json():
    assert extract_secure_url(json.dumps({"secure_url": URL})) == URL


def test_extract_secure_url_strips_ansi_colors():
    """Older versions of cld colorize the JSON even when it is captured."""
    assert extract_secure_url(f'\x1b[32m{{"secure_url": "{URL}"}}\x1b[0m') == URL


def test_extract_secure_url_ignores_a_leading_log_line():
    stdout = f'uploading...\n{{"secure_url": "{URL}"}}'
    assert extract_secure_url(stdout) == URL


def test_extract_secure_url_scrapes_the_field_out_of_broken_json():
    """A non-Cloudinary host keeps this off the CLOUDINARY_URL fallback."""
    assert (
        extract_secure_url('{"secure_url": "https://h.test/a.png", truncated')
        == "https://h.test/a.png"
    )


def test_extract_secure_url_scrapes_a_wrapped_response():
    """Valid JSON without a top-level secure_url must not end the search."""
    assert extract_secure_url(json.dumps({"result": {"secure_url": URL}})) == URL


def test_extract_secure_url_falls_back_to_any_cloudinary_url():
    assert extract_secure_url(f"Uploaded to {URL}") == URL


def test_extract_secure_url_rejects_a_response_without_the_field():
    with pytest.raises(ValueError, match="Could not find"):
        extract_secure_url(json.dumps({"error": "nope"}))


def test_extract_secure_url_rejects_empty_output():
    with pytest.raises(ValueError, match="no output"):
        extract_secure_url("   ")


def test_extract_secure_url_rejects_unparseable_output():
    with pytest.raises(ValueError, match="Could not find"):
        extract_secure_url("something went wrong")


def test_build_cld_command_passes_folder_and_tags(tmp_path):
    image = tmp_path / "a.png"
    assert build_cld_command(image, "tasks", ["x", "y"]) == [
        "cld",
        "uploader",
        "upload",
        str(image),
        "folder=tasks",
        "tags=x,y",
    ]


def test_build_cld_command_omits_tags_when_there_are_none(tmp_path):
    assert "tags=" not in " ".join(build_cld_command(tmp_path / "a.png", "tasks", []))


def test_build_cld_command_does_not_quote_tags(tmp_path):
    """There is no shell involved, so a quote would become part of a tag."""
    cmd = build_cld_command(tmp_path / "a.png", "tasks", ["x", "y"])
    assert '"' not in "".join(cmd)


def test_parse_args_requires_an_image():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_rejects_two_image_sources():
    with pytest.raises(SystemExit):
        parse_args(["--image-path", "a.png", "--image-url", URL])


def test_parse_args_rejects_two_issue_sources():
    with pytest.raises(SystemExit):
        parse_args(["--image-url", URL, "--issue-number", "1", "--issue-title", "t"])


def test_parse_args_defaults_to_no_issue():
    args = parse_args(["--image-url", URL])
    assert args.issue_number == 0
    assert args.issue_title is None


def test_parse_args_accepts_short_options():
    args = parse_args(
        ["-r", "o/n", "-n", "7", "-u", URL, "-g", "x,y", "-a", "A", "-b", "B"]
    )
    assert (args.repo, args.issue_number) == ("o/n", 7)
    assert (args.image_url, args.tags, args.alt, args.body) == (URL, ["x,y"], "A", "B")


@pytest.mark.parametrize("option", ["-p", "--image-path", "--image"])
def test_parse_args_accepts_every_spelling_of_the_image_path(option):
    """--image stays usable, and being explicit keeps it from being ambiguous."""
    assert parse_args([option, "a.png"]).image_path == "a.png"


def test_parse_args_short_issue_title():
    args = parse_args(["-T", "a title", "-p", "a.png"])
    assert args.issue_title == "a title"
    assert args.image_path == "a.png"


def test_parse_args_has_no_short_option_for_the_token():
    """-t next to -T would let a case slip post the token as an issue title."""
    with pytest.raises(SystemExit):
        parse_args(["-t", "tk", "-u", URL])
    assert parse_args(["--token", "tk", "-u", URL]).token == "tk"


def test_select_repo_allows_free_text_entry():
    """--print-query is what makes a repository outside the list reachable."""
    proc = sp.CompletedProcess([], returncode=1, stdout="owner/other\n", stderr="")
    with patch.object(module, "_run_fzf", return_value=proc) as mock:
        assert module.select_repo() == "owner/other"
    options = mock.call_args.args[1:]
    assert mock.call_args.args[0] == module.POPULAR_REPOS
    assert "--print-query" in options
    assert "--exact" in options


def test_select_issue_offers_open_issues_only_and_forbids_free_text():
    repository = MagicMock()
    repository.get_issues.return_value = [
        {"number": 5, "title": "an issue"},
        {"number": 6, "title": "a PR", "pull_request": {}},
    ]
    proc = sp.CompletedProcess([], returncode=0, stdout="5\tan issue\n", stderr="")
    with patch.object(module, "_run_fzf", return_value=proc) as mock:
        assert module.select_issue(repository) == 5
    assert repository.get_issues.call_args.kwargs == {"state": IssueState.OPEN}
    assert mock.call_args.args[0] == ["5\tan issue"]
    options = mock.call_args.args[1:]
    assert "--bind=enter:accept-non-empty" in options
    assert "--no-print-query" in options


def test_select_issue_reports_when_there_is_nothing_to_select():
    """Better than dropping the user into an empty picker."""
    repository = MagicMock()
    repository.get_issues.return_value = []
    with patch.object(module, "_run_fzf") as mock:
        with pytest.raises(ValueError, match="no open issue"):
            module.select_issue(repository)
    mock.assert_not_called()


def test_upload_image_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        module.upload_image(tmp_path / "nope.png", "tasks", [])


def test_upload_image_reports_a_missing_cld(tmp_path):
    image = tmp_path / "a.png"
    image.touch()
    with patch.object(module.shutil, "which", return_value=None):
        with pytest.raises(FileNotFoundError, match="Cloudinary CLI"):
            module.upload_image(image, "tasks", [])


def test_upload_image_surfaces_stderr_on_failure(tmp_path):
    """`check=True` would swallow the message Cloudinary actually sent."""
    image = tmp_path / "a.png"
    image.touch()
    proc = sp.CompletedProcess([], returncode=1, stdout="", stderr="invalid api key")
    with patch.object(module.shutil, "which", return_value="/usr/bin/cld"):
        with patch.object(module.sp, "run", return_value=proc):
            with pytest.raises(RuntimeError, match="invalid api key"):
                module.upload_image(image, "tasks", [])


def test_upload_image_reports_the_upload(tmp_path, caplog):
    """The upload is the slow step, so it must not look like a hang."""
    image = tmp_path / "a.png"
    image.touch()
    proc = sp.CompletedProcess([], returncode=0, stdout=json.dumps({"secure_url": URL}))
    with patch.object(module.shutil, "which", return_value="/usr/bin/cld"):
        with patch.object(module.sp, "run", return_value=proc):
            with caplog.at_level(logging.INFO, logger=module.__name__):
                module.upload_image(image, "tasks", ["x", "y"])
    assert f"Uploading '{image}' to the Cloudinary folder 'tasks'" in caplog.text
    assert "with tags x, y" in caplog.text
    assert f"Uploaded the image to {URL}" in caplog.text


def _run_main(monkeypatch, argv, repository=None):
    """Drive `main` with a mocked `Repository` and return it with the exit code."""
    repository = repository or MagicMock()
    # Without a token in the environment, the resolution would fall through to
    # an interactive prompt and hang the test run.
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(sys, "argv", ["upload_image_to_issue", *argv])
    with patch.object(module, "Repository", return_value=repository):
        code = module.main()
    return repository, code


def test_main_comments_on_an_existing_issue(monkeypatch, capsys):
    repository, code = _run_main(
        monkeypatch,
        ["--repo", "dclong/tasks", "--issue-number", "7", "--image-url", URL],
    )
    assert code == 0
    repository.create_issue_comment_image.assert_called_once()
    assert repository.create_issue_comment_image.call_args.args == (7, URL)
    repository.create_issue.assert_not_called()
    # The bare number, so that it can be captured and reused.
    assert capsys.readouterr().out == "7\n"


def test_main_creates_an_issue_and_prints_its_number(monkeypatch, capsys):
    repository = MagicMock()
    repository.create_issue.return_value = {"number": 42}
    _, code = _run_main(
        monkeypatch,
        ["--repo", "dclong/tasks", "--issue-title", "a title", "--image-url", URL],
        repository,
    )
    assert code == 0
    assert repository.create_issue.call_args.kwargs == {"title": "a title"}
    assert repository.create_issue_comment_image.call_args.args == (42, URL)
    assert capsys.readouterr().out == "42\n"


def test_main_selects_a_repository_when_none_is_given(monkeypatch):
    with patch.object(module, "select_repo", return_value="legendu-net/blog") as mock:
        repository, code = _run_main(
            monkeypatch, ["--issue-number", "7", "--image-url", URL]
        )
    assert code == 0
    mock.assert_called_once()
    assert repository.create_issue_comment_image.call_args.args == (7, URL)


def test_main_selects_an_issue_when_none_is_given(monkeypatch):
    with patch.object(module, "select_issue", return_value=9) as mock:
        repository, code = _run_main(
            monkeypatch, ["--repo", "dclong/tasks", "--image-url", URL]
        )
    assert code == 0
    mock.assert_called_once()
    assert repository.create_issue_comment_image.call_args.args == (9, URL)


def test_main_uploads_a_local_image_into_the_repository_folder(monkeypatch):
    with patch.object(module, "upload_image", return_value=URL) as mock:
        repository, code = _run_main(
            monkeypatch,
            [
                "--repo",
                "legendu-net/blog",
                "--issue-number",
                "7",
                "--image-path",
                "/tmp/a.png",
                "--tags",
                "x,y",
                "z",
            ],
        )
    assert code == 0
    assert mock.call_args.kwargs == {"folder": "blog", "tags": ["x", "y", "z"]}
    assert repository.create_issue_comment_image.call_args.args == (7, URL)


def test_main_defaults_alt_to_the_image_name(monkeypatch):
    with patch.object(module, "upload_image", return_value=URL):
        repository, _ = _run_main(
            monkeypatch,
            [
                "--repo",
                "dclong/tasks",
                "--issue-number",
                "7",
                "--image-path",
                "/tmp/shot.png",
            ],
        )
    assert repository.create_issue_comment_image.call_args.kwargs["alt"] == "shot.png"


def test_main_does_not_create_an_issue_when_the_upload_fails(monkeypatch, capsys):
    """A failed upload must not leave an empty issue behind."""
    with patch.object(module, "upload_image", side_effect=RuntimeError("upload boom")):
        repository, code = _run_main(
            monkeypatch,
            [
                "--repo",
                "dclong/tasks",
                "--issue-title",
                "a title",
                "--image-path",
                "/tmp/a.png",
            ],
        )
    assert code == 1
    repository.create_issue.assert_not_called()
    repository.create_issue_comment_image.assert_not_called()
    assert "upload boom" in capsys.readouterr().err


def test_main_reports_a_failure_on_stderr(monkeypatch, capsys):
    with patch.object(module, "select_issue", side_effect=ValueError("no open issue")):
        _, code = _run_main(monkeypatch, ["--repo", "dclong/tasks", "--image-url", URL])
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no open issue" in captured.err


def test_main_reports_the_issue_number_when_commenting_fails(monkeypatch, capsys):
    """A retry must be able to reuse the issue instead of creating a second one."""
    repository = MagicMock()
    repository.create_issue.return_value = {"number": 42}
    repository.create_issue_comment_image.side_effect = RuntimeError("502")
    _, code = _run_main(
        monkeypatch,
        ["--repo", "dclong/tasks", "--issue-title", "a title", "--image-url", URL],
        repository,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "Created issue #42" in err
    assert "--issue-number 42" in err
    # The original cause must survive into the message, not be swallowed.
    assert "502" in err


def test_main_does_not_claim_to_have_created_an_existing_issue(monkeypatch, capsys):
    """The failure of a comment on --issue-number must not be reported as a creation."""
    repository = MagicMock()
    repository.create_issue_comment_image.side_effect = RuntimeError("403")
    _, code = _run_main(
        monkeypatch,
        ["--repo", "dclong/tasks", "--issue-number", "7", "--image-url", URL],
        repository,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "403" in err
    assert "Created issue" not in err


def test_main_reports_the_created_issue(monkeypatch, caplog):
    """Progress goes to the log, so that stdout stays the issue number alone."""
    repository = MagicMock()
    repository.create_issue.return_value = {
        "number": 42,
        "html_url": "https://github.com/dclong/tasks/issues/42",
    }
    with caplog.at_level(logging.INFO, logger=module.__name__):
        _run_main(
            monkeypatch,
            ["--repo", "dclong/tasks", "--issue-title", "a title", "--image-url", URL],
            repository,
        )
    assert "Creating issue 'a title' in dclong/tasks" in caplog.text
    assert "Created issue #42: https://github.com/dclong/tasks/issues/42" in caplog.text


def test_main_reports_a_created_issue_without_a_link(monkeypatch, caplog):
    """A response without an html_url must not log a message that looks cut off."""
    repository = MagicMock()
    repository.create_issue.return_value = {"number": 42}
    with caplog.at_level(logging.INFO, logger=module.__name__):
        _run_main(
            monkeypatch,
            ["--repo", "dclong/tasks", "--issue-title", "a title", "--image-url", URL],
            repository,
        )
    assert "Created issue #42." in caplog.text


def test_main_keeps_the_log_off_stdout(monkeypatch):
    """Stdout carries the issue number alone, so the progress has to go elsewhere.

    Asserting on captured stdout cannot catch this: under pytest the root
    logger already has handlers, which makes `basicConfig` a no-op.
    """
    with patch.object(module.logging, "basicConfig") as mock:
        _run_main(
            monkeypatch,
            ["--repo", "dclong/tasks", "--issue-number", "7", "--image-url", URL],
        )
    # Neither a stream nor a handler, which is a `StreamHandler` on stderr.
    assert mock.call_args.kwargs == {"level": logging.INFO, "format": "%(message)s"}


def test_main_reports_the_posted_comment(monkeypatch, caplog):
    repository = MagicMock()
    repository.create_issue_comment_image.return_value = {
        "html_url": "https://github.com/dclong/tasks/issues/7#issuecomment-1"
    }
    with caplog.at_level(logging.INFO, logger=module.__name__):
        _run_main(
            monkeypatch,
            ["--repo", "dclong/tasks", "--issue-number", "7", "--image-url", URL],
            repository,
        )
    assert "Commenting on issue #7 of dclong/tasks" in caplog.text
    assert "issuecomment-1" in caplog.text


def test_main_does_not_upload_when_the_issue_picker_is_cancelled(monkeypatch):
    """Otherwise an image is left on Cloudinary that nothing ever references."""
    with patch.object(module, "select_issue", side_effect=ValueError("cancelled")):
        with patch.object(module, "upload_image") as mock:
            _, code = _run_main(
                monkeypatch, ["--repo", "dclong/tasks", "--image-path", "/tmp/a.png"]
            )
    assert code == 1
    mock.assert_not_called()


@pytest.mark.parametrize("image_url", ["", "   "])
def test_main_rejects_a_blank_image_url(monkeypatch, capsys, image_url):
    """An empty value satisfies the group but names no image."""
    repository, code = _run_main(
        monkeypatch,
        ["--repo", "dclong/tasks", "--issue-number", "7", "--image-url", image_url],
    )
    assert code == 1
    repository.create_issue_comment_image.assert_not_called()
    assert "must not be empty" in capsys.readouterr().err


def test_main_rejects_a_blank_issue_title(monkeypatch, capsys):
    """Otherwise this silently opens the issue picker instead."""
    with patch.object(module, "select_issue") as mock:
        repository, code = _run_main(
            monkeypatch,
            ["--repo", "dclong/tasks", "--issue-title", "", "--image-url", URL],
        )
    assert code == 1
    mock.assert_not_called()
    repository.create_issue.assert_not_called()
    assert "must not be empty" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--issue-title", CLASSIC_TOKEN, "--image-url", URL],
        ["--issue-number", "7", "--body", CLASSIC_TOKEN, "--image-url", URL],
        ["--issue-number", "7", "--alt", CLASSIC_TOKEN, "--image-url", URL],
        ["--issue-number", "7", "--image-path", CLASSIC_TOKEN],
        ["--issue-number", "7", "--image-url", CLASSIC_TOKEN],
        ["--issue-number", "7", "--tags", "ok", CLASSIC_TOKEN, "--image-url", URL],
    ],
)
def test_main_refuses_a_token_passed_to_another_option(monkeypatch, capsys, argv):
    """A token in any of these would be published where others can read it."""
    with patch.object(module, "upload_image") as upload:
        repository, code = _run_main(monkeypatch, ["--repo", "dclong/tasks", *argv])
    assert code == 1
    upload.assert_not_called()
    repository.create_issue.assert_not_called()
    repository.create_issue_comment_image.assert_not_called()
    err = capsys.readouterr().err
    assert "looks like a GitHub token" in err
    assert CLASSIC_TOKEN not in err


def test_main_refuses_a_token_in_the_repository(monkeypatch, capsys):
    """Caught before validate_repo, which would echo the value verbatim."""
    with patch.object(module, "validate_repo") as validate:
        _, code = _run_main(
            monkeypatch,
            ["--repo", CLASSIC_TOKEN, "--issue-number", "7", "--image-url", URL],
        )
    assert code == 1
    validate.assert_not_called()
    err = capsys.readouterr().err
    assert "looks like a GitHub token" in err
    assert CLASSIC_TOKEN not in err


def test_main_refuses_a_token_typed_into_the_repository_picker(monkeypatch, capsys):
    """The picker takes free text, so its result needs the check too."""
    with patch.object(module, "select_repo", return_value=f"me/{CLASSIC_TOKEN}"):
        with patch.object(module, "upload_image") as upload:
            repository, code = _run_main(
                monkeypatch, ["--issue-number", "7", "--image-url", URL]
            )
    assert code == 1
    upload.assert_not_called()
    repository.create_issue_comment_image.assert_not_called()
    err = capsys.readouterr().err
    assert "looks like a GitHub token" in err
    assert CLASSIC_TOKEN not in err


def test_parse_args_redacts_a_token_from_argparse_errors(capsys):
    """argparse quotes the bad value back, which would print the token."""
    with pytest.raises(SystemExit):
        parse_args(["-t", CLASSIC_TOKEN, "--image-url", URL])
    err = capsys.readouterr().err
    assert CLASSIC_TOKEN not in err
    assert "<redacted>" in err


def test_parse_args_redacts_a_token_from_a_bad_int_value(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--issue-number", CLASSIC_TOKEN, "--image-url", URL])
    err = capsys.readouterr().err
    assert CLASSIC_TOKEN not in err
    assert "<redacted>" in err


def test_main_rejects_a_malformed_repository(monkeypatch, capsys):
    repository, code = _run_main(
        monkeypatch, ["--repo", "tasks", "--issue-number", "7", "--image-url", URL]
    )
    assert code == 1
    repository.create_issue_comment_image.assert_not_called()
    assert "Invalid repo format" in capsys.readouterr().err
