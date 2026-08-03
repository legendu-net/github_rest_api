"""Upload an image and post it as a new comment on a GitHub issue.

GitHub has no public API for the attachment upload that its web UI performs on
drag and drop, so an image has to be hosted elsewhere before it can be
referenced from a comment. This script hosts it on Cloudinary through the
Cloudinary CLI (``cld``), which is expected to be authenticated through its own
environment variables.
"""

import json
import logging
import re
import shutil
import subprocess as sp
import sys
from argparse import Namespace
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from github_rest_api import IssueState, Repository
from github_rest_api.scripts.utils import (
    RedactingArgumentParser,
    reject_github_token,
    resolve_github_token,
    validate_repo,
)
from github_rest_api.utils import as_str_sequence

logger = logging.getLogger(__name__)

POPULAR_REPOS = ("dclong/tasks", "legendu-net/blog")
# fzf reports both "nothing matched the query" and "the user walked away" through
# its exit code, and neither is a failure of the command itself.
FZF_EXIT_NO_MATCH = 1
FZF_EXIT_ERROR = 2
FZF_EXIT_ABORT = 130
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SECURE_URL = re.compile(r'"secure_url"\s*:\s*"(?P<url>[^"]+)"')
CLOUDINARY_URL = re.compile(r"https://res\.cloudinary\.com/\S+")
TAGS_HELP = (
    "Tags to attach to the uploaded image. Both comma- and space-delimited "
    "values are accepted and can be mixed, so that --tags holiday,summer and "
    "--tags holiday summer are equivalent, as is --tags 'holiday, summer'. "
    "A tag containing spaces has to be quoted, so that --tags 'my son' beach "
    "attaches the 2 tags 'my son' and 'beach'; a tag cannot contain a comma, "
    "which always separates tags. Surrounding whitespace is trimmed, empty "
    "entries are dropped and duplicates removed (the first occurrence decides "
    "the order). Ignored together with --image-url, since nothing is uploaded "
    "in that case."
)


def parse_tags(tags: str | Sequence[str]) -> list[str]:
    """Split tags on commas into a normalized list.

    Space-delimited tags need no handling here because the shell has already
    split them into separate values. Splitting on whitespace as well would make
    a tag containing a space (``my son``) impossible to express, so quoting a
    value is what marks it as a single tag.

    :param tags: Tags as a bare string or as a sequence of strings, each of
        which may itself hold several comma-delimited tags.
    """
    result: list[str] = []
    for tag in as_str_sequence(tags):
        for part in tag.split(","):
            part = part.strip()
            if part and part not in result:
                result.append(part)
    return result


def _link(response: dict[str, Any]) -> str:
    """Render the web link of a created issue or comment for a log message.

    :param response: An issue or comment as returned by the GitHub API. GitHub
        always carries an ``html_url``, but a message reading ``Created issue
        #7: `` would look truncated if one ever did not.
    """
    url = response.get("html_url", "")
    return f": {url}" if url else "."


def cloudinary_folder(repo: str) -> str:
    """Derive the Cloudinary folder to upload to from a GitHub repository.

    :param repo: A GitHub repository of the form ``owner/repo``, whose name
        (``repo``) is used as the folder.
    """
    return repo.split("/")[-1]


def issue_choices(issues: Iterable[dict[str, Any]]) -> list[str]:
    """Build the ``<number>\\t<title>`` lines to feed to the issue picker.

    GitHub's issues endpoint also returns pull requests, which are dropped here.
    Whitespace is squeezed out of every title so that an issue is always exactly
    one record holding exactly one delimiter.

    :param issues: Issues as returned by ``Repository.get_issues``.
    """
    return [
        f"{issue['number']}\t{' '.join(str(issue.get('title', '')).split())}"
        for issue in issues
        if "pull_request" not in issue
    ]


