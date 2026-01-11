"""Util functions for GitHub actions."""

from typing import Iterable
from pathlib import Path
import random
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


def strip_patch_version(version: str) -> str:
    parts = version.split(".")
    match len(parts):
        case 1:
            return parts[0] + ".0.0"
        case 2 | 3:
            return ".".join(parts[:2]) + ".0"
        case _:
            raise ValueError("Invalid version semantic provided!")


def strip_minor_version(version: str) -> str:
    parts = version.split(".")
    match len(parts):
        case 1:
            return parts[0] + ".0.0"
        case 2 | 3:
            return ".".join(parts[:1]) + ".0.0"
        case _:
            raise ValueError("Invalid version semantic provided!")
