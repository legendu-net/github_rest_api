import argparse
import datetime
import os
import sys
from pathlib import Path
import re
from dulwich import porcelain
from github_rest_api import Repository
from github_rest_api.utils import next_minor_or_strip_patch
from requests.exceptions import HTTPError


def parse_latest_version(repo: str) -> str:
    r = Repository(token="", repo=repo)
    try:
        release = r.get_release_latest()
        version = release["tag_name"]
    except HTTPError as err:
        if err.response is not None and err.response.status_code == 404:
            tags = r.get_tags(n=1)
            version = tags[0]["name"]
        else:
            raise err
    version = version.replace("v", "")
    print(f"The latest version of {repo} is v{version}.")
    return version


def update_version(
    containerfile: str | Path, version: str, pattern: str, replace: str
) -> None:
    if not pattern:
        raise ValueError("The pattern to replace must not be empty.")
    if isinstance(containerfile, str):
        containerfile = Path(containerfile)
    original = containerfile.read_text(encoding="utf-8")
    repl = replace.replace("{version}", version)
    updated = re.sub(pattern, lambda _: repl, original)
    if updated != original:
        containerfile.write_text(updated, encoding="utf-8")


def _branch_prefix(repo: str) -> str:
    return repo.replace("/", "_") + "_version"


def push_changes(repo: str, token: str):
    if not porcelain.status().unstaged:
        print("No changes!")
        return
    porcelain.add()
    porcelain.commit(message=f"update version of {repo}")
    gh_repo = os.environ["GITHUB_REPOSITORY"]
    porcelain.push(
        repo=".",
        remote_location=f"https://github.com/{gh_repo}.git",
        username="x-access-token",
        password=token,
    )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Update the version of a package in a Dockerfile or Containerfile."
    )
    parser.add_argument(
        "--containerfile",
        dest="containerfile",
        required=True,
        help="The Dockerfile or Containerfile to update.",
    )
    parser.add_argument(
        "--token",
        dest="token",
        required=True,
        help="A GitHub token for the repo to be updated.",
    )
    parser.add_argument(
        "--repo",
        dest="repo",
        required=True,
        help="The GitHub repo (in the format of owner/repo) whose release versions are watched.",
    )
    parser.add_argument(
        "--pattern",
        dest="pattern",
        required=True,
        help="The version pattern to replace.",
    )
    parser.add_argument(
        "--replace",
        dest="replace",
        required=True,
        help="The replacement for the matched version pattern.",
    )
    parser.add_argument(
        "--next-minor-or-strip-patch",
        dest="next_minor_or_strip_patch",
        type=int,
        default=None,
        help=(
            "If provided, transform the version with next_minor_or_strip_patch(version, N): "
            "bump to next minor if patch >= N, otherwise strip patch to 0."
        ),
    )
    return parser.parse_args()


def has_open_pr(head_prefix: str) -> bool:
    """Check if there's an open PR whose head starts with head_prefix.

    :param head_prefix: The prefix of head to check for.
    """
    prs = Repository(token="", repo=os.environ["GITHUB_REPOSITORY"]).get_pull_requests()
    for pr in prs:
        if pr["head"]["ref"].startswith(head_prefix):
            return True
    return False


def checkout_branch(repo: str):
    branch = _branch_prefix(repo) + datetime.date.today().strftime("_%Y%m%d")
    porcelain.branch_create(repo=".", name=branch)
    porcelain.checkout(repo=".", target=branch)


def main() -> int:
    args = parse_args()
    if has_open_pr(head_prefix=_branch_prefix(args.repo)):
        return 0
    checkout_branch(args.repo)
    version = parse_latest_version(repo=args.repo)
    if args.next_minor_or_strip_patch is not None:
        version = next_minor_or_strip_patch(version, args.next_minor_or_strip_patch)
    update_version(
        containerfile=args.containerfile,
        version=version,
        pattern=args.pattern,
        replace=args.replace,
    )
    push_changes(repo=args.repo, token=args.token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
