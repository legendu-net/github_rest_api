from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from github_rest_api.scripts.lint_repo import (
    LANGUAGES,
    Check,
    _check_pep723_script_types,
    _has_pep723_header,
    _has_test_function,
    _missing_tool,
    build_bash_checks,
    build_checks,
    build_fish_checks,
    build_golang_checks,
    build_golang_test_checks,
    build_lua_checks,
    build_markdown_checks,
    build_python_project_checks,
    build_python_project_test_checks,
    build_python_scripts_checks,
    build_python_scripts_test_checks,
    build_rust_project_checks,
    build_rust_project_test_checks,
    build_rust_scripts_checks,
    build_test_checks,
    detect_languages,
    lint_repo,
    list_tracked_files,
    main,
    parse_args,
    run_check,
    run_checks,
)


def _init_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    porcelain.init(str(tmp_path))
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    porcelain.add(str(tmp_path), paths=list(files))
    return tmp_path


def test_list_tracked_files_returns_sorted_relative_paths(tmp_path):
    _init_repo(tmp_path, {"b.py": "", "a/c.py": "", "pyproject.toml": ""})
    assert list_tracked_files(tmp_path) == ["a/c.py", "b.py", "pyproject.toml"]


def test_list_tracked_files_ignores_untracked_files(tmp_path):
    _init_repo(tmp_path, {"tracked.py": ""})
    (tmp_path / "untracked.py").write_text("")
    assert list_tracked_files(tmp_path) == ["tracked.py"]


def test_list_tracked_files_survives_a_non_utf8_filename(tmp_path):
    # Git tracks arbitrary byte sequences as filenames; decoding with
    # `surrogateescape` must not raise on one that isn't valid UTF-8. Written
    # to the index directly: dulwich's own `porcelain.add` insists on
    # UTF-8-decoding the paths it's given, so it can't stage this itself.
    from dulwich.index import IndexEntry
    from dulwich.objects import ObjectID

    porcelain.init(str(tmp_path))
    raw_name = b"caf\xe9.py"
    with Repo(str(tmp_path)) as repo:
        index = repo.open_index()
        index[raw_name] = IndexEntry(
            ctime=0,
            mtime=0,
            dev=0,
            ino=0,
            mode=0o100644,
            uid=0,
            gid=0,
            size=0,
            sha=ObjectID(b"0" * 40),
        )
        index.write()
    files = list_tracked_files(tmp_path)
    assert len(files) == 1
    assert files[0].encode(errors="surrogateescape") == raw_name


def test_detect_languages_python_project_takes_precedence_over_scripts():
    files = ["pyproject.toml", "pkg/mod.py"]
    assert detect_languages(files)["python"] == "project"


def test_detect_languages_python_scripts_without_root_pyproject():
    files = ["scripts/build.py"]
    assert detect_languages(files)["python"] == "scripts"


def test_detect_languages_nested_pyproject_toml_is_not_a_project():
    # Only a *root* pyproject.toml counts as a project, matching lint.yaml's
    # `have pyproject.toml` pathspec check.
    files = ["subdir/pyproject.toml", "subdir/mod.py"]
    assert detect_languages(files)["python"] == "scripts"


def test_detect_languages_rust_project_vs_scripts():
    assert detect_languages(["Cargo.toml", "src/main.rs"])["rust"] == "project"
    assert detect_languages(["scripts/tool.rs"])["rust"] == "scripts"


def test_detect_languages_golang_requires_root_go_mod():
    assert detect_languages(["go.mod", "main.go"])["golang"] == "module"
    assert "golang" not in detect_languages(["sub/go.mod"])


def test_detect_languages_bash_fish_lua_markdown():
    files = ["a.sh", "b.fish", "c.lua", "d.md"]
    languages = detect_languages(files)
    assert languages["bash"] == "scripts"
    assert languages["fish"] == "scripts"
    assert languages["lua"] == "scripts"
    assert languages["markdown"] == "files"


def test_detect_languages_no_matches_omits_language():
    assert detect_languages(["README.rst"]) == {}


