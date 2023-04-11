"""Util functions for GitHub actions.
"""
from typing import Iterable
import string
import datetime
import subprocess as sp
import random


class FailToPushToGitHubException(Exception):
    """Exception for failure to push a branch to GitHub."""

    def __init__(self, branch: str, branch_alt):
        super().__init__(
            f"Failed to push the branch {branch} to GitHub! Pushed to {branch_alt} instead."
        )


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
    sp.run(cmd, shell=True, check=True)


def create_branch(branch: str) -> None:
    """Create a new local branch.
    :param branch: The new local branch to create.
    """
    cmd = f"git checkout -b {branch}"
    sp.run(cmd, shell=True, check=True)


def switch_branch(branch: str, fetch: bool) -> None:
    """Switch to another branch.
    :param branch: The branch to checkout.
    :param fetch: If true, fetch the branch from remote first.
    """
    if fetch:
        cmd = f"git fetch origin {branch}"
        sp.run(cmd, shell=True, check=True)
    cmd = f"git checkout {branch}"
    sp.run(cmd, shell=True, check=True)


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


def push_gh_pages(name: str):
    """Try pushing the local gh-pages branch.
    On failure, fork a new branch and push it to GitHub.
    :param name: A meaningful name as part of the name of the new branch.
    """
    cmd = "git push origin gh-pages"
    try:
        sp.run(cmd, shell=True, check=True)
    except Exception as err:
        branch = gen_temp_branch(
            prefix=f"gh-pages_{name}_", chars=string.ascii_lowercase, nrand=3
        )
        cmd = f"git checkout -b {branch} && git push origin {branch}"
        sp.run(cmd, shell=True, check=True)
        raise FailToPushToGitHubException("gh-pages", branch) from err


def build_project(profile: str = "release") -> None:
    """Build the Rust project.
    :param profile: The profile for building.
    """
    cmd = f"RUSTFLAGS=-Awarnings cargo build --profile {profile}"
    print("Started building at ", datetime.datetime.now(), sep="")
    sp.run(cmd, shell=True, check=True)
