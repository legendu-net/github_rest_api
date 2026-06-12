"""Create a PR from the specified branch to dev.
The branch is updated (using dev) before creating the PR.
"""

from argparse import ArgumentParser, Namespace
import os
import sys
from github_rest_api import Repository
from github_rest_api.utils import compile_patterns


def parse_args(args=None, namespace=None) -> Namespace:
    """Parse command-line arguments.
    :param args: The arguments to parse.
        If None, the arguments from command-line are parsed.
    :param namespace: An inital Namespace object.
    :return: A namespace object containing parsed options.
    """
    parser = ArgumentParser(description="Create pull requests to the dev branch.")
    parser.add_argument(
        "--token",
        dest="token",
        required=True,
        help="The personal access token for authentication.",
    )
    parser.add_argument(
        "--head-branch",
        dest="head_branch",
        required=True,
        help="The head branch containing changes to merge.",
    )
    parser.add_argument(
        "--base-branch",
        dest="base_branch",
        required=True,
        help="The base branch to merge changes into.",
    )
    parser.add_argument(
        "--ignore-patterns",
        dest="ignore_patterns",
        nargs="*",
        default=["^_"],
        help="A list of regular expression patterns. Branches matching any of these patterns will be ignored.",
    )
    parser.add_argument(
        "--update",
        dest="update",
        action="store_true",
        help="Update the head branch using the base branch before creating the pull request.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def main() -> int:
    """Main entrance of the script,
    which creates a PR from the specified branch to dev.
    The branch is updated (using dev) before creating the PR.
    """
    args = parse_args()
    # skip branches matching any of the ignore patterns
    try:
        compiled = compile_patterns(args.ignore_patterns)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    for pattern in compiled:
        if pattern.search(args.head_branch):
            print(
                f"Branch '{args.head_branch}' matches ignore pattern '{
                    pattern.pattern
                }', skipping."
            )
            return 0
    repo = Repository(args.token, os.environ["GITHUB_REPOSITORY"])
    if args.update:
        repo.update_branch(update=args.head_branch, upstream=args.base_branch)
    repo.create_pull_request(
        {
            "base": args.base_branch,
            "head": args.head_branch,
            "title": f"Merge {args.head_branch} Into {args.base_branch}",
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
