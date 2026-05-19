import os
import sys
import argparse
import tomllib
import getpass
from pathlib import Path
from dulwich.repo import Repo
from dulwich.errors import NotGitRepository
from github_rest_api import Repository


def find_root(path: Path | None = None) -> Path | None:
    if path is None:
        path = Path.cwd()
    while path != path.parent:
        if (path / ".git").exists():
            return path
        path = path.parent
    return None


def get_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
            return data.get("project", {}).get("version", "")
    cargo = root / "Cargo.toml"
    if cargo.exists():
        with cargo.open("rb") as f:
            data = tomllib.load(f)
            return data.get("package", {}).get("version", "")
    return ""


def get_repo(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
            repo_url = data.get("project", {}).get("urls", {}).get("Repository")
            if repo_url:
                return (
                    repo_url.split("github.com/")[-1].removesuffix(".git").rstrip("/")
                )
    # Fallback to git remote
    try:
        repo = Repo(root)
        config = repo.get_config()
        output = config.get((b"remote", b"origin"), b"url").decode().strip()
        if "github.com" in output:
            if output.startswith("git@"):
                return output.split("github.com:")[-1].replace(".git", "").rstrip("/")
            return output.split("github.com/")[-1].replace(".git", "").rstrip("/")
    except (NotGitRepository, KeyError):
        pass
    return None


def parse_args(args=None, namespace=None):
    parser = argparse.ArgumentParser(
        description="Make a release of the project on GitHub."
    )
    parser.add_argument(
        "-b",
        "--branch",
        default="main",
        help="The branch (default to main) from which to make the release.",
    )
    parser.add_argument(
        "-t",
        "--tag",
        default="",
        help="The tag for the release. If not specified, the version from project configuration is used.",
    )
    parser.add_argument(
        "-n",
        "--notes",
        default="",
        help="Notes for the release. If not specified, it's auto generated.",
    )
    parser.add_argument(
        "--token",
        default="",
        help="GitHub token. If not specified, the GITHUB_TOKEN environment variable is used.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def release_on_github(token: str, branch: str, tag: str = "", notes: str = "") -> None:
    """Make a release of the project on GitHub.

    The command should be run under the root or a sub-dir of the project.

    :param branch: The branch from which to make the release.
    :param tag: The tag for the release.
        If not specified, the version from project configuration (pyproject.toml, Cargo.toml, etc) is used.
    :param notes: Notes for the release.
        If not specified, it's auto generated.
    :param token: GitHub token.
        If not specified, the GITHUB_TOKEN environment variable is used.
    """
    token = token or os.getenv("GITHUB_TOKEN", "")
    if not token:
        token = getpass.getpass("Please enter your GitHub token: ")
        if not token:
            sys.exit(
                "No GitHub token is provided (via $GITHUB_TOKEN, --token or at prompt)."
            )
    root = find_root()
    if not root:
        sys.exit("Could not find project root (no .git found).")

    if not tag:
        tag = get_version(root)
        if not tag:
            sys.exit(
                "Could not find project version to use as tag. Please specify a tag."
            )

    repo_name = get_repo(root)
    if not repo_name:
        sys.exit("Could not find GitHub repository name.")

    repo = Repository(token=token, repo=repo_name)
    data = {
        "tag_name": tag,
        "target_commitish": branch,
        "name": tag,
        "body": notes,
        "draft": False,
        "prerelease": False,
        "generate_release_notes": not notes,
    }
    repo.create_release(data)
    print(f"Successfully created release {tag} on {repo_name}.")


def main():
    args = parse_args()
    release_on_github(
        token=args.token, branch=args.branch, tag=args.tag, notes=args.notes
    )


if __name__ == "__main__":
    main()
