from github_rest_api.github import MergeMethod
from github_rest_api.scripts.github import auto_merge_pull_request


def test_parse_args_merge_method_is_an_enum_member():
    """`--merge-method` is converted, so the namespace holds a MergeMethod.

    `is` rather than `==`: a plain string compares equal to a StrEnum member,
    so `==` would still pass if the conversion were dropped.
    """
    args = auto_merge_pull_request.parse_args(["--token", "t"])
    assert args.merge_method is MergeMethod.MERGE
    args = auto_merge_pull_request.parse_args(
        ["--token", "t", "--merge-method", "squash"]
    )
    assert args.merge_method is MergeMethod.SQUASH
