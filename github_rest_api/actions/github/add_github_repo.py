"""Add a GitHub repository and initialize a local Git repository with workflows."""

import argparse
import getpass
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from dulwich import porcelain

from github_rest_api import User, Organization


def parse_args(args=None, namespace=None):
    parser = argparse.ArgumentParser(description="Add a GitHub repository.")
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
    return parser.parse_args(args=args, namespace=namespace)


def _validate_repo(repo: str) -> None:
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        sys.exit(f"Invalid repo format '{repo}'. Expected 'owner/repo'.")


def _create_remote_repo(
    repo: str, private: bool, token: str, is_owner_user: bool
) -> None:
    owner, r = repo.split("/")
    entity = (
        User(token=token, user=owner)
        if is_owner_user
        else Organization(token=token, org=owner)
    )
    entity.create_repository(name=r, private=private)
    print(f"\nCreated the GitHub repo https://github.com/{repo}.\n")


def _init_local_repo(
    repo: str, language: str, dir_: str, token: str, branches: Sequence[str]
) -> None:
    repo_name = repo.split("/")[-1]
    path = Path(dir_) if dir_ else Path(repo_name)
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {repo_name}\n")
    if not (path / ".git").exists():
        porcelain.init(path=path)
        initial_branch = (
            (path / ".git" / "HEAD").read_text().strip().partition("refs/heads/")[-1]
        )
        porcelain.add(repo=path)
        porcelain.commit(repo=path, message="first commit")

        def _create_push_branch(branch: str):
            if branch != initial_branch:
                porcelain.branch_create(repo=path, name=branch)
            porcelain.checkout(repo=path, target=branch)
            porcelain.push(
                repo=path,
                remote_location=f"https://github.com/{repo}.git",
                username="x-access-token",
                password=token,
            )

        for branch in branches:
            _create_push_branch(branch)
        porcelain.checkout(repo=path, target=branches[0])
        if initial_branch not in branches:
            porcelain.branch_delete(repo=path, name=initial_branch)
    _add_workflow(path, language)


def add_github_repo(
    repo: str,
    private: bool,
    language: str,
    is_owner_user: bool,
    dir_: str,
    token: str,
    branches: Sequence[str] = ("main",),
) -> None:
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not token:
        token = getpass.getpass("Please enter your GitHub token: ")
        if not token:
            sys.exit(
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
    )


def _add_workflow(path: Path, language: str, workflow_dir: Path | None = None) -> None:
    if workflow_dir is None:
        workflow_dir = Path(__file__).parent / "workflows"
    dir_dest = path / ".github" / "workflows"
    dir_dest.mkdir(parents=True, exist_ok=True)
    for yaml in workflow_dir.glob("*.yml"):
        if not (dir_dest / yaml.name).exists():
            shutil.copy2(yaml, dir_dest)
    if not language:
        return
    lang_dir = workflow_dir / language
    if not lang_dir.exists():
        return
    for yaml in lang_dir.glob("*.yml"):
        if not (dir_dest / yaml.name).exists():
            shutil.copy2(yaml, dir_dest)


def main():
    args = parse_args()
    add_github_repo(
        repo=args.repo,
        private=args.private,
        language=args.language,
        is_owner_user=args.is_owner_user,
        dir_=args.dir,
        token=args.token,
        branches=args.branches,
    )


if __name__ == "__main__":
    main()
