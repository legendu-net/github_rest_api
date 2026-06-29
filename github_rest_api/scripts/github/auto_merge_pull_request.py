"""Auto-merge eligible open pull requests in the repository.

Every open PR is evaluated by ``Repository.auto_merge_pull_requests`` and merged
only when it passes the eligibility gate (author allowlist, title type, head
commit age, mergeable state and an approving review or marker comment).
"""

import logging
import os
import sys
from argparse import ArgumentParser, Namespace

from github_rest_api import MergeMethod, Repository
from github_rest_api.github import (
    DEFAULT_AUTO_MERGE_MARKER,
    DEFAULT_AUTO_MERGE_TYPES,
    DEFAULT_MIN_AGE_MINUTES,
)


def parse_args(args=None, namespace=None) -> Namespace:
    """Parse command-line arguments.
    :param args: The arguments to parse.
        If None, the arguments from command-line are parsed.
    :param namespace: An inital Namespace object.
    :return: A namespace object containing parsed options.
    """
    parser = ArgumentParser(description="Auto-merge eligible open pull requests.")
    parser.add_argument(
        "--token",
        dest="token",
        required=True,
        help="The personal access token for authentication.",
    )
    parser.add_argument(
        "--authors",
        dest="authors",
        nargs="*",
        default=[],
        help="Logins whose PRs are eligible for auto-merge. An empty allowlist "
        "makes nothing eligible (fail-safe).",
    )
    parser.add_argument(
        "--approvers",
        dest="approvers",
        nargs="*",
        default=[],
        help="Logins whose reviews or marker comments grant approval.",
    )
    parser.add_argument(
        "--allowed-types",
        dest="allowed_types",
        nargs="*",
        default=list(DEFAULT_AUTO_MERGE_TYPES),
        help="Conventional-Commits title types eligible for auto-merge.",
    )
    parser.add_argument(
        "--min-age-minutes",
        dest="min_age_minutes",
        type=int,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="The minimum head-commit age in minutes; set to 0 to disable the guard.",
    )
    parser.add_argument(
        "--marker",
        dest="marker",
        default=DEFAULT_AUTO_MERGE_MARKER,
        help="The marker substring an approver may comment to approve.",
    )
    parser.add_argument(
        "--merge-method",
        dest="merge_method",
        choices=[m.value for m in MergeMethod],
        default=MergeMethod.MERGE.value,
        help="The merge method to use.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Log the PRs that would be merged without merging them.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def main() -> int:
    """Main entrance of the script,
    which auto-merges every eligible open pull request in the repository.
    """
    args = parse_args()
    # auto_merge_pull_requests reports its outcomes (including the dry-run
    # listing) via logging.info, which the root logger suppresses by default;
    # configure logging so those messages are actually emitted.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    repo = Repository(args.token, os.environ["GITHUB_REPOSITORY"])
    repo.auto_merge_pull_requests(
        authors=args.authors,
        approvers=args.approvers,
        allowed_types=args.allowed_types,
        min_age_minutes=args.min_age_minutes,
        marker=args.marker,
        merge_method=args.merge_method,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