def test_build_bash_checks_empty_when_no_shell_scripts():
    assert build_bash_checks(["a.py"]) == []


def test_build_bash_checks_includes_shfmt_and_shellcheck():
    checks = build_bash_checks(["a.sh", "sub/b.sh"])
    names = [c.name for c in checks]
    assert "bash: shfmt" in names
    assert "bash: shellcheck" in names
    shfmt = next(c for c in checks if c.name == "bash: shfmt")
    assert shfmt.command == ["shfmt", "-i", "4", "-ci", "-d", "a.sh", "sub/b.sh"]
    assert shfmt.fatal


def test_build_fish_checks_runs_fish_n_per_file():
    # `fish -n a b` only checks `a`, so each file needs its own check.
    checks = build_fish_checks(["a.fish", "b.fish"])
    names = [c.name for c in checks]
    assert names == [
        "fish: fish_indent",
        "fish: fish -n (a.fish)",
        "fish: fish -n (b.fish)",
    ]
    assert checks[1].command == ["fish", "-n", "a.fish"]
    assert checks[2].command == ["fish", "-n", "b.fish"]


def test_build_rust_scripts_checks_empty_without_rs_files():
    assert build_rust_scripts_checks(["a.py"]) == []


def test_build_rust_scripts_checks_includes_all_files():
    checks = build_rust_scripts_checks(["b.rs", "a.rs"])
    assert len(checks) == 1
    assert checks[0].command == [
        "rustfmt",
        "--check",
        "--edition",
        "2024",
        "a.rs",
        "b.rs",
    ]


def test_build_markdown_checks_splits_skill_md(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SKILL.md").write_text("")
    (tmp_path / "README.md").write_text("")
    checks = build_markdown_checks(["README.md", "docs/SKILL.md"])
    by_name = {c.name: c for c in checks}
    assert by_name["markdown: mdformat"].command == ["mdformat", "--check", "README.md"]
    assert by_name["markdown: mdformat (SKILL.md)"].command == [
        "mdformat",
        "--check",
        "--number",
        "docs/SKILL.md",
    ]
    assert by_name["markdown: codespell"].command == [
        "codespell",
        "README.md",
        "docs/SKILL.md",
    ]


def test_build_markdown_checks_lychee_is_non_fatal():
    checks = build_markdown_checks(["README.md"])
    lychee = next(c for c in checks if c.name == "markdown: lychee")
    assert lychee.fatal is False


def test_build_python_scripts_checks_only_type_checks_pep723_scripts(tmp_path):
    (tmp_path / "plain.py").write_text("print('hi')\n")
    (tmp_path / "tool.py").write_text(
        "# /// script\n# requires-python = '>=3.12'\n# ///\n"
    )
    checks = build_python_scripts_checks(["plain.py", "tool.py"], tmp_path)
    names = [c.name for c in checks]
    assert "python-scripts: ty (tool.py)" in names
    assert "python-scripts: ty (plain.py)" not in names


def test_build_checks_dispatches_by_detected_language(tmp_path):
    languages = {"bash": "scripts", "markdown": "files"}
    files = ["a.sh", "README.md"]
    checks = build_checks(languages, files, tmp_path)
    names = [c.name for c in checks]
    assert "bash: shfmt" in names
    assert "markdown: codespell" in names
    assert not any(name.startswith("python") for name in names)


def test_check_requires_command_or_run():
    with pytest.raises(ValueError, match="needs either"):
        Check("nothing")


def test_check_requires_defaults_to_the_command_s_first_argument():
    check = Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])
    assert check.requires == ["shfmt"]


def test_check_requires_is_not_overridden_when_given_explicitly():
    check = Check("custom", run=lambda cwd: True, requires=["uv", "ty"])
    assert check.requires == ["uv", "ty"]


