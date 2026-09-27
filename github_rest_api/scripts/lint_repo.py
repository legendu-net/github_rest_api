"""Detect the languages used in a git repository and lint each of them.

This mirrors the ``detect`` + per-language lint jobs of ``lint.yaml`` (a
reusable GitHub Actions workflow), but as a single script meant to run
wherever the required tools already are -- a slim, everything-installed
container image in CI, or a developer's own toolbox locally. By default it
only checks (nothing is changed), a missing tool fails its check, and every
check runs before reporting failures -- but all three are configurable:

- ``--fix``: autofix what each tool can (formatting, safe lint fixes)
  instead of only checking, for local use.
- ``--on-missing-tool skip``: skip a check instead of failing it when its
  tool isn't on PATH, e.g. against a developer's toolbox that only has some
  of the tools installed.
- ``--fail-fast``: stop at the first fatal failure instead of always running
  everything.
- ``--test``: also run each language's test suite, alongside linting.
  ``--test-only`` runs just the tests, skipping lint checks entirely.
"""

import argparse
import os
import re
import shutil
import subprocess as sp
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from dulwich.repo import Repo

#: The outcome of running one check.
Outcome = Literal["pass", "fail", "skip"]

#: What to do when a check's tool isn't found on PATH.
OnMissingTool = Literal["fail", "skip"]

# Order mirrors the `detect` job in lint.yaml.
LANGUAGES = ["python", "rust", "golang", "bash", "fish", "lua", "markdown"]


def list_tracked_files(root: Path) -> list[str]:
    """List all git-tracked files in a repository.

    :param root: The root directory of the git repository (not an arbitrary
        subdirectory of one -- the returned paths, and every command a check
        later runs, are relative to this exact directory).
    :return: A sorted list of POSIX-style paths, relative to `root`.
    """
    repo = Repo(str(root))
    try:
        # `surrogateescape` so a non-UTF-8 filename (which git tracks fine)
        # doesn't crash the whole run; it just won't print legibly.
        return sorted(
            name.decode(errors="surrogateescape") for name in repo.open_index()
        )
    finally:
        repo.close()


def detect_languages(files: Sequence[str]) -> dict[str, str]:
    """Detect which languages a repository contains and how.

    Mirrors the ``detect`` job in ``lint.yaml``: a language backed by a
    project file at the repository root is linted as a whole project;
    otherwise the presence of any file with a matching extension is enough
    to lint it as a loose collection of scripts.

    :param files: The relative paths of all git-tracked files in the repo.
    :return: A mapping of language name to detection mode, e.g.
        ``{"python": "project", "markdown": "files"}``. A language not
        present in the repository is omitted.
    """
    file_set = set(files)
    paths = [Path(f) for f in files]

    def any_match(pattern: str) -> bool:
        return any(p.match(pattern) for p in paths)

    languages: dict[str, str] = {}
    if "pyproject.toml" in file_set:
        languages["python"] = "project"
    elif any_match("*.py"):
        languages["python"] = "scripts"
    if "Cargo.toml" in file_set:
        languages["rust"] = "project"
    elif any_match("*.rs"):
        languages["rust"] = "scripts"
    if "go.mod" in file_set:
        languages["golang"] = "module"
    if any_match("*.sh"):
        languages["bash"] = "scripts"
    if any_match("*.fish"):
        languages["fish"] = "scripts"
    if any_match("*.lua"):
        languages["lua"] = "scripts"
    if any_match("*.md"):
        languages["markdown"] = "files"
    return languages


def _files_matching(files: Sequence[str], pattern: str) -> list[str]:
    """Filter and sort the tracked files matching an extension glob."""
    return sorted(f for f in files if Path(f).match(pattern))


def _cmd(fix: bool, fix_command: list[str], check_command: list[str]) -> list[str]:
    """Pick a check's command for `--fix` mode or plain (read-only) checking.

    Not every tool has a fix mode (e.g. a type checker or a linter with no
    safe autofix), so a builder simply doesn't call this for those and uses
    the same command either way.
    """
    return fix_command if fix else check_command


