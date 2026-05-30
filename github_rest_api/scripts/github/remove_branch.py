import argparse
import re
import sys
import datetime
from github_rest_api import Repository


def parse_args(args=None, namespace=None):
    parser = argparse.ArgumentParser(
        description="Remove branches matching the specified pattern from a GitHub repository."
    )
    parser.add_argument(
        "--token",
        dest="token",
        required=True,
        help="The personal access token for authentication.",
    )
    parser.add_argument(
        "--repo",
        dest="repo",
        required=True,
        help="The GitHub repository (of the format owner/repo) from which to remove branches.",
    )
    parser.add_argument(
        "--pattern",
        dest="pattern",
        required=True,
        help="A regular expression pattern. Branches matching this regex pattern will be removed.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def get_active_branches(repository: Repository) -> set[str]:
    """Retrieve all branches currently involved in open pull requests."""
    open_prs = repository.get_pull_requests()
    branches_in_prs = set()
    for pr in open_prs:
        branches_in_prs.add(pr["head"]["ref"])
        branches_in_prs.add(pr["base"]["ref"])
    return branches_in_prs


def is_branch_old_enough(
    repository: Repository, branch_name: str, hours: int = 24
) -> bool:
    """Check if the branch has been created/active for at least the specified number of hours."""
    branch_info = repository.get_branch(branch_name)

    created_at_str = (
        branch_info.get("commit", {}).get("commit", {}).get("committer", {}).get("date")
    )

    if not created_at_str:
        print(f"Could not determine creation time for branch {branch_name}. Skipping.")
        return False

    created_at = datetime.datetime.fromisoformat(created_at_str)
    now = datetime.datetime.now(datetime.timezone.utc)

    return (now - created_at) >= datetime.timedelta(hours=hours)


def remove_branch(token: str, repo: str, pattern: str) -> None:
    """Remove branches matching the specified pattern from the GitHub repository.

    :param token: The personal access token for authentication.
    :param repo: The GitHub repository (of the format owner/repo) from which to remove branches.
    :param pattern: A regular expression pattern. Branches matching this regex pattern will be removed.
    """
    repository = Repository(token=token, repo=repo)
    branches = repository.get_branches()
    active_branches = get_active_branches(repository)
    regex = re.compile(pattern)

    for branch in branches:
        branch_name = branch["name"]
        if not regex.search(branch_name):
            print(
                f"Skipping branch {branch_name} as it doesn't match the pattern '{
                    pattern
                }'."
            )
            continue

        if branch_name in active_branches:
            print(f"Skipping branch {branch_name} as it is involved in an active PR.")
            continue

        if not is_branch_old_enough(repository, branch_name, hours=24):
            print(f"Skipping branch {branch_name} as it is less than 24 hours old.")
            continue

        print(f"Removing branch: {branch_name} ...")
        repository.delete_branch(branch_name)


def main() -> int:
    args = parse_args()
    try:
        remove_branch(token=args.token, repo=args.repo, pattern=args.pattern)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