def test_check_run_without_requires_defaults_to_empty():
    check = Check("custom", run=lambda cwd: True)
    assert check.requires == []


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_checks_missing_tool_is_a_failure_by_default(mock_which, tmp_path):
    checks = [Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])]
    assert run_checks(checks, tmp_path) == ["bash: shfmt"]


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_check_missing_tool_returns_fail_by_default(mock_which, tmp_path):
    check = Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])
    assert run_check(check, tmp_path) == "fail"


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_check_missing_tool_returns_skip_when_configured(mock_which, tmp_path):
    check = Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])
    assert run_check(check, tmp_path, on_missing_tool="skip") == "skip"


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_checks_missing_tool_skip_is_not_a_failure(mock_which, tmp_path):
    checks = [
        Check("bash: shfmt", command=["shfmt", "-d", "a.sh"]),
        Check("bash: shellcheck", command=["shellcheck", "a.sh"]),
    ]
    assert run_checks(checks, tmp_path, on_missing_tool="skip") == []


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_check_missing_tool_never_invokes_a_run_callable(mock_which, tmp_path):
    # A `run`-based check (e.g. the PEP 723 type-check) must not execute its
    # side effect at all when one of its `requires` is missing -- not even to
    # then fail from inside the callable.
    invoked = []
    check = Check(
        "python-scripts: ty (tool.py)",
        run=lambda cwd: invoked.append(True) or True,
        requires=["uv", "ty"],
    )
    assert run_check(check, tmp_path) == "fail"
    assert invoked == []


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None)
def test_run_checks_fail_fast_does_not_stop_on_a_skip(mock_which, tmp_path):
    ran = []

    def _record(cwd):
        ran.append("second")
        return True

    checks = [
        Check("bash: shfmt", command=["shfmt", "-d", "a.sh"]),
        Check("second", run=_record),
    ]
    assert run_checks(checks, tmp_path, on_missing_tool="skip", fail_fast=True) == []
    assert ran == ["second"]


@patch("github_rest_api.scripts.lint_repo.sp.run")
@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value="/usr/bin/shfmt")
def test_run_checks_reports_the_command_that_failed(mock_which, mock_run, tmp_path):
    mock_run.return_value.returncode = 1
    checks = [Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])]
    assert run_checks(checks, tmp_path) == ["bash: shfmt"]
    mock_run.assert_called_once_with(["shfmt", "-d", "a.sh"], cwd=tmp_path, env=None)


def test_run_checks_non_fatal_failure_is_not_returned():
    checks = [Check("markdown: lychee", run=lambda cwd: False, fatal=False)]
    assert run_checks(checks, Path(".")) == []


def test_run_checks_fail_fast_stops_after_the_first_fatal_failure():
    ran = []

    def _record(name, ok):
        def _run(cwd):
            ran.append(name)
            return ok

        return _run

    checks = [
        Check("first", run=_record("first", False)),
        Check("second", run=_record("second", True)),
    ]
    assert run_checks(checks, Path("."), fail_fast=True) == ["first"]
    assert ran == ["first"]


def test_run_checks_fail_fast_does_not_stop_on_a_non_fatal_failure():
    ran = []

    def _record(name, ok):
        def _run(cwd):
            ran.append(name)
            return ok

        return _run

    checks = [
        Check("markdown: lychee", run=_record("lychee", False), fatal=False),
        Check("second", run=_record("second", True)),
    ]
    assert run_checks(checks, Path("."), fail_fast=True) == []
    assert ran == ["lychee", "second"]


def test_run_checks_without_fail_fast_runs_every_check():
    ran = []

    def _record(name, ok):
        def _run(cwd):
            ran.append(name)
            return ok

        return _run

    checks = [
        Check("first", run=_record("first", False)),
        Check("second", run=_record("second", True)),
    ]
    assert run_checks(checks, Path(".")) == ["first"]
    assert ran == ["first", "second"]


def test_run_checks_passes_when_run_callable_succeeds():
    checks = [Check("custom", run=lambda cwd: True)]
    assert run_checks(checks, Path(".")) == []