def _run_fzf(candidates: Sequence[str], *options: str) -> sp.CompletedProcess[str]:
    """Run fzf over the given candidates and return the completed process.

    ``check=True`` is deliberately not used: fzf reports "nothing matched" and
    "the user aborted" through its exit code, and the callers interpret both
    rather than treating them as errors.

    :param candidates: The lines to choose from.
    :param options: Additional command line options for fzf.
    """
    if not shutil.which("fzf"):
        raise FileNotFoundError(
            "fzf is not installed but is needed to select a repository or an "
            "issue interactively. Install fzf, or pass --repo, --issue-number "
            "or --issue-title explicitly."
        )
    # --no-multi is passed explicitly because $FZF_DEFAULT_OPTS is merged into
    # every invocation, and a --multi in there would spread a selection over
    # several lines and silently break the parsing below.
    proc = sp.run(
        ["fzf", "--no-multi", "--height=40%", "--reverse", *options],
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == FZF_EXIT_ERROR:
        raise RuntimeError(
            f"fzf failed: {proc.stderr.strip() or 'unknown error'}. An "
            "interactive terminal is required to select interactively."
        )
    return proc


def parse_fzf_repo(returncode: int, stdout: str) -> str:
    """Interpret the output of the repository picker.

    Given ``--print-query``, fzf prints the typed query as the first line and,
    when an entry was actually selected, that entry as the second one. An exit
    code of 0 therefore means a listed repository was picked, 1 means the query
    matched nothing and is itself the repository to use, and 130 means the
    picker was cancelled, in which case fzf prints nothing at all.

    :param returncode: The exit code of fzf.
    :param stdout: The standard output of fzf.
    """
    if returncode == FZF_EXIT_ABORT:
        raise ValueError("No repository was selected (the picker was cancelled).")
    lines = stdout.splitlines()
    query = lines[0].strip() if lines else ""
    selection = lines[1].strip() if len(lines) > 1 else ""
    if repo := selection or query:
        return repo
    raise ValueError("No repository was selected or entered.")


def parse_fzf_issue(returncode: int, stdout: str) -> int:
    """Interpret the output of the issue picker.

    :param returncode: The exit code of fzf.
    :param stdout: The standard output of fzf, a single ``<number>\\t<title>``
        line since the issue picker does not use ``--print-query``.
    """
    if returncode == FZF_EXIT_ABORT:
        raise ValueError("No issue was selected (the picker was cancelled).")
    line = stdout.strip()
    if not line:
        raise ValueError("No issue was selected.")
    number = line.split("\t", 1)[0].strip()
    if not number.isdigit():
        raise ValueError(f"Could not parse an issue number out of '{line}'.")
    return int(number)


def select_repo() -> str:
    """Select a GitHub repository interactively.

    Any repository can be typed in freely; the popular ones are merely offered.
    Matching is exact rather than fuzzy so that a typed repository is not
    hijacked by a loose match against one of the offered ones.
    """
    proc = _run_fzf(
        POPULAR_REPOS,
        "--print-query",
        "--exact",
        "--prompt=repo> ",
        "--header=Select a repository, or type any owner/repo and press Enter.",
    )
    return parse_fzf_repo(proc.returncode, proc.stdout)


def select_issue(repository: Repository) -> int:
    """Select an open issue of the repository interactively.

    :param repository: The repository whose open issues to choose from.
    """
    choices = issue_choices(repository.get_issues(state=IssueState.OPEN))
    if not choices:
        # Without this, the user would land in an empty picker that can only be
        # escaped with Esc, which then reports a cancellation instead of the
        # real reason.
        raise ValueError(
            "There is no open issue to select. Use --issue-title to create one."
        )
    # accept-non-empty makes Enter a no-op unless something is highlighted, so
    # that free text cannot leak through this picker. --no-print-query guards
    # against a --print-query in $FZF_DEFAULT_OPTS, which would otherwise add
    # the typed query as an extra line and break the parsing.
    proc = _run_fzf(
        choices,
        "--bind=enter:accept-non-empty",
        "--no-print-query",
        "--prompt=issue> ",
        "--header=Select an open issue.",
    )
    return parse_fzf_issue(proc.returncode, proc.stdout)


def _largest_brace_span(text: str) -> str:
    """Return the substring from the first ``{`` to the last ``}``, if any."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else ""


def extract_secure_url(stdout: str) -> str:
    """Extract the ``secure_url`` from the output of ``cld uploader upload``.

    The Cloudinary CLI pretty-prints the raw API response as JSON, so parsing
    the whole output is the primary path. The fallbacks are there because the
    exact format is an implementation detail of the CLI that has changed across
    versions: older ones colorize the JSON even when the output is captured,
    and a future one may prepend progress information.

    :param stdout: The standard output of ``cld uploader upload``.
    """
    text = ANSI_ESCAPE.sub("", stdout).strip()
    if not text:
        raise ValueError(
            "`cld uploader upload` produced no output; the upload probably failed."
        )
    for candidate in (text, _largest_brace_span(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        url = data.get("secure_url", "") if isinstance(data, dict) else ""
        if url:
            return url
        # Fall through rather than give up: a response that parses but carries
        # no top-level `secure_url` (a wrapped or list payload, say) is exactly
        # the kind of variation the scraping below is here to absorb.
    if match := SECURE_URL.search(text):
        return match.group("url")
    if match := CLOUDINARY_URL.search(text):
        return match.group().rstrip('",')
    raise ValueError(
        f"Could not find the uploaded image URL in the output of cld:\n{text[:1000]}"
    )


def build_cld_command(image: Path, folder: str, tags: Sequence[str]) -> list[str]:
    """Build the ``cld uploader upload`` command.

    The upload options are passed as ``key=value`` positionals. Note that the
    quotes in a shell example such as ``tags="a,b"`` are shell quoting only:
    nothing here runs through a shell, so quoting the value would make the
    quotes part of the first and last tag.

    :param image: The local image to upload.
    :param folder: The Cloudinary folder to upload into.
    :param tags: Tags to attach to the uploaded image.
    """
    cmd = ["cld", "uploader", "upload", str(image), f"folder={folder}"]
    if tags:
        cmd.append("tags=" + ",".join(tags))
    return cmd


def upload_image(image: Path, folder: str, tags: Sequence[str]) -> str:
    """Upload a local image to Cloudinary and return its URL.

    :param image: The local image to upload.
    :param folder: The Cloudinary folder to upload into.
    :param tags: Tags to attach to the uploaded image.
    """
    if not image.is_file():
        raise FileNotFoundError(f"The image file '{image}' does not exist.")
    if not shutil.which("cld"):
        raise FileNotFoundError(
            "The Cloudinary CLI (cld) is not installed but is needed to upload "
            "a local image. Install it, or pass --image-url instead."
        )
    logger.info(
        "Uploading '%s' to the Cloudinary folder '%s'%s ...",
        image,
        folder,
        f" with tags {', '.join(tags)}" if tags else "",
    )
    # check=True is avoided on purpose: it would raise a CalledProcessError
    # whose message drops stderr, which is exactly where Cloudinary explains
    # what went wrong.
    proc = sp.run(
        build_cld_command(image.resolve(), folder, tags),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to upload '{image}' to Cloudinary (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    url = extract_secure_url(proc.stdout)
    logger.info("Uploaded the image to %s", url)
    return url


def parse_args(args=None, namespace=None) -> Namespace:
    """Parse command line arguments.

    :param args: The arguments to parse. Defaults to the command line ones.
    :param namespace: An object to take the parsed attributes.
    :return: The parsed arguments.
    """
    parser = RedactingArgumentParser(
        description="Upload an image and post it as a new comment on a GitHub issue."
    )
    parser.add_argument(
        # No short option on purpose: -t next to -T for --issue-title would
        # make a case slip post the token as a public issue title.
        "--token",
        dest="token",
        default="",
        help="The personal access token for authentication. "
        "If not specified, the GITHUB_TOKEN environment variable is used.",
    )
    parser.add_argument(
        "-r",
        "--repo",
        dest="repo",
        default="",
        help="The GitHub repository (of the format owner/repo) to comment on. "
        f"If not specified, one of {', '.join(POPULAR_REPOS)} is selected using "
        "fzf, where any other repository can also be typed in freely.",
    )
    # Not required: with neither of the two specified, an open issue is picked
    # interactively instead.
    group_issue = parser.add_mutually_exclusive_group()
    group_issue.add_argument(
        "-n",
        "--issue-number",
        dest="issue_number",
        type=int,
        default=0,
        help="The number of an existing issue to comment on. "
        "If neither this nor --issue-title is specified, "
        "an open issue is selected using fzf.",
    )
    group_issue.add_argument(
        "-T",
        "--issue-title",
        dest="issue_title",
        # None rather than "" so that an explicitly empty title is told apart
        # from an absent one and rejected instead of opening the issue picker.
        default=None,
        help="The title of a new issue to create and comment on.",
    )
    # Required: there is no way to guess which image is meant.
    group_image = parser.add_mutually_exclusive_group(required=True)
    group_image.add_argument(
        "-p",
        "--image-path",
        "--image",
        dest="image_path",
        default="",
        help="The path of a local image, which is uploaded to Cloudinary "
        "using the Cloudinary CLI (cld).",
    )
    group_image.add_argument(
        "-u",
        "--image-url",
        dest="image_url",
        default="",
        help="The URL of an already uploaded image, which is referenced as is.",
    )
    parser.add_argument(
        "-g", "--tags", dest="tags", nargs="*", default=[], help=TAGS_HELP
    )
    parser.add_argument(
        "-a",
        "--alt",
        dest="alt",
        default="",
        help="Alt text of the image. Defaults to the name of the image.",
    )
    parser.add_argument(
        "-b",
        "--body",
        dest="body",
        default="",
        help="Text to place above the image in the comment.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def upload_image_to_issue(
    token: str,
    repo: str,
    issue_number: int,
    issue_title: str | None,
    image_path: str,
    image_url: str,
    tags: Sequence[str],
    alt: str,
    body: str,
) -> int:
    """Post an image as a new comment on a GitHub issue and return its number.

    The image is uploaded before an issue is created so that a failed upload
    does not leave an empty issue behind, and every interactive step happens
    before anything is created so that a cancelled picker does not leave an
    unreferenced image on Cloudinary.

    :param token: The personal access token for authentication.
    :param repo: The GitHub repository (of the format owner/repo) to comment on.
        If empty, it is selected interactively.
    :param issue_number: The number of an existing issue to comment on.
    :param issue_title: The title of a new issue to create and comment on.
        Used only if no issue number is specified.
    :param image_path: The path of a local image to upload to Cloudinary.
    :param image_url: The URL of an already uploaded image.
        Takes precedence over the local image.
    :param tags: Tags to attach to the uploaded image.
    :param alt: Alt text of the image. Defaults to the name of the image.
    :param body: Text to place above the image in the comment.
    :raises ValueError: If no image, an empty issue title, or a value that
        looks like a GitHub token is specified.
    :raises RuntimeError: If an issue was created but could not be commented
        on, in which case the message carries the number of that issue.
    """
    # Checked before anything is uploaded, created or even authenticated, so
    # that a misplaced token never reaches GitHub or Cloudinary.
    for option, value in (
        ("--repo", repo),
        ("--issue-title", issue_title or ""),
        ("--image-path", image_path),
        ("--image-url", image_url),
        ("--alt", alt),
        ("--body", body),
        *(("--tags", tag) for tag in tags),
    ):
        reject_github_token(option, value)
    # An option given as an empty string satisfies its mutually exclusive group
    # and would otherwise be silently treated as absent, taking a branch the
    # user did not ask for.
    if not image_path.strip() and not image_url.strip():
        raise ValueError("--image-path and --image-url must not be empty.")
    if issue_title is not None and not issue_title.strip():
        raise ValueError("--issue-title must not be empty.")
    token = resolve_github_token(token)
    repo = repo.strip() or select_repo()
    # The picker accepts free text, so a repository typed there has not
    # been through the check above.
    reject_github_token("--repo", repo)
    validate_repo(repo)
    repository = Repository(token=token, repo=repo)
    if not issue_number and not issue_title:
        issue_number = select_issue(repository)
    url = image_url.strip() or upload_image(
        Path(image_path), folder=cloudinary_folder(repo), tags=tags
    )
    created = not issue_number
    if created:
        logger.info("Creating issue '%s' in %s ...", issue_title, repo)
        issue = repository.create_issue(title=str(issue_title))
        issue_number = issue["number"]
        logger.info("Created issue #%s%s", issue_number, _link(issue))
    logger.info("Commenting on issue #%s of %s ...", issue_number, repo)
    try:
        comment = repository.create_issue_comment_image(
            issue_number, url, alt=alt or Path(image_path or url).name, body=body
        )
    except Exception as e:
        if created:
            # The issue exists but holds no image, and its number has not been
            # reported yet, so without this a retry would just create a second
            # empty issue.
            raise RuntimeError(
                f"Created issue #{issue_number} but failed to comment on it: {e}. "
                f"Retry with --issue-number {issue_number}."
            ) from e
        raise
    logger.info("Posted the comment%s", _link(comment))
    return issue_number


def main() -> int:
    args = parse_args()
    # The progress messages are emitted through logging, which the root logger
    # would otherwise suppress. They go to stderr, leaving stdout to the issue
    # number alone.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        issue_number = upload_image_to_issue(
            token=args.token,
            repo=args.repo,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            image_path=args.image_path,
            image_url=args.image_url,
            tags=parse_tags(args.tags),
            alt=args.alt,
            body=args.body,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    # The bare number is the only thing on stdout so that it can be captured
    # with a command substitution and reused.
    print(issue_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
