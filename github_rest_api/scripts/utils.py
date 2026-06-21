"""Util functions for GitHub actions."""

import random
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Iterable

from dulwich import porcelain
from dulwich.repo import Repo


def config_git(local_repo_dir: str | Path, user_email: str, user_name: str):
    """Config Git.
    :param local_repo_dir: The root directory of the project.
    :param user_email: The email of the user (no need to be a valid one).
    :param user_name: The name of the user.
    """
    config = Repo(local_repo_dir).get_config()
    config.set(b"user", b"email", user_email.encode())
    config.set(b"user", b"name", user_name.encode())


def switch_branch(branch: str, fetch: bool) -> None:
    """Switch to another branch.
    :param branch: The branch to checkout.
    :param fetch: If true, fetch the branch from remote first.
    """
    if fetch:
        porcelain.fetch(repo=".")
    porcelain.checkout(repo=".", target=branch)


def gen_temp_branch(
    prefix: str = "_branch_", chars: Iterable[str | int] = range(10), nrand: int = 10
) -> str:
    """Generate a name for a (temp) branch.
    :param prefix: The prefix of the name.
    :param chars: An iterable of characters to sample from to form the suffix of the name.
    :param nrand: The number of characters for the suffix of the name.
    """
    if not isinstance(chars, (list, tuple)):
        chars = list(chars)
    chars = random.sample(chars, nrand)
    return prefix + "".join(str(char) for char in chars)


def push_branch(branch: str, branch_alt: str = ""):
    """Try pushing a local Git branch to remote.
    On failure, fork an alternative branch (if specified) and push it to GitHub.
    :param branch: The local branch to push to GitHub.
    :param branch_alt: An alternative branch name to push to GitHub.
    """
    try:
        porcelain.push(repo=".", refspecs=branch)
    except Exception as err:
        if branch_alt:
            porcelain.checkout(repo=".", target=branch)
            porcelain.checkout(repo=".", new_branch=branch_alt)
            porcelain.push(repo=".", refspecs=branch_alt)
        else:
            raise err


def commit_benchmarks(bench_dir: str | Path):
    """Commit changes in the benchmark directory.
    :param bench_dir: The benchmark directory.
    """
    porcelain.add(paths=bench_dir)
    porcelain.commit(message="Add benchmarks.")


def commit_profiling(prof_dir: str | Path):
    """Commit changes in the profiling directory.
    :param prof_dir: The profiling directory.
    """
    porcelain.add(paths=prof_dir)
    porcelain.commit(message="Updating profiling results.")


def find_project_root(path: Path | None = None) -> Path | None:
    if path is None:
        path = Path.cwd()
    while path != path.parent:
        if (path / ".git").exists():
            return path
        path = path.parent
    return None


def get_toml_value(path: Path, keys: Sequence[str]) -> Any:
    if not keys or not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None
    for key in keys[:-1]:
        data = data.get(key, {}) if isinstance(data, dict) else {}
    return data.get(keys[-1]) if isinstance(data, dict) else None


def get_project_version(root: Path) -> str:
    for path, keys in [
        (root / "pyproject.toml", ["project", "version"]),
        (root / "Cargo.toml", ["package", "version"]),
    ]:
        version = get_toml_value(path, keys)
        if isinstance(version, str) and version.strip():
            return version
    return ""


def parse_github_repo(url: str) -> str:
    delimiters = ["github.com/", "github.com:"]
    delim = next((d for d in delimiters if d in url), "")
    if not delim:
        return ""
    return url.split(delim)[-1].rstrip("/").removesuffix(".git")


def _get_repo_from_toml(path: Path, keys: Sequence[str]) -> str:
    value = get_toml_value(path, keys)
    return parse_github_repo(value) if isinstance(value, str) else ""


def get_repo(root: Path) -> str | None:
    if repo := _get_repo_from_toml(
        root / "pyproject.toml", ["project", "urls", "Repository"]
    ):
        return repo
    if repo := _get_repo_from_toml(root / "Cargo.toml", ["package", "repository"]):
        return repo
    try:
        repo = Repo(root)
        config = repo.get_config()
        url = config.get((b"remote", b"origin"), b"url").decode().strip()
        if repo_name := parse_github_repo(url):
            return repo_name
    except Exception:
        pass
    return None