@patch("github_rest_api.scripts.lint_repo.run_checks", return_value=["bash: shfmt"])
def test_lint_repo_returns_failed_checks(mock_run_checks, tmp_path):
    _init_repo(tmp_path, {"a.sh": ""})
    assert lint_repo(root=tmp_path) == ["bash: shfmt"]
    mock_run_checks.assert_called_once()


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_restricts_to_requested_languages(mock_run_checks, tmp_path):
    _init_repo(tmp_path, {"a.sh": "", "README.md": ""})
    lint_repo(root=tmp_path, languages=["bash"])
    checks = mock_run_checks.call_args[0][0]
    names = [c.name for c in checks]
    assert any(name.startswith("bash") for name in names)
    assert not any(name.startswith("markdown") for name in names)


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_skips_undetected_requested_language(
    mock_run_checks, tmp_path, capsys
):
    _init_repo(tmp_path, {"a.sh": ""})
    result = lint_repo(root=tmp_path, languages=["golang"])
    assert result == []
    mock_run_checks.assert_not_called()
    assert "not detected" in capsys.readouterr().out


def test_lint_repo_with_no_supported_languages_runs_nothing(tmp_path):
    _init_repo(tmp_path, {"README.rst": ""})
    assert lint_repo(root=tmp_path) == []


def test_build_python_project_checks_covers_every_step():
    commands = [c.command for c in build_python_project_checks()]
    assert ["uv", "sync", "--all-extras"] in commands
    assert ["uv", "run", "ty", "check"] in commands
    assert ["uv", "run", "deptry", "."] in commands


def test_build_rust_project_checks():
    checks = build_rust_project_checks()
    assert len(checks) == 1
    assert checks[0].command == ["cargo", "fmt", "--all", "--", "--check"]


def test_build_golang_checks_sets_goflags_env():
    checks = build_golang_checks()
    lint = next(c for c in checks if c.name == "golang: lint")
    assert lint.env == {"GOFLAGS": "-buildvcs=false"}
    fmt = next(c for c in checks if c.name == "golang: fmt")
    assert fmt.command == ["golangci-lint", "fmt", "-d"]


def test_build_lua_checks():
    commands = [c.command for c in build_lua_checks()]
    assert ["stylua", "--check", "."] in commands
    assert ["selene", "."] in commands


def test_build_checks_dispatches_project_modes(tmp_path):
    languages = {
        "python": "project",
        "rust": "project",
        "golang": "module",
        "lua": "scripts",
    }
    checks = build_checks(languages, [], tmp_path)
    names = [c.name for c in checks]
    assert "python-project: deptry" in names
    assert "rust-project: cargo fmt" in names
    assert "golang: lint" in names
    assert "lua: selene" in names


# --- --fix mode: each builder's autofix command vs. its check-only one -----


def test_build_python_project_checks_fix_mode():
    by_name = {c.name: c.command for c in build_python_project_checks(fix=True)}
    assert by_name["python-project: pyproject-fmt"] == [
        "uv",
        "run",
        "pyproject-fmt",
        "pyproject.toml",
    ]
    assert by_name["python-project: ruff format"] == [
        "uv",
        "run",
        "ruff",
        "format",
        "./",
    ]
    assert by_name["python-project: ruff check"] == [
        "uv",
        "run",
        "ruff",
        "check",
        "--fix",
    ]
    # No fix mode for these two: unchanged.
    assert by_name["python-project: ty check"] == ["uv", "run", "ty", "check"]
    assert by_name["python-project: deptry"] == ["uv", "run", "deptry", "."]


def test_build_python_scripts_checks_fix_mode(tmp_path):
    (tmp_path / "a.py").write_text("")
    checks = build_python_scripts_checks(["a.py"], tmp_path, fix=True)
    by_name = {c.name: c.command for c in checks}
    assert by_name["python-scripts: ruff format"] == ["ruff", "format", "."]
    assert by_name["python-scripts: ruff check"] == [
        "ruff",
        "check",
        "--extend-select",
        "I,RUF022",
        "--fix",
        ".",
    ]


def test_build_rust_project_checks_fix_mode():
    checks = build_rust_project_checks(fix=True)
    assert checks[0].command == ["cargo", "fmt", "--all"]


