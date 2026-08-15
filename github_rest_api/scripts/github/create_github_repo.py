"""Add a GitHub repository and initialize a local Git repository with workflows."""

import argparse
import logging
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from dulwich import porcelain
from dulwich.refs import HEADREF, LOCAL_BRANCH_PREFIX, Ref

from github_rest_api import Organization, User
from github_rest_api.scripts.utils import resolve_github_token, validate_repo
from github_rest_api.utils import as_str_sequence

logger = logging.getLogger(__name__)

# Workflows that configure this repository itself rather than the repositories
# created from these templates.
_REPO_ONLY_WORKFLOWS = frozenset({"check_workflow_template.yaml"})


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
            f"Cannot determine the initial branch of the local repo at '{path}' "
            "as HEAD does not point at a branch. "
            "Check out a branch (e.g. `git switch -c main`) before running this command."
        ) from e


def _head_and_branches(path: Path) -> tuple[str | None, set[str]]:
    """Resolve HEAD and the local branches with a single open of the repo.

    Returns the commit SHA HEAD points at (None if it does not resolve to one)
    and the existing local branch names (without the `refs/heads/` prefix).
    """
    with porcelain.open_repo_closing(path) as repo:
        try:
            head = repo.refs[HEADREF].decode()
        except KeyError:
            head = None
        branches = {b.decode() for b in repo.refs.keys(base=Ref(LOCAL_BRANCH_PREFIX))}
    return head, branches


def _init_local_repo(
    repo: str,
    dir_: str,
    token: str,
    protocol: str,
    push: bool,
    branches: Sequence[str] = ("main",),
) -> None:
    branches = list(dict.fromkeys(branches))
    if not branches:
        raise ValueError("At least one branch must be specified.")
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
    head, existing_branches = _head_and_branches(path)
    if head is None and not existing_branches:
        # A brand new repository: no commit and no branch yet.
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
    else:
        missing = [b for b in branches if b not in existing_branches]
        if missing and head is None:
            # HEAD is unborn (e.g. after `git switch --orphan`) but branches
            # already exist, so there is no commit to branch from. Without this,
            # dulwich falls back to its "HEAD" objectish default and fails deep
            # inside the object store with "Invalid object name b'HEAD'".
            # `existing_branches` is never empty here: an empty one with a None
            # head takes the brand-new-repository path above.
            raise ValueError(
                "Cannot create the branch(es) "
                f"{', '.join(repr(b) for b in missing)} in the local repo at "
                f"'{path}' as HEAD does not point at a commit to branch from. "
                f"Check out an existing branch (e.g. "
                f"`git switch {sorted(existing_branches)[0]}`) "
                "before running this command."
            )
        # Point new branches at HEAD's commit explicitly. Dulwich fails to resolve
        # its default "HEAD" objectish when HEAD is detached, which is the normal
        # state of a colocated Jujutsu repository.
        for branch in missing:
            porcelain.branch_create(repo=path, name=branch, objectish=head)
            logger.info("Created branch '%s' from HEAD", branch)
    _ensure_remote(path, repo, protocol)
    if push:
        for branch in branches:
            logger.info("Pushing branch '%s' to remote '%s'...", branch, repo)
            porcelain.push(
                repo=path,
                remote_location=_remote_url(repo, "https"),
                refspecs=[branch.encode()],
                username="x-access-token",
                password=token,
            )
            logger.info("Successfully pushed branch '%s'.", branch)
    else:
        logger.info("Skipping push (pass --push to push branches to the remote).")
    _add_workflow(path)


def create_github_repo(
    repo: str,
    private: bool,
    is_owner_user: bool,
    dir_: str,
    token: str,
    protocol: str,
    push: bool,
    branches: Sequence[str] = ("main",),
) -> None:
    branches = as_str_sequence(branches)
    token = resolve_github_token(token)
    repo = repo.strip()
    validate_repo(repo)
    _create_remote_repo(
        repo=repo, private=private, token=token, is_owner_user=is_owner_user
    )
    _init_local_repo(
        repo=repo,
        dir_=dir_,
        token=token,
        branches=branches,
        protocol=protocol,
        push=push,
    )


def _add_workflow(path: Path, workflow_dir: Path | None = None) -> None:
    if workflow_dir is None:
        workflow_dir = Path(__file__).parent / "workflows"
    templates = [
        yaml
        for yaml in workflow_dir.glob("*.yaml")
        if yaml.name not in _REPO_ONLY_WORKFLOWS
    ]
    if not templates:
        raise FileNotFoundError(f"No workflow templates found in '{workflow_dir}'.")
    dir_dest = path / ".github" / "workflows"
    dir_dest.mkdir(parents=True, exist_ok=True)
    for yaml in templates:
        if not (dir_dest / yaml.name).exists():
            shutil.copy2(yaml, dir_dest)
            logger.info("Copied workflow %s to %s", yaml.name, dir_dest)


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
        help="Branches to create (and push if --push is set) (default: main).",
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
        "--push",
        dest="push",
        action="store_true",
        help="Push branches to the remote (by default, nothing is pushed).",
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
            is_owner_user=args.is_owner_user,
            dir_=args.dir,
            token=args.token,
            branches=args.branches,
            protocol=args.protocol,
            push=args.push,
        )
    except Exception as e:
        logger.error("Failed to create GitHub repo: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
