import os
import re
import sys
import argparse
import getpass
from pathlib import Path
from github_rest_api import Repository
from github_rest_api.scripts.utils import (
    find_project_root,
    get_project_version,
    get_repo,
)


def _get_release_tag(tag: str, root: Path, validate: bool = True) -> str:
    tag = tag.strip()
    if not tag:
        tag = get_project_version(root).strip()
        if not tag:
            raise ValueError(
                "Could not find project version to use as tag. Please specify a tag."
            )

    if not validate:
        return tag

    # matches v?X.Y.Z and optionally pre-releases (e.g., v1.2.3-alpha.1) or build metadata
    semver_pattern = r"v?\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?"
    if not re.fullmatch(semver_pattern, tag):
        raise ValueError(
            f"Tag '{
                tag
            }' is not in a semantic versioning format. Use --no-validate to skip."
        )
    normalized = tag if tag.startswith("v") else f"v{tag}"
    if normalized != tag:
        print(f"Tag normalized from '{tag}' to '{normalized}'.")
    return normalized


def release_on_github(
    token: str, branch: str, tag: str = "", notes: str = "", validate: bool = True
) -> None:
    """Make a release of the project on GitHub.

    The command should be run under the root or a sub-dir of the project.

    :param branch: The branch from which to make the release.
    :param tag: The tag for the release.
        If not specified, the version from project configuration (pyproject.toml, Cargo.toml, etc) is used.
    :param notes: Notes for the release.
        If not specified, it's auto generated.
    :param token: GitHub token.
        If not specified, the GITHUB_TOKEN environment variable is used.
    :param validate: If True, validate the tag against semantic versioning format before creating the release.
    """
    root = find_project_root()
    if not root:
        raise FileNotFoundError("Could not find project root (no .git found).")

    tag = _get_release_tag(tag, root, validate=validate)

    repo_name = get_repo(root)
    if not repo_name:
        raise ValueError("Could not find GitHub repository name.")

    token = token or os.getenv("GITHUB_TOKEN", "")
    if not token:
        token = getpass.getpass("Please enter your GitHub token: ")
        if not token:
            raise ValueError(
                "No GitHub token is provided (via $GITHUB_TOKEN, --token or at prompt)."
            )
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
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Skip release tag format validation and normalization. Use it exactly as provided.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def main() -> int:
    args = parse_args()
    try:
        release_on_github(
            token=args.token,
            branch=args.branch,
            tag=args.tag,
            notes=args.notes,
            validate=args.validate,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