def test_build_rust_scripts_checks_fix_mode():
    checks = build_rust_scripts_checks(["a.rs"], fix=True)
    assert checks[0].command == ["rustfmt", "--edition", "2024", "a.rs"]


def test_build_golang_checks_fix_mode():
    by_name = {c.name: c.command for c in build_golang_checks(fix=True)}
    assert by_name["golang: fmt"] == ["golangci-lint", "fmt"]
    assert by_name["golang: lint"] == ["golangci-lint", "run", "--fix"]


def test_build_bash_checks_fix_mode():
    checks = build_bash_checks(["a.sh"], fix=True)
    by_name = {c.name: c.command for c in checks}
    assert by_name["bash: shfmt"] == ["shfmt", "-i", "4", "-ci", "-w", "a.sh"]
    # shellcheck has no autofix: unchanged.
    assert by_name["bash: shellcheck"] == ["shellcheck", "a.sh"]


def test_build_fish_checks_fix_mode():
    checks = build_fish_checks(["a.fish"], fix=True)
    by_name = {c.name: c.command for c in checks}
    assert by_name["fish: fish_indent"] == ["fish_indent", "-w", "a.fish"]
    # `fish -n` has no autofix: unchanged.
    assert by_name["fish: fish -n (a.fish)"] == ["fish", "-n", "a.fish"]


def test_build_lua_checks_fix_mode():
    by_name = {c.name: c.command for c in build_lua_checks(fix=True)}
    assert by_name["lua: stylua"] == ["stylua", "."]
    # selene has no autofix: unchanged.
    assert by_name["lua: selene"] == ["selene", "."]


