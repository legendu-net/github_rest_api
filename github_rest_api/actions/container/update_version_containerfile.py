import argparse
import datetime
import os
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
    if containerfile == "":
        containerfile = "Dockerfile" if Path("Dockerfile").exists() else "Containerfile"
    if isinstance(containerfile, str):
        containerfile = Path(containerfile).resolve()
    match containerfile.parent.name:
        case "docker-base":
            return _update_version_docker_base(
                containerfile=containerfile, version=version
            )
        case "docker-jupyterlab":
            return _update_version_docker_jupyterlab(
                containerfile=containerfile, version=version
            )
        case "docker-jupyterhub":
            return _update_version_docker_jupyterhub(
                containerfile=containerfile, version=version
            )
        case "docker-vscode-server":
            return _update_version_docker_vscode_server(
                containerfile=containerfile, version=version
            )
        case _:
            if not pattern:
                raise ValueError("A version pattern must be specified!")
            return _update_version_default(
                containerfile=containerfile,
                version=version,
                pattern=pattern,
                replace=replace,
            )


def _update_version_default(
    containerfile: Path, version: str, pattern: str, replace: str
) -> None:
    text = containerfile.read_text()
    text = re.sub(pattern, replace.format(version=version), text)
    containerfile.write_text(text)


def _update_version_docker_base(containerfile: Path, version: str) -> None:
    _update_version_default(
        containerfile=containerfile,
        version=version,
        pattern=r"-v v?\d+\.\d+\.\d+",
        replace="-v v{version}",
    )


def _update_version_docker_jupyterlab(containerfile: Path, version: str) -> None:
    version = next_minor_or_strip_patch(version, 3)
    _update_version_default(
        containerfile=containerfile,
        version=version,
        pattern=r",<\d+\.\d+\.0",
        replace=",<{version}",
    )


def _update_version_docker_jupyterhub(containerfile: Path, version: str) -> None:
    version = next_minor_or_strip_patch(version, 3)
    _update_version_default(
        containerfile=containerfile,
        version=version,
        pattern=r"jupyterhub<\d+\.\d+\.0",
        replace="jupyterhub<{version}",
    )


def _update_version_docker_vscode_server(containerfile: Path, version: str) -> None:
    version = next_minor_or_strip_patch(version, 3)
    _update_version_default(
        containerfile=containerfile,
        version=version,
        pattern=r",<\d+\.\d+\.0",
        replace=",<{version}",
    )


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
        default="",
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
        default="",
        help="The version pattern to replace.",
    )
    parser.add_argument(
        "--replace",
        dest="replace",
        default="",
        help="The replacement for the matched version pattern.",
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


def main():
    args = parse_args()
    if has_open_pr(head_prefix=_branch_prefix(args.repo)):
        return
    checkout_branch(args.repo)
    version = parse_latest_version(repo=args.repo)
    update_version(
        containerfile=args.containerfile,
        version=version,
        pattern=args.pattern,
        replace=args.replace,
    )
    push_changes(repo=args.repo, token=args.token)


if __name__ == "__main__":
    main()