@dataclass
class Check:
    """A single lint step.

    :param name: A human-readable, unique label for the check.
    :param command: The command to run. Mutually exclusive with `run`.
    :param run: A callable that performs the check itself, for steps whose
        command depends on the output of an earlier command (e.g. resolving
        the Python interpreter of a PEP 723 script). Takes the directory the
        check should operate in and returns whether it passed.
    :param requires: The tools (as found by `shutil.which`) this check needs
        on PATH. Defaults to `[command[0]]` for a `command` check; a `run`
        check that shells out to a tool itself must list it explicitly to
        get `--on-missing-tool` handling for free.
    :param fatal: Whether a failure of this check should fail the overall run.
    :param env: Extra environment variables to set (in addition to the
        current environment) when running `command`.
    """

    name: str
    command: list[str] | None = None
    run: Callable[[Path], bool] | None = None
    requires: list[str] = field(default_factory=list)
    fatal: bool = True
    env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.command is None and self.run is None:
            raise ValueError(f"Check {self.name!r} needs either `command` or `run`.")
        if not self.requires and self.command is not None:
            self.requires = [self.command[0]]


def _has_pep723_header(path: Path) -> bool:
    """Check whether a Python script carries a PEP 723 inline metadata block."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as fin:
            return any(line.rstrip("\n") == "# /// script" for line in fin)
    except OSError:
        return False


_TEST_FUNCTION = re.compile(r"^\s*def test_\w+\(", re.MULTILINE)


def _has_test_function(path: Path) -> bool:
    """Check whether a Python file defines at least one pytest-style test function."""
    try:
        return bool(
            _TEST_FUNCTION.search(path.read_text(encoding="utf-8", errors="ignore"))
        )
    except OSError:
        return False


def _check_pep723_script_types(script: str, cwd: Path) -> bool:
    """Type-check a PEP 723 script against its own declared dependencies.

    Its tools (`uv`, `ty`) are declared via the owning `Check`'s `requires`,
    so `run_check` already handles a missing one before this ever runs.
    """
    if sp.run(["uv", "sync", "--script", script], cwd=cwd).returncode != 0:
        return False
    found = sp.run(
        ["uv", "python", "find", "--script", script],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if found.returncode != 0:
        print(found.stderr, file=sys.stderr, end="")
        return False
    python = found.stdout.strip()
    return sp.run(["ty", "check", "--python", python, script], cwd=cwd).returncode == 0


def build_python_project_checks(fix: bool = False) -> list[Check]:
    return [
        Check("python-project: uv sync", command=["uv", "sync", "--all-extras"]),
        Check(
            "python-project: pyproject-fmt",
            command=_cmd(
                fix,
                ["uv", "run", "pyproject-fmt", "pyproject.toml"],
                ["uv", "run", "pyproject-fmt", "--check", "pyproject.toml"],
            ),
        ),
        Check(
            "python-project: ruff format",
            command=_cmd(
                fix,
                ["uv", "run", "ruff", "format", "./"],
                ["uv", "run", "ruff", "format", "--check", "./"],
            ),
        ),
        Check(
            "python-project: ruff check",
            command=_cmd(
                fix,
                ["uv", "run", "ruff", "check", "--fix"],
                ["uv", "run", "ruff", "check"],
            ),
        ),
        Check("python-project: ty check", command=["uv", "run", "ty", "check"]),
        Check("python-project: deptry", command=["uv", "run", "deptry", "."]),
    ]


def build_python_scripts_checks(
    files: Sequence[str], root: Path, fix: bool = False
) -> list[Check]:
    checks = [
        Check(
            "python-scripts: ruff format",
            command=_cmd(
                fix, ["ruff", "format", "."], ["ruff", "format", "--check", "."]
            ),
        ),
        Check(
            "python-scripts: ruff check",
            command=_cmd(
                fix,
                ["ruff", "check", "--extend-select", "I,RUF022", "--fix", "."],
                ["ruff", "check", "--extend-select", "I,RUF022", "."],
            ),
        ),
    ]
    py_files = _files_matching(files, "*.py")
    for script in py_files:
        if not _has_pep723_header(root / script):
            continue
        checks.append(
            Check(
                f"python-scripts: ty ({script})",
                run=lambda cwd, s=script: _check_pep723_script_types(s, cwd),
                requires=["uv", "ty"],
            )
        )
    return checks


def build_rust_project_checks(fix: bool = False) -> list[Check]:
    return [
        Check(
            "rust-project: cargo fmt",
            command=_cmd(
                fix,
                ["cargo", "fmt", "--all"],
                ["cargo", "fmt", "--all", "--", "--check"],
            ),
        )
    ]


def build_rust_scripts_checks(files: Sequence[str], fix: bool = False) -> list[Check]:
    rs_files = _files_matching(files, "*.rs")
    if not rs_files:
        return []
    return [
        Check(
            "rust-scripts: rustfmt",
            command=_cmd(
                fix,
                ["rustfmt", "--edition", "2024", *rs_files],
                ["rustfmt", "--check", "--edition", "2024", *rs_files],
            ),
        )
    ]


def build_golang_checks(fix: bool = False) -> list[Check]:
    return [
        Check(
            "golang: fmt",
            command=_cmd(fix, ["golangci-lint", "fmt"], ["golangci-lint", "fmt", "-d"]),
        ),
        Check(
            "golang: lint",
            command=_cmd(
                fix,
                ["golangci-lint", "run", "--fix"],
                ["golangci-lint", "run"],
            ),
            env={"GOFLAGS": "-buildvcs=false"},
        ),
    ]


def build_bash_checks(files: Sequence[str], fix: bool = False) -> list[Check]:
    sh_files = _files_matching(files, "*.sh")
    if not sh_files:
        return []
    return [
        Check(
            "bash: shfmt",
            command=_cmd(
                fix,
                ["shfmt", "-i", "4", "-ci", "-w", *sh_files],
                ["shfmt", "-i", "4", "-ci", "-d", *sh_files],
            ),
        ),
        # shellcheck has no autofix; it runs the same way in --fix mode.
        Check("bash: shellcheck", command=["shellcheck", *sh_files]),
    ]


def build_fish_checks(files: Sequence[str], fix: bool = False) -> list[Check]:
    fish_files = _files_matching(files, "*.fish")
    if not fish_files:
        return []
    checks = [
        Check(
            "fish: fish_indent",
            command=_cmd(
                fix,
                ["fish_indent", "-w", *fish_files],
                ["fish_indent", "-c", *fish_files],
            ),
        )
    ]
    # `fish -n a b` only checks `a` (`b` is passed to it as an argument), so
    # each file needs its own invocation, unlike the other xargs-driven checks.
    # `fish -n` is a syntax check with no autofix; it's the same in --fix mode.
    for f in fish_files:
        checks.append(Check(f"fish: fish -n ({f})", command=["fish", "-n", f]))
    return checks


def build_lua_checks(fix: bool = False) -> list[Check]:
    return [
        Check(
            "lua: stylua",
            command=_cmd(fix, ["stylua", "."], ["stylua", "--check", "."]),
        ),
        # selene has no autofix; it runs the same way in --fix mode.
        Check("lua: selene", command=["selene", "."]),
    ]


def build_markdown_checks(files: Sequence[str], fix: bool = False) -> list[Check]:
    # Deliberately scoped to git-tracked files, like every other check here,
    # rather than the raw filesystem glob `./**/*.md` the original workflow's
    # lychee-action step used -- so an untracked/generated .md file (e.g. in
    # a build output directory) isn't linted.
    md_files = _files_matching(files, "*.md")
    if not md_files:
        return []
    skill_files = [f for f in md_files if Path(f).name in ("SKILL.md", "AGENTS.md")]
    other_files = [f for f in md_files if f not in skill_files]
    checks = []
    if other_files:
        checks.append(
            Check(
                "markdown: mdformat",
                command=_cmd(
                    fix,
                    ["mdformat", *other_files],
                    ["mdformat", "--check", *other_files],
                ),
            )
        )
    if skill_files:
        checks.append(
            Check(
                "markdown: mdformat (SKILL.md/AGENTS.md)",
                command=_cmd(
                    fix,
                    ["mdformat", "--number", *skill_files],
                    ["mdformat", "--check", "--number", *skill_files],
                ),
            )
        )
    checks.append(
        Check(
            "markdown: codespell",
            command=_cmd(fix, ["codespell", "-w", *md_files], ["codespell", *md_files]),
        )
    )
    # lychee has no autofix (it's a link checker); it runs the same, and
    # stays non-fatal, in --fix mode.
    checks.append(
        Check(
            "markdown: lychee",
            command=["lychee", "--no-progress", *md_files],
            fatal=False,
        )
    )
    return checks


def build_checks(
    languages: dict[str, str], files: Sequence[str], root: Path, fix: bool = False
) -> list[Check]:
    """Build the list of checks to run for the detected languages.

    :param languages: A mapping of language name to detection mode, as
        returned by `detect_languages`.
    :param files: The relative paths of all git-tracked files in the repo.
    :param root: The root directory of the repository being linted.
    :param fix: Autofix what each tool can (formatting, safe lint fixes)
        instead of only checking. A tool with no fix mode (a type checker, a
        linter with no safe autofix, a link checker) runs the same either way.
    """
    checks: list[Check] = []
    if "python" in languages:
        checks += (
            build_python_project_checks(fix)
            if languages["python"] == "project"
            else build_python_scripts_checks(files, root, fix)
        )
    if "rust" in languages:
        checks += (
            build_rust_project_checks(fix)
            if languages["rust"] == "project"
            else build_rust_scripts_checks(files, fix)
        )
    if "golang" in languages:
        checks += build_golang_checks(fix)
    if "bash" in languages:
        checks += build_bash_checks(files, fix)
    if "fish" in languages:
        checks += build_fish_checks(files, fix)
    if "lua" in languages:
        checks += build_lua_checks(fix)
    if "markdown" in languages:
        checks += build_markdown_checks(files, fix)
    return checks


def build_python_project_test_checks() -> list[Check]:
    return [
        # `--test-only` skips `build_python_project_checks` entirely, whose
        # own leading `uv sync --all-extras` this would otherwise rely on --
        # without it, `uv run pytest`'s implicit sync only covers the
        # default dependency group, not extras a test might need.
        Check("python-project: uv sync", command=["uv", "sync", "--all-extras"]),
        Check("python-project: pytest", command=["uv", "run", "pytest"]),
    ]


def build_python_scripts_test_checks(files: Sequence[str], root: Path) -> list[Check]:
    checks = []
    for script in _files_matching(files, "*.py"):
        if not _has_test_function(root / script):
            continue
        command = ["uv", "run", "--with", "pytest"]
        # `--with-requirements <script>` additionally installs the script's
        # own PEP 723 dependencies -- but unlike `uv sync --script`, it
        # errors out on a script that has no PEP 723 header at all, so it's
        # only added when there is one.
        if _has_pep723_header(root / script):
            command += ["--with-requirements", script]
        command += ["pytest", script]
        checks.append(
            Check(
                f"python-scripts: pytest ({script})", command=command, requires=["uv"]
            )
        )
    return checks


def build_rust_project_test_checks() -> list[Check]:
    return [Check("rust-project: cargo test", command=["cargo", "test", "--workspace"])]


def build_golang_test_checks() -> list[Check]:
    return [
        Check(
            "golang: go test",
            command=["go", "test", "./..."],
            env={"GOFLAGS": "-buildvcs=false"},
        )
    ]


def build_test_checks(
    languages: dict[str, str], files: Sequence[str], root: Path
) -> list[Check]:
    """Build the list of test-running checks for the detected languages.

    Only languages with a standard, unambiguous test convention get one:
    python, rust (project mode -- a loose .rs script has no natural "cargo
    test" without a `Cargo.toml`), and golang. Bash, fish, lua and markdown
    have no test convention this can assume, so `--test`/`--test-only` add
    nothing for them.

    :param languages: A mapping of language name to detection mode, as
        returned by `detect_languages`.
    :param files: The relative paths of all git-tracked files in the repo.
    :param root: The root directory of the repository being linted.
    """
    checks: list[Check] = []
    if "python" in languages:
        checks += (
            build_python_project_test_checks()
            if languages["python"] == "project"
            else build_python_scripts_test_checks(files, root)
        )
    if languages.get("rust") == "project":
        checks += build_rust_project_test_checks()
    if "golang" in languages:
        checks += build_golang_test_checks()
    return checks


def _missing_tool(tool: str) -> str | None:
    return None if shutil.which(tool) else tool


def run_check(
    check: Check, cwd: Path, on_missing_tool: OnMissingTool = "fail"
) -> Outcome:
    """Run a single check, returning its outcome.

    A missing required tool is handled centrally here, before `check.run` or
    `check.command` ever runs, so both kinds of check get `--on-missing-tool`
    handling for free. An unexpected exception (e.g. the command couldn't
    even be launched) fails just this check rather than aborting the whole
    run, matching how the original workflow's jobs fail independently of
    each other.

    :param check: The check to run.
    :param cwd: The directory to run it in.
    :param on_missing_tool: What to do when one of `check.requires` isn't on
        PATH: `"fail"` the check, or `"skip"` it.
    """
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    if in_ci:
        print(f"::group::{check.name}", flush=True)
    try:
        missing = [tool for tool in check.requires if _missing_tool(tool)]
        if missing:
            message = f"missing tool(s) {', '.join(missing)} required for check '{check.name}'"
            if on_missing_tool == "skip":
                print(f"SKIP: {message}", flush=True)
                return "skip"
            print(f"error: {message}", file=sys.stderr)
            return "fail"
        if check.run is not None:
            passed = check.run(cwd)
        else:
            command = cast(list[str], check.command)
            env = {**os.environ, **check.env} if check.env else None
            passed = sp.run(command, cwd=cwd, env=env).returncode == 0
        return "pass" if passed else "fail"
    except Exception as e:
        print(f"error: {check.name} raised {e}", file=sys.stderr)
        return "fail"
    finally:
        if in_ci:
            print("::endgroup::", flush=True)


def run_checks(
    checks: Sequence[Check],
    cwd: Path,
    on_missing_tool: OnMissingTool = "fail",
    fail_fast: bool = False,
) -> list[str]:
    """Run every check, printing a summary.

    :param checks: The checks to run.
    :param cwd: The directory to run them in.
    :param on_missing_tool: What to do when a check's tool isn't on PATH: see
        `run_check`.
    :param fail_fast: Stop at the first fatal failure instead of running
        every check and reporting all failures at the end (the default).
    :return: The names of the fatal checks that failed (empty if all fatal
        checks passed).
    """
    failed: list[str] = []
    skipped: list[str] = []
    ran: list[Check] = []
    for check in checks:
        print(f"\n=== {check.name} ===", flush=True)
        ran.append(check)
        outcome = run_check(check, cwd, on_missing_tool=on_missing_tool)
        if outcome == "pass":
            print(f"PASS: {check.name}", flush=True)
        elif outcome == "skip":
            skipped.append(check.name)
        elif check.fatal:
            failed.append(check.name)
            print(f"FAIL: {check.name}", flush=True)
            if fail_fast:
                print("Stopping after the first failure (--fail-fast).", flush=True)
                break
        else:
            print(f"WARN (non-fatal): {check.name}", flush=True)
    passed = len(ran) - len(failed) - len(skipped)
    print(
        f"\n=== Summary: {passed}/{len(ran)} passed"
        f"{f', {len(skipped)} skipped' if skipped else ''}"
        f"{f', {len(failed)} failed' if failed else ''} ===",
        flush=True,
    )
    if failed:
        print("Failed checks:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
    return failed


def lint_repo(
    root: str | Path = ".",
    languages: Sequence[str] | None = None,
    on_missing_tool: OnMissingTool = "fail",
    fail_fast: bool = False,
    fix: bool = False,
    test: bool = False,
    test_only: bool = False,
) -> list[str]:
    """Detect the languages of a git repository and lint each of them.

    :param root: The root directory of the git repository to lint.
    :param languages: Only lint these languages. A requested language that
        isn't detected in the repository is skipped with a note. Defaults to
        every language detected in the repository.
    :param on_missing_tool: What to do when a check's tool isn't on PATH: see
        `run_check`.
    :param fail_fast: Stop at the first fatal failure instead of running
        every check and reporting all failures at the end (the default).
    :param fix: Autofix what each tool can, instead of only checking: see
        `build_checks`.
    :param test: Also run each language's test suite, alongside linting: see
        `build_test_checks`.
    :param test_only: Run only each language's test suite; skip linting
        entirely. Implies `test`.
    :return: The names of the fatal checks that failed (empty if everything
        passed).
    """
    root = Path(root)
    files = list_tracked_files(root)
    detected = detect_languages(files)
    selected = detected
    if languages:
        for language in languages:
            if language not in detected:
                print(
                    f"Note: '{language}' was requested but not detected in the "
                    "repository; skipping."
                )
        selected = {
            language: mode
            for language, mode in detected.items()
            if language in languages
        }
    if not selected:
        print("No supported languages detected; nothing to do.")
        return []
    print(
        "Detected languages:",
        ", ".join(f"{language} ({mode})" for language, mode in selected.items()),
    )
    checks: list[Check] = []
    if not test_only:
        checks += build_checks(selected, files, root, fix)
    if test or test_only:
        checks += build_test_checks(selected, files, root)
    if not checks:
        print("No checks to run.")
        return []
    return run_checks(
        checks, cwd=root, on_missing_tool=on_missing_tool, fail_fast=fail_fast
    )


def parse_args(args=None, namespace=None) -> argparse.Namespace:
    """Parse command-line arguments.

    :param args: The arguments to parse. If None, the arguments from the
        command line are parsed.
    :param namespace: An initial Namespace object.
    :return: A namespace object containing the parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Detect the languages used in a git repository and lint each of them."
    )
    parser.add_argument(
        "--root",
        dest="root",
        default=".",
        help=(
            "The root directory of the git repository to lint (default: '.'). "
            "Must be the repository's actual root, not an arbitrary subdirectory."
        ),
    )
    parser.add_argument(
        "--languages",
        dest="languages",
        nargs="+",
        default=None,
        choices=LANGUAGES,
        metavar="LANGUAGE",
        help=(
            "Only lint these languages, e.g. for an image that only has some "
            "languages' tools installed (default: every language detected in "
            "the repository)."
        ),
    )
    parser.add_argument(
        "--on-missing-tool",
        dest="on_missing_tool",
        choices=["fail", "skip"],
        default="fail",
        help=(
            "What to do when a check's tool isn't found on PATH: fail the "
            "check (default), or skip it -- e.g. when running against a "
            "developer's toolbox that only has some of the tools installed."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        dest="fail_fast",
        action="store_true",
        help=(
            "Stop at the first fatal failure. By default (this flag omitted) "
            "every check runs and all failures are reported at the end."
        ),
    )
    parser.add_argument(
        "--fix",
        dest="fix",
        action="store_true",
        help=(
            "Autofix what each tool can (formatting, safe lint fixes) instead "
            "of only checking. A tool with no fix mode (a type checker, a "
            "linter with no safe autofix, a link checker) runs the same "
            "either way. By default (this flag omitted), nothing is changed."
        ),
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help=(
            "Also run each language's test suite, in addition to linting. "
            "Only python, rust (project mode) and golang have a standard "
            "test convention to run this way; other languages are unaffected."
        ),
    )
    parser.add_argument(
        "--test-only",
        dest="test_only",
        action="store_true",
        help=(
            "Run only each language's test suite; skip linting entirely. "
            "Implies --test."
        ),
    )
    return parser.parse_args(args=args, namespace=namespace)


def main() -> int:
    args = parse_args()
    try:
        failed = lint_repo(
            root=args.root,
            languages=args.languages,
            on_missing_tool=args.on_missing_tool,
            fail_fast=args.fail_fast,
            fix=args.fix,
            test=args.test,
            test_only=args.test_only,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
