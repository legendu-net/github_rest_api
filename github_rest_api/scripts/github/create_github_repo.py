"""Add a GitHub repository and initialize a local Git repository with workflows."""

import argparse
import getpass
import logging
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from dulwich import porcelain

from github_rest_api import Organization, User

logger = logging.getLogger(__name__)


def _validate_repo(repo: str) -> None:
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo format '{repo}'. Expected 'owner/repo'.")


def _create_remote_repo(
    repo: str, private: bool, token: str, is_owner_user: bool
) -> None:
    owner, r = repo.split("/")
    entity = (
        User(token=token, user=owner)
        if is_owner_user
        else Organization(token=token, org=owner)
    )
    logger.info("Creating remote GitHub repository '%s'...", repo)
    entity.create_repository(name=r, private=private)
    logger.info("Created the GitHub repo https://github.com/%s.", repo)


def _remote_url(repo: str, protocol: str) -> str:
    if protocol == "git":
        return f"git@github.com:{repo}.git"
    return f"https://github.com/{repo}.git"


def _ensure_remote(path: Path, repo: str, protocol: str) -> None:
    try:
        porcelain.remote_add(path, "origin", _remote_url(repo, protocol))
        logger.info("Added remote 'origin' for %s", repo)
    except porcelain.RemoteExists:
        pass


def _active_branch(path: Path) -> str:
    try:
        return porcelain.active_branch(path).decode()
    except (IndexError, ValueError) as e:
        raise ValueError(
            f"HEAD of the local repo at '{path}' is detached (not on any branch), "
            "which is unsupported (this happens, e.g., with a colocated Jujutsu repo). "
            "Check out a branch (e.g. `git switch -c main`) before running this command."
        ) from e


def _init_local_repo(
    repo: str,
    language: str,
    dir_: str,
    token: str,
    protocol: str,
    branches: Sequence[str] = ("main",),
) -> None:
    repo_name = repo.split("/")[-1]
    path = Path(dir_) if dir_ else Path(repo_name)
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {repo_name}\n")
        logger.info("Created %s", readme)
    if not (path / ".git").exists():
        porcelain.init(path=path)
        logger.info("Initialized empty Git repository in %s", path)
    if not porcelain.branch_list(path):
        initial_branch = _active_branch(path)
        porcelain.add(repo=path, paths=["README.md"])
        logger.info("Added README.md to staging")
        porcelain.commit(repo=path, message="first commit")
        logger.info("Created first commit")
        for branch in branches:
            if branch != initial_branch:
                porcelain.branch_create(repo=path, name=branch)
                logger.info("Created branch '%s'", branch)
        porcelain.checkout(repo=path, target=branches[0])
        logger.info("Checked out branch '%s'", branches[0])
        if initial_branch not in branches:
            porcelain.branch_delete(repo=path, name=initial_branch)
            logger.info("Deleted initial branch '%s'", initial_branch)
    _ensure_remote(path, repo, protocol)
    local_branches = {b.decode() for b in porcelain.branch_list(path)}
    branches_to_push = [branch for branch in branches if branch in local_branches]
    if not branches_to_push:
        branches_to_push = [_active_branch(path)]
    for branch in branches_to_push:
        logger.info("Pushing branch '%s' to remote '%s'...", branch, repo)
        porcelain.push(
            repo=path,
            remote_location=_remote_url(repo, "https"),
            refspecs=[branch.encode()],
            username="x-access-token",
            password=token,
        )
        logger.info("Successfully pushed branch '%s'.", branch)
    _add_workflow(path, language)


def create_github_repo(
    repo: str,
    private: bool,
    language: str,
    is_owner_user: bool,
    dir_: str,
    token: str,
    protocol: str,
    branches: Sequence[str] = ("main",),
) -> None:
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not token:
        token = getpass.getpass("Please enter your GitHub token: ")
        if not token:
            raise ValueError(
                "No GitHub token is provided (via $GITHUB_TOKEN, --token or at prompt)."
            )
    repo = repo.strip()
    _validate_repo(repo)
    _create_remote_repo(
        repo=repo, private=private, token=token, is_owner_user=is_owner_user
    )
    _init_local_repo(
        repo=repo,
        language=language,
        dir_=dir_,
        token=token,
        branches=branches,
        protocol=protocol,
    )


def _add_workflow(path: Path, language: str, workflow_dir: Path | None = None) -> None:
    if workflow_dir is None:
        workflow_dir = Path(__file__).parent / "workflows"
    dir_dest = path / ".github" / "workflows"
    dir_dest.mkdir(parents=True, exist_ok=True)
    for yaml in workflow_dir.glob("*.yaml"):
        if not (dir_dest / yaml.name).exists():
            shutil.copy2(yaml, dir_dest)
            logger.info("Copied workflow %s to %s", yaml.name, dir_dest)
    if not language:
        return
    lang_dir = workflow_dir / language
    if not lang_dir.exists():
        return
    for yaml in lang_dir.glob("*.yaml"):
        if not (dir_dest / yaml.name).exists():
            shutil.copy2(yaml, dir_dest)
            logger.info(
                "Copied %s specific workflow %s to %s", language, yaml.name, dir_dest
            )


def parse_args(args=None, namespace=None):
    parser = argparse.ArgumentParser(description="Create a GitHub repository.")
    parser.add_argument(
        "repo",
        help="The GitHub repo (in the format of owner/repo) to be created.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-u",
        "--user",
        dest="is_owner_user",
        action="store_true",
        help="The owner of the repo is a user.",
    )
    group.add_argument(
        "-o",
        "--org",
        "--organization",
        dest="is_owner_user",
        action="store_false",
        help="The owner of the repo is an organization.",
    )
    parser.add_argument(
        "-t",
        "--token",
        dest="token",
        default="",
        help="The GitHub token to use.",
    )
    parser.add_argument(
        "-p",
        "--public",
        dest="private",
        action="store_false",
        help="Whether to create the repository as public.",
    )
    parser.add_argument(
        "-l",
        "--lang",
        "--language",
        dest="language",
        default="",
        help="The language of the GitHub repository.",
    )
    parser.add_argument(
        "-d",
        "--dir",
        dest="dir",
        default="",
        help="The directory (default to './repo') of the local Git repository.",
    )
    parser.add_argument(
        "--branches",
        dest="branches",
        nargs="+",
        default=["main"],
        metavar="BRANCH",
        help="Branches to create and push to remote (default: main).",
    )
    parser.add_argument(
        "--https",
        dest="protocol",
        action="store_const",
        const="https",
        default="git",
        help="Use the HTTPS protocol for the remote URL.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        help="Enable verbose output, including tracebacks on errors.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        create_github_repo(
            repo=args.repo,
            private=args.private,
            language=args.language,
            is_owner_user=args.is_owner_user,
            dir_=args.dir,
            token=args.token,
            branches=args.branches,
            protocol=args.protocol,
        )
    except Exception as e:
        logger.error("Failed to create GitHub repo: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
