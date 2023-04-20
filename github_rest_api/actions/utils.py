"""Util functions for GitHub actions.
"""
from typing import Iterable
from pathlib import Path
import random
from ..utils import run_cmd


class FailToPushToGitHubException(Exception):
    """Exception for failure to push a branch to GitHub."""

    def __init__(self, branch: str, branch_alt: str):
        msg = f"Failed to push the branch {branch} to GitHub!"
        if branch_alt:
            msg += f" Pushed to {branch_alt} instead."
        super().__init__(msg)


def config_git(local_repo_dir: str, user_email: str, user_name: str):
    """Config Git.
    :param local_repo_dir: The root directory of the project.
    :param user_email: The email of the user (no need to be a valid one).
    :param user_name: The name of the user.
    """
    cmd = f"""git config --global --add safe.directory {local_repo_dir} \
            && git config --global user.email "{user_email}" \
            && git config --global user.name "{user_name}"
        """
    run_cmd(cmd)


def create_branch(branch: str) -> None:
    """Create a new local branch.
    :param branch: The new local branch to create.
    """
    run_cmd(f"git checkout -b {branch}")


def switch_branch(branch: str, fetch: bool) -> None:
    """Switch to another branch.
    :param branch: The branch to checkout.
    :param fetch: If true, fetch the branch from remote first.
    """
    if fetch:
        run_cmd(f"git fetch origin {branch}")
    run_cmd(f"git checkout {branch}")


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
        run_cmd(f"git push origin {branch}")
    except Exception as err:
        if branch_alt:
            cmd = f"""git checkout {branch} \
                && git checkout -b {branch_alt} \
                && git push origin {branch_alt}
                """
            run_cmd(cmd)
        raise FailToPushToGitHubException(branch, branch_alt) from err


def commit_benchmarks(bench_dir: str | Path):
    """Commit changes in the benchmark directory.
    :param bench_dir: The benchmark directory.
    """
    run_cmd(f"git add {bench_dir} && git commit -m 'add benchmarks'")


def commit_profiling(prof_dir: str | Path):
    """Commit changes in the profiling directory.
    :param prof_dir: The profiling directory.
    """
    run_cmd(f"git add {prof_dir} && git commit -m 'update profiling results'")
