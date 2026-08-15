"""Add a GitHub repository and initialize a local Git repository with workflows."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import requests
from dulwich import porcelain
from dulwich.refs import HEADREF, LOCAL_BRANCH_PREFIX, Ref

from github_rest_api import Organization, User
from github_rest_api.github import DEFAULT_TIMEOUT, URL_API, build_http_headers
from github_rest_api.scripts.utils import resolve_github_token, validate_repo
from github_rest_api.utils import as_str_sequence

logger = logging.getLogger(__name__)

# Workflows that configure this repository itself rather than the repositories
# created from these templates.
_REPO_ONLY_WORKFLOWS = frozenset({"check-workflow-template.yaml"})

# The default GitHub repo to copy workflow templates from.
DEFAULT_WORKFLOWS_REPO = "legendu-net/github_rest_api"


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
    url = _remote_url(repo, protocol)
    try:
        porcelain.remote_add(path, "origin", url)
        logger.info("Added remote 'origin' for %s", repo)
    except porcelain.RemoteExists:
        pass
    # `porcelain.remote_add` only writes the remote's `url`, unlike `git remote
    # add`/`git clone`, which also set a `fetch` refspec. Without it, `git
    # fetch`/`jj git fetch` never update `refs/remotes/origin/*`, so local
    # branches silently stop tracking the remote. Repair it for a pre-existing
    # 'origin' too (e.g. added by an older version of this script), but only
    # when it already points at this repo, so an unrelated 'origin' remote is
    # left untouched.
    with porcelain.open_repo_closing(path) as r:
        c = r.get_config()
        try:
            existing_url = c.get((b"remote", b"origin"), b"url")
        except KeyError:
            return
        if existing_url != url.encode():
            return
        c.set((b"remote", b"origin"), b"fetch", b"+refs/heads/*:refs/remotes/origin/*")
        c.write_to_path()


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
    workflows_repo: str = DEFAULT_WORKFLOWS_REPO,
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
    _add_workflow(path, workflows_repo=workflows_repo, token=token)


def create_github_repo(
    repo: str,
    private: bool,
    is_owner_user: bool,
    dir_: str,
    token: str,
    protocol: str,
    push: bool,
    branches: Sequence[str] = ("main",),
    workflows_repo: str = DEFAULT_WORKFLOWS_REPO,
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
        workflows_repo=workflows_repo,
    )


def _list_workflow_entries(workflows_repo: str, token: str) -> list[dict]:
    """List the workflow template file entries available in a GitHub repo.

    :param workflows_repo: The GitHub repo (in the format of owner/repo) to
        list workflow templates from.
    :param token: The GitHub token used to authenticate the request.
    """
    url = f"{URL_API}/repos/{workflows_repo}/contents/.github/workflows"
    resp = requests.get(
        url=url, headers=build_http_headers(token), timeout=DEFAULT_TIMEOUT
    )
    resp.raise_for_status()
    return [
        entry
        for entry in resp.json()
        if entry["type"] == "file"
        and entry["name"].endswith(".yaml")
        and entry["name"] not in _REPO_ONLY_WORKFLOWS
    ]


def _download_workflow(workflows_repo: str, entry: dict, token: str) -> str:
    """Download the raw content of a single workflow template.

    Prefers the entry's ``download_url`` (served from
    raw.githubusercontent.com, which has a much higher rate limit than the
    api.github.com contents endpoint used to list the entries), falling back
    to a raw content request against the contents API for the rare case
    where GitHub omits it.

    :param workflows_repo: The GitHub repo (in the format of owner/repo) to
        download the workflow template from.
    :param entry: A file entry as returned by ``_list_workflow_entries``.
    :param token: The GitHub token used to authenticate the request.
    """
    download_url = entry.get("download_url")
    if download_url:
        # No Authorization header here: raw.githubusercontent.com already
        # embeds any access it needs (e.g. a signed token for a private repo)
        # in the URL itself, and forwarding an Authorization header scoped to
        # a *different* repo makes it fail closed with a 404 even for public
        # content.
        resp = requests.get(url=download_url, timeout=DEFAULT_TIMEOUT)
    else:
        url = f"{URL_API}/repos/{workflows_repo}/contents/.github/workflows/{
            entry['name']
        }"
        headers = build_http_headers(token) | {
            "Accept": "application/vnd.github.raw+json"
        }
        resp = requests.get(url=url, headers=headers, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _add_workflow(path: Path, workflows_repo: str, token: str) -> None:
    """Download workflow templates from a GitHub repo into a local repo.

    :param path: The local repo directory to add workflow templates to.
    :param workflows_repo: The GitHub repo (in the format of owner/repo) to
        copy workflow templates from. An empty string skips this step.
    :param token: The GitHub token used to authenticate the requests.
    """
    if not workflows_repo:
        logger.info("Skipping workflow templates (workflows_repo is empty).")
        return
    entries = _list_workflow_entries(workflows_repo, token)
    if not entries:
        raise FileNotFoundError(f"No workflow templates found in '{workflows_repo}'.")
    dir_dest = path / ".github" / "workflows"
    dir_dest.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        dest = dir_dest / entry["name"]
        if not dest.exists():
            dest.write_text(_download_workflow(workflows_repo, entry, token))
            logger.info("Downloaded workflow %s from %s", entry["name"], workflows_repo)


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
        "-w",
        "--workflows-repo",
        dest="workflows_repo",
        default=DEFAULT_WORKFLOWS_REPO,
        help="The GitHub repo (in the format of owner/repo) to copy workflow "
        f"templates from (default: {DEFAULT_WORKFLOWS_REPO}). "
        "Pass an empty string to skip copying workflow templates.",
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
            workflows_repo=args.workflows_repo,
        )
    except Exception as e:
        logger.error("Failed to create GitHub repo: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