def test_build_markdown_checks_fix_mode(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SKILL.md").write_text("")
    (tmp_path / "README.md").write_text("")
    checks = build_markdown_checks(["README.md", "docs/SKILL.md"], fix=True)
    by_name = {c.name: c.command for c in checks}
    assert by_name["markdown: mdformat"] == ["mdformat", "README.md"]
    assert by_name["markdown: mdformat (SKILL.md)"] == [
        "mdformat",
        "--number",
        "docs/SKILL.md",
    ]
    assert by_name["markdown: codespell"] == [
        "codespell",
        "-w",
        "README.md",
        "docs/SKILL.md",
    ]
    # lychee has no autofix and stays non-fatal: unchanged.
    lychee = next(c for c in checks if c.name == "markdown: lychee")
    assert lychee.command == ["lychee", "--no-progress", "README.md", "docs/SKILL.md"]
    assert lychee.fatal is False


def test_build_checks_threads_fix_through(tmp_path):
    checks = build_checks({"bash": "scripts"}, ["a.sh"], tmp_path, fix=True)
    shfmt = next(c for c in checks if c.name == "bash: shfmt")
    assert shfmt.command == ["shfmt", "-i", "4", "-ci", "-w", "a.sh"]


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_passes_fix_through(mock_run_checks, tmp_path):
    _init_repo(tmp_path, {"a.sh": ""})
    lint_repo(root=tmp_path, fix=True)
    checks = mock_run_checks.call_args[0][0]
    shfmt = next(c for c in checks if c.name == "bash: shfmt")
    assert shfmt.command == ["shfmt", "-i", "4", "-ci", "-w", "a.sh"]


def test_parse_args_fix_defaults_to_false():
    assert parse_args([]).fix is False
    assert parse_args(["--fix"]).fix is True


@patch("github_rest_api.scripts.lint_repo.lint_repo", return_value=[])
def test_main_passes_fix_through(mock_lint_repo, monkeypatch):
    monkeypatch.setattr("sys.argv", ["lint_repo", "--fix"])
    assert main() == 0
    _, kwargs = mock_lint_repo.call_args
    assert kwargs["fix"] is True


def test_missing_tool_reports_the_tool_name():
    with patch("github_rest_api.scripts.lint_repo.shutil.which", return_value=None):
        assert _missing_tool("shfmt") == "shfmt"
    with patch(
        "github_rest_api.scripts.lint_repo.shutil.which",
        return_value="/usr/bin/shfmt",
    ):
        assert _missing_tool("shfmt") is None


def test_has_pep723_header_missing_file_returns_false(tmp_path):
    assert not _has_pep723_header(tmp_path / "does-not-exist.py")


@patch("github_rest_api.scripts.lint_repo.sp.run")
def test_check_pep723_script_types_sync_failure(mock_run, tmp_path):
    mock_run.return_value.returncode = 1
    assert not _check_pep723_script_types("tool.py", tmp_path)
    mock_run.assert_called_once_with(
        ["uv", "sync", "--script", "tool.py"], cwd=tmp_path
    )


@patch("github_rest_api.scripts.lint_repo.sp.run")
def test_check_pep723_script_types_python_find_failure(mock_run, tmp_path):
    sync_result, find_result = Mock(returncode=0), Mock(returncode=1, stderr="boom")
    mock_run.side_effect = [sync_result, find_result]
    assert not _check_pep723_script_types("tool.py", tmp_path)
    assert mock_run.call_count == 2


@patch("github_rest_api.scripts.lint_repo.sp.run")
def test_check_pep723_script_types_success(mock_run, tmp_path):
    sync_result = Mock(returncode=0)
    find_result = Mock(returncode=0, stdout="/opt/py/bin/python3\n")
    ty_result = Mock(returncode=0)
    mock_run.side_effect = [sync_result, find_result, ty_result]
    assert _check_pep723_script_types("tool.py", tmp_path)
    mock_run.assert_called_with(
        ["ty", "check", "--python", "/opt/py/bin/python3", "tool.py"], cwd=tmp_path
    )


def test_build_python_scripts_checks_pep723_check_requires_uv_and_ty(tmp_path):
    (tmp_path / "tool.py").write_text("# /// script\n# ///\n")
    checks = build_python_scripts_checks(["tool.py"], tmp_path)
    ty_check = next(c for c in checks if c.name == "python-scripts: ty (tool.py)")
    assert ty_check.requires == ["uv", "ty"]


@patch("github_rest_api.scripts.lint_repo.shutil.which", return_value="/usr/bin/shfmt")
def test_run_check_merges_env(mock_which, tmp_path):
    with patch("github_rest_api.scripts.lint_repo.sp.run") as mock_run:
        mock_run.return_value.returncode = 0
        check = Check(
            "golang: lint", command=["golangci-lint", "run"], env={"GOFLAGS": "x"}
        )
        assert run_check(check, tmp_path) == "pass"
        _, kwargs = mock_run.call_args
        assert kwargs["env"]["GOFLAGS"] == "x"


def test_run_check_catches_unexpected_exceptions(tmp_path, capsys):
    with patch(
        "github_rest_api.scripts.lint_repo.shutil.which", return_value="/usr/bin/x"
    ):
        with patch(
            "github_rest_api.scripts.lint_repo.sp.run", side_effect=OSError("boom")
        ):
            check = Check("bash: shfmt", command=["shfmt", "-d", "a.sh"])
            assert run_check(check, tmp_path) == "fail"
    assert "boom" in capsys.readouterr().err


def test_run_check_catches_exception_from_run_callable(tmp_path, capsys):
    def _boom(cwd):
        raise RuntimeError("boom")

    check = Check("custom", run=_boom)
    assert run_check(check, tmp_path) == "fail"
    assert "boom" in capsys.readouterr().err


def test_run_check_prints_ci_group_markers(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    check = Check("custom", run=lambda cwd: True)
    assert run_check(check, tmp_path) == "pass"
    out = capsys.readouterr().out
    assert "::group::custom" in out
    assert "::endgroup::" in out


def test_parse_args_defaults():
    args = parse_args([])
    assert args.root == "."
    assert args.languages is None
    assert args.on_missing_tool == "fail"
    assert args.fail_fast is False


def test_parse_args_on_missing_tool_and_fail_fast():
    args = parse_args(["--on-missing-tool", "skip", "--fail-fast"])
    assert args.on_missing_tool == "skip"
    assert args.fail_fast is True
    with pytest.raises(SystemExit):
        parse_args(["--on-missing-tool", "ignore"])


def test_parse_args_languages_must_be_known():
    args = parse_args(["--languages", "python", "rust"])
    assert args.languages == ["python", "rust"]
    with pytest.raises(SystemExit):
        parse_args(["--languages", "cobol"])


def test_parse_args_languages_choices_match_detect_languages():
    assert set(LANGUAGES) == {
        "python",
        "rust",
        "golang",
        "bash",
        "fish",
        "lua",
        "markdown",
    }


@patch("github_rest_api.scripts.lint_repo.lint_repo", return_value=[])
def test_main_returns_0_when_nothing_failed(mock_lint_repo, monkeypatch):
    monkeypatch.setattr("sys.argv", ["lint_repo"])
    assert main() == 0


@patch("github_rest_api.scripts.lint_repo.lint_repo", return_value=["bash: shfmt"])
def test_main_returns_1_when_a_check_failed(mock_lint_repo, monkeypatch):
    monkeypatch.setattr("sys.argv", ["lint_repo"])
    assert main() == 1


@patch(
    "github_rest_api.scripts.lint_repo.lint_repo", side_effect=ValueError("bad root")
)
def test_main_returns_1_and_prints_on_exception(mock_lint_repo, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["lint_repo"])
    assert main() == 1
    assert "bad root" in capsys.readouterr().err


@patch("github_rest_api.scripts.lint_repo.lint_repo", return_value=[])
def test_main_passes_on_missing_tool_and_fail_fast_through(mock_lint_repo, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["lint_repo", "--on-missing-tool", "skip", "--fail-fast"]
    )
    assert main() == 0
    _, kwargs = mock_lint_repo.call_args
    assert kwargs["on_missing_tool"] == "skip"
    assert kwargs["fail_fast"] is True


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_passes_on_missing_tool_and_fail_fast_through(
    mock_run_checks, tmp_path
):
    _init_repo(tmp_path, {"a.sh": ""})
    lint_repo(root=tmp_path, on_missing_tool="skip", fail_fast=True)
    _, kwargs = mock_run_checks.call_args
    assert kwargs["on_missing_tool"] == "skip"
    assert kwargs["fail_fast"] is True


# --- --test / --test-only ---------------------------------------------------


def test_has_test_function_true_for_a_pytest_style_function(tmp_path):
    path = tmp_path / "test_foo.py"
    path.write_text("def test_something():\n    assert True\n")
    assert _has_test_function(path)


def test_has_test_function_false_without_one(tmp_path):
    path = tmp_path / "plain.py"
    path.write_text("def helper():\n    return 1\n")
    assert not _has_test_function(path)


def test_has_test_function_missing_file_returns_false(tmp_path):
    assert not _has_test_function(tmp_path / "does-not-exist.py")


def test_build_python_project_test_checks():
    checks = build_python_project_test_checks()
    by_name = {c.name: c.command for c in checks}
    # `uv sync --all-extras` is included so `--test-only` (which skips the
    # lint checks' own sync step) still syncs extras a test might need.
    assert by_name["python-project: uv sync"] == ["uv", "sync", "--all-extras"]
    assert by_name["python-project: pytest"] == ["uv", "run", "pytest"]


def test_build_python_scripts_test_checks_only_includes_files_with_tests(tmp_path):
    (tmp_path / "plain.py").write_text("print('hi')\n")
    (tmp_path / "test_tool.py").write_text("def test_it():\n    assert True\n")
    checks = build_python_scripts_test_checks(["plain.py", "test_tool.py"], tmp_path)
    names = [c.name for c in checks]
    assert names == ["python-scripts: pytest (test_tool.py)"]
    # No PEP 723 header, so no `--with-requirements`: `uv run --with-requirements`
    # errors out on a script that doesn't have one (unlike `uv sync --script`).
    assert checks[0].command == [
        "uv",
        "run",
        "--with",
        "pytest",
        "pytest",
        "test_tool.py",
    ]
    assert checks[0].requires == ["uv"]


def test_build_python_scripts_test_checks_uses_with_requirements_for_pep723(tmp_path):
    (tmp_path / "test_tool.py").write_text(
        "# /// script\n# requires-python = '>=3.12'\n# ///\n"
        "def test_it():\n    assert True\n"
    )
    checks = build_python_scripts_test_checks(["test_tool.py"], tmp_path)
    assert checks[0].command == [
        "uv",
        "run",
        "--with",
        "pytest",
        "--with-requirements",
        "test_tool.py",
        "pytest",
        "test_tool.py",
    ]


def test_build_rust_project_test_checks():
    checks = build_rust_project_test_checks()
    assert len(checks) == 1
    assert checks[0].command == ["cargo", "test", "--workspace"]


def test_build_golang_test_checks_sets_goflags_env():
    checks = build_golang_test_checks()
    assert len(checks) == 1
    assert checks[0].command == ["go", "test", "./..."]
    assert checks[0].env == {"GOFLAGS": "-buildvcs=false"}


def test_build_test_checks_python_project(tmp_path):
    checks = build_test_checks({"python": "project"}, [], tmp_path)
    assert [c.name for c in checks] == [
        "python-project: uv sync",
        "python-project: pytest",
    ]


def test_build_test_checks_rust_scripts_mode_gets_no_test_check(tmp_path):
    # A loose .rs script has no `Cargo.toml`, so there's no `cargo test` to run.
    assert build_test_checks({"rust": "scripts"}, ["a.rs"], tmp_path) == []


def test_build_test_checks_dispatches_python_rust_golang(tmp_path):
    languages = {"python": "project", "rust": "project", "golang": "module"}
    checks = build_test_checks(languages, [], tmp_path)
    names = [c.name for c in checks]
    assert "python-project: pytest" in names
    assert "rust-project: cargo test" in names
    assert "golang: go test" in names


def test_build_test_checks_bash_fish_lua_markdown_get_nothing(tmp_path):
    languages = {
        "bash": "scripts",
        "fish": "scripts",
        "lua": "scripts",
        "markdown": "files",
    }
    files = ["a.sh", "a.fish", "a.lua", "a.md"]
    assert build_test_checks(languages, files, tmp_path) == []


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_test_adds_test_checks_alongside_lint(mock_run_checks, tmp_path):
    _init_repo(tmp_path, {"a.sh": ""})
    lint_repo(root=tmp_path, test=True)
    names = [c.name for c in mock_run_checks.call_args[0][0]]
    # bash has no test convention, but its lint checks are still present.
    assert "bash: shfmt" in names


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_test_only_skips_lint_checks(mock_run_checks, tmp_path):
    _init_repo(tmp_path, {"pyproject.toml": ""})
    lint_repo(root=tmp_path, test_only=True)
    names = [c.name for c in mock_run_checks.call_args[0][0]]
    assert names == ["python-project: uv sync", "python-project: pytest"]


@patch("github_rest_api.scripts.lint_repo.run_checks")
def test_lint_repo_test_only_without_a_test_convention_runs_nothing(
    mock_run_checks, tmp_path
):
    _init_repo(tmp_path, {"a.sh": ""})
    assert lint_repo(root=tmp_path, test_only=True) == []
    mock_run_checks.assert_not_called()


def test_parse_args_test_and_test_only_default_to_false():
    args = parse_args([])
    assert args.test is False
    assert args.test_only is False


def test_parse_args_test_and_test_only():
    args = parse_args(["--test"])
    assert args.test is True
    assert args.test_only is False
    args = parse_args(["--test-only"])
    assert args.test_only is True


@patch("github_rest_api.scripts.lint_repo.lint_repo", return_value=[])
def test_main_passes_test_and_test_only_through(mock_lint_repo, monkeypatch):
    monkeypatch.setattr("sys.argv", ["lint_repo", "--test", "--test-only"])
    assert main() == 0
    _, kwargs = mock_lint_repo.call_args
    assert kwargs["test"] is True
    assert kwargs["test_only"] is True
