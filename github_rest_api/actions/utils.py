"""Util functions for GitHub actions.
"""
import subprocess as sp


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
