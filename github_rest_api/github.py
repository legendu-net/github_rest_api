"""Simple wrapper of GitHub REST APIs."""

import logging
import re
import time
from abc import ABCMeta, abstractmethod
from base64 import b64encode
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, TypeGuard
from urllib.parse import quote

import requests
from nacl import encoding, public

from github_rest_api.pr_content import (
    deterministic_body,
    deterministic_title,
    generate_pr_content,
)

logger = logging.getLogger(__name__)

URL_API = "https://api.github.com"

# Default connect/read timeout (in seconds) for a GitHub REST API request. It is a
# per-socket-operation deadline rather than a total-transfer one, so a slow but
# progressing response is not cut off. Callers may pass their own `timeout` to any
# of the request helpers, but never `None`: see `_apply_timeout`.
DEFAULT_TIMEOUT = 10

# Default LiteLLM 'provider/model' used to generate PR titles and descriptions.
DEFAULT_PR_MODEL = "anthropic/claude-haiku-4-5-20251001"

# Defaults for automatic pull request merging (see Repository.auto_merge_pull_requests).
# A marker comment from an allowlisted approver is accepted as an approval signal
# for AI reviewers that only comment instead of submitting a formal review.
DEFAULT_AUTO_MERGE_MARKER = "AUTO_MERGE_APPROVED"
# Conventional-commit title types eligible for auto-merge.
DEFAULT_AUTO_MERGE_TYPES = ("build", "chore", "ci", "docs", "deps")
# A PR is only auto-merged once its head commit is at least this old, an anti-race
# guard so a PR that momentarily reads `clean` before CI registers is not merged.
# A just-created PR is simply deferred to the next run, never dropped, so a small
# margin over the CI-registration latency suffices; set to 0 to disable the guard.
DEFAULT_MIN_AGE_MINUTES = 2


def _validate_secret_name(name: str) -> None:
    """Validate a secret name against GitHub's naming rules.

    GitHub rejects invalid secret names with a 422 response. Validating the
    name client-side surfaces a clear error before the request is sent.

    :param name: The name of the secret.
    :raises ValueError: If the name is empty, starts with the reserved
        ``GITHUB_`` prefix, starts with a digit, or contains characters other
        than alphanumerics and underscores.
    """
    if not name:
        raise ValueError("A secret name must not be empty.")
    if name.upper().startswith("GITHUB_"):
        raise ValueError(
            f"Invalid secret name {name!r}: names must not start with the "
            "reserved 'GITHUB_' prefix."
        )
    if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(
            f"Invalid secret name {name!r}: names may only contain alphanumeric "
            "characters and underscores, and must not start with a digit."
        )


def _apply_timeout(kwargs: dict[str, Any]) -> None:
    """Apply the default request timeout in place, rejecting an infinite one.

    `requests` treats `timeout=None` as "wait forever", which can hang a caller
    indefinitely on an unresponsive endpoint. No GitHub REST API call is worth
    that, so `None` is rejected rather than silently replaced: a caller that
    passed it meant to disable the deadline and should say what it wants instead.

    :param kwargs: Keyword arguments destined for a `requests` call. A `timeout`
        entry is added when absent; any other entry is left untouched.
    :raises ValueError: If `timeout` is explicitly None.
    """
    if kwargs.setdefault("timeout", DEFAULT_TIMEOUT) is None:
        raise ValueError(
            "timeout=None would wait indefinitely; pass a positive number of "
            f"seconds, or omit timeout to use the default of {DEFAULT_TIMEOUT}."
        )


def _encrypt_secret(public_key: str, value: str) -> str:
    """Encrypt a secret value using a LibSodium sealed box.
    :param public_key: The base64-encoded public key to encrypt against.
    :param value: The plaintext secret value to encrypt.
    """
    pkey = public.PublicKey(public_key.encode(), encoding.Base64Encoder)
    encrypted = public.SealedBox(pkey).encrypt(value.encode())
    return b64encode(encrypted).decode()


def build_http_headers(token: str) -> dict[str, str]:
    """Build headers for GitHub REST APIs.
    :param token: The authentication token for GitHub REST APIs.
    """
    headers = {
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_rust(file: str) -> bool:
    path = Path(file)
    if path.name in ("Cargo.toml", "Cargo.lock"):
        return True
    if path.suffix == ".rs":
        return True
    return False


def _login(obj: dict[str, Any]) -> str | None:
    """Return the author login of a GitHub object (PR, review, comment), or None.

    The ``user`` field can be absent or null (e.g. a ghost/deleted account), so
    it is guarded before reading ``login``.

    :param obj: A GitHub object carrying an optional ``user`` sub-object.
    """
    return (obj.get("user") or {}).get("login")


def _normalized_logins(logins: Sequence[str]) -> set[str]:
    """Lower-case GitHub logins into a set for case-insensitive membership tests.

    GitHub logins are case-insensitive, so normalizing the allowlist once (rather
    than per candidate) lets repeated membership tests compare against a stable
    lower-cased set.

    :param logins: The logins eligible for a gated action.
    """
    return {login.lower() for login in logins}


def _in_allowlist(login: str | None, allowlist: set[str]) -> TypeGuard[str]:
    """Check whether a GitHub login is in a normalized allowlist.

    ``allowlist`` must already be lower-cased (see :func:`_normalized_logins`).
    The login is lower-cased here before comparison, so casing differences
    between the configured allowlist and the API response do not cause spurious
    mismatches. A ``None`` login (e.g. a ghost/deleted account) is never a member.

    :param login: The login to test, or None.
    :param allowlist: A lower-cased set of logins eligible for the gated action.
    """
    return login is not None and login.lower() in allowlist


# Matches the leading Conventional-Commits type token of a title, e.g. the
# `chore` in "chore(deps): bump x" or "feat!: ...".
_CONVENTIONAL_TYPE = re.compile(r"^(\w+)(\([^)]*\))?!?:")


def _conventional_type(title: str) -> str | None:
    """Extract the lower-cased Conventional-Commits type token from a PR title.

    :param title: The pull request title.
    :return: The type token (e.g. ``chore``) lower-cased, or None when the title
        does not start with a Conventional-Commits type prefix.
    """
    match = _CONVENTIONAL_TYPE.match(title)
    return match.group(1).lower() if match else None


def _title_type_allowed(title: str, allowed_types: Sequence[str]) -> bool:
    """Check whether a PR title's Conventional-Commits type is auto-merge eligible.

    :param title: The pull request title.
    :param allowed_types: The Conventional-Commits types eligible for auto-merge.
    """
    type_ = _conventional_type(title)
    return type_ is not None and type_ in allowed_types


def _field_gate_failure(
    pr: dict[str, Any], authors: Sequence[str], allowed_types: Sequence[str]
) -> str | None:
    """Return why a PR fails the field-only auto-merge gates, or None if it passes.

    These gates (not a draft, trusted author, eligible title type) read only
    fields carried by both the list and single-PR payloads, so they can filter
    list items before the more expensive single-PR detail is fetched.

    :param pr: A PR payload (a list item or full detail).
    :param authors: Logins whose PRs are eligible for auto-merge.
    :param allowed_types: Conventional-Commits title types eligible for auto-merge.
    """
    if pr.get("draft"):
        return "it is a draft"
    if not _in_allowlist(_login(pr), _normalized_logins(authors)):
        return "its author is not in the allowlist"
    title = pr.get("title") or ""
    if not _title_type_allowed(title, allowed_types):
        type_ = _conventional_type(title)
        current = f"'{type_}'" if type_ is not None else "missing/unrecognized"
        eligible = ", ".join(allowed_types) or "none"
        return (
            f"its title type ({current}) is not auto-merge eligible "
            f"(eligible types: {eligible})"
        )
    return None


def _approver_review_states(
    reviews: Sequence[dict[str, Any]], approvers: Sequence[str]
) -> set[str]:
    """Return the set of latest decisive review states among allowlisted reviewers.

    Only the latest decisive review per reviewer is kept. ``APPROVED``,
    ``CHANGES_REQUESTED`` and ``DISMISSED`` are decisive (so a later
    ``DISMISSED`` clears an earlier approval), while ``COMMENTED`` and
    ``PENDING`` are ignored (a comment does not revoke an approval).

    Returning the set (rather than a single approved/not-approved boolean) lets
    the caller treat ``CHANGES_REQUESTED`` as a veto independently of how
    approval is granted, so a marker comment cannot override a standing change
    request.

    :param reviews: Reviews as returned by ``get_pull_request_reviews``.
    :param approvers: Logins whose reviews are trusted for auto-merge.
    """
    approver_set = _normalized_logins(approvers)
    # Reviews are returned in chronological order, so a later decisive entry
    # overrides an earlier one for the same reviewer.
    latest: dict[str, str] = {}
    for review in reviews:
        login = _login(review)
        state = review.get("state")
        if _in_allowlist(login, approver_set) and state in (
            "APPROVED",
            "CHANGES_REQUESTED",
            "DISMISSED",
        ):
            latest[login.lower()] = state
    return set(latest.values())


def _marker_approved(
    comments: Sequence[dict[str, Any]],
    approvers: Sequence[str],
    marker: str,
    since: datetime,
) -> bool:
    """Check whether an allowlisted approver marked the current head for merge.

    Only comments created after ``since`` (the head commit's timestamp) count: a
    marker left before the current head commit approved an older revision, so
    honouring it would auto-merge newer, unreviewed changes.

    :param comments: Issue comments as returned by ``get_issue_comments``.
    :param approvers: Logins whose marker comments are trusted for auto-merge.
    :param marker: The marker substring signalling approval.
    :param since: The head commit's timestamp; earlier comments are ignored.
    """
    approver_set = _normalized_logins(approvers)
    return any(
        _in_allowlist(_login(comment), approver_set)
        and marker in (comment.get("body") or "")
        and _parse_iso(comment["created_at"]) > since
        for comment in comments
    )


def _parse_iso(timestamp: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware ``datetime``.

    A naive timestamp (no offset) is assumed to be UTC.

    :param timestamp: An ISO-8601 timestamp (e.g. ``2026-06-27T12:00:00Z``).
    """
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _old_enough(commit_iso: str, min_age_minutes: int) -> bool:
    """Check whether a commit timestamp is at least ``min_age_minutes`` old.

    :param commit_iso: An ISO-8601 UTC timestamp (e.g. ``2026-06-27T12:00:00Z``).
    :param min_age_minutes: The minimum age in minutes.
    """
    committed = _parse_iso(commit_iso)
    return datetime.now(timezone.utc) - committed >= timedelta(minutes=min_age_minutes)


class GitHub:
    def __init__(self, token: str):
        self._token = token
        self._headers = build_http_headers(token)

    def _get(
        self, url: str, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        """Send a GET request to a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param raise_for_status: Whether to raise on a non-2xx response.
        :param kwargs: Additional keyword arguments (e.g. `params`) forwarded
            to `requests.get`. `timeout` defaults to `DEFAULT_TIMEOUT`
            seconds and must not be None (see `_apply_timeout`).
        """
        _apply_timeout(kwargs)
        resp = requests.get(
            url=url,
            headers=self._headers,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _post(
        self, url: str, headers=None, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        """Send a POST request to a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param headers: Request headers; defaults to the standard auth headers.
        :param raise_for_status: Whether to raise on a non-2xx response.
        :param kwargs: Additional keyword arguments (e.g. `json`) forwarded
            to `requests.post`. `timeout` defaults to `DEFAULT_TIMEOUT`
            seconds and must not be None (see `_apply_timeout`).
        """
        if headers is None:
            headers = self._headers
        _apply_timeout(kwargs)
        resp = requests.post(
            url=url,
            headers=headers,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _delete(
        self, url, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        """Send a DELETE request to a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param raise_for_status: Whether to raise on a non-2xx response.
        :param kwargs: Additional keyword arguments (e.g. `json`) forwarded
            to `requests.delete`. `timeout` defaults to `DEFAULT_TIMEOUT`
            seconds and must not be None (see `_apply_timeout`).
        """
        _apply_timeout(kwargs)
        resp = requests.delete(
            url=url,
            headers=self._headers,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _put(
        self, url: str, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        """Send a PUT request to a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param raise_for_status: Whether to raise on a non-2xx response.
        :param kwargs: Additional keyword arguments (e.g. `json`) forwarded
            to `requests.put`. `timeout` defaults to `DEFAULT_TIMEOUT`
            seconds and must not be None (see `_apply_timeout`).
        """
        _apply_timeout(kwargs)
        resp = requests.put(
            url=url,
            headers=self._headers,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _patch(self, url, raise_for_status: bool = True, **kwargs) -> requests.Response:
        """Send a PATCH request to a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param raise_for_status: Whether to raise on a non-2xx response.
        :param kwargs: Additional keyword arguments (e.g. `json`) forwarded
            to `requests.patch`. `timeout` defaults to `DEFAULT_TIMEOUT`
            seconds and must not be None (see `_apply_timeout`).
        """
        _apply_timeout(kwargs)
        resp = requests.patch(
            url=url,
            headers=self._headers,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _extract_all(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        n: int = 0,
        key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Collect all paginated items from a GitHub REST API endpoint.
        :param url: The endpoint URL to request.
        :param params: Query parameters forwarded to each request.
        :param n: The maximum number of items to return (0 means all).
        :param key: The key under which the list is nested in the response.
            Defaults to None for endpoints that return a plain JSON array.
        """
        params = params.copy() if params else {}
        if "per_page" not in params:
            params["per_page"] = min(100, n) if n > 0 else 100
        params["page"] = 1
        res = []
        while True:
            resp = self._get(url=url, params=params.copy())
            resp.raise_for_status()
            data = resp.json()
            items = data if key is None else data[key]
            res.extend(items)
            if n and len(res) >= n:
                return res[:n]
            if len(items) < params["per_page"]:
                return res
            params["page"] += 1


class IssueState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class MergeMethod(StrEnum):
    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"


class Repository(GitHub):
    """Abstraction of a GitHub repository."""

    def __init__(self, token: str, repo: str):
        """Initialize Repository.
        :param token: An authorization token for GitHub REST APIs.
        :param repo: A GitHub repository (in the format of owner/repo).
        """
        super().__init__(token)
        self._repo = repo
        self._url_repo = f"{URL_API}/repos/{repo}"
        self._url_tags = f"{self._url_repo}/tags"
        self._url_transfer = f"{self._url_repo}/transfer"
        self._url_pull = f"{self._url_repo}/pulls"
        self._url_branches = f"{self._url_repo}/branches"
        self._url_refs = f"{self._url_repo}/git/refs"
        self._url_issues = f"{self._url_repo}/issues"
        self._url_releases = f"{self._url_repo}/releases"
        self._url_secrets = f"{self._url_repo}/actions/secrets"
        self._url_compare = f"{self._url_repo}/compare"

    def get_releases(self, n: int = 0) -> list[dict[str, Any]]:
        """List releases in this repository."""
        return self._extract_all(url=self._url_releases, n=n)

    def get_tags(self, n: int = 0) -> list[dict[str, Any]]:
        """List repository tags."""
        return self._extract_all(url=self._url_tags, n=n)

    def get_release_latest(self) -> dict[str, Any]:
        return self._get(url=f"{self._url_releases}/latest").json()

    def get_release_assets(self, release: int, n: int = 0) -> list[dict[str, Any]]:
        return self._extract_all(url=f"{self._url_releases}/{release}/assets", n=n)

    def create_release(self, json: dict[str, Any]):
        """Create a release.

        :param json: A dict containing the following info for the release.
            - tag_name
            - target_commitish
            - name
            - body
            - draft
            - prerelease
            - generate_release_notes
        It's passed to the json parameter of requests.post.
        For more details, please refer to
        https://docs.github.com/en/rest/releases/releases#create-a-release.
        """
        return self._post(
            url=self._url_releases,
            json=json,
        ).json()

    def upload_release_asset(
        self, release: int, name: str, path: str | Path
    ) -> dict[str, Any]:
        if isinstance(path, str):
            path = Path(path)
        with path.open(mode="rb") as fin:
            return self._post(
                url=f"{self._url_releases.replace('api', 'uploads', 1)}/{
                    release
                }/assets",
                params={
                    "name": name,
                },
                headers=self._headers
                | {
                    "Content-Type": "application/octet-stream",
                },
                data=fin,
            ).json()

    def get_pull_requests(self, n: int = 0) -> list[dict[str, Any]]:
        """List pull requests in this repository."""
        return self._extract_all(url=self._url_pull, n=n)

    def get_pull_request(self, pr_number: int) -> dict[str, Any]:
        """Get the full detail of a single pull request.

        Unlike ``get_pull_requests``, this returns fields computed per-PR such as
        ``mergeable`` and ``mergeable_state``. These are computed asynchronously
        by GitHub, so ``mergeable_state`` may be ``unknown`` immediately after a
        push; callers that need it should re-fetch until it settles.

        :param pr_number: The number of the pull request.
        """
        return self._get(url=f"{self._url_pull}/{pr_number}").json()

    def get_pull_request_reviews(
        self, pr_number: int, n: int = 0
    ) -> list[dict[str, Any]]:
        """List reviews on a pull request.

        :param pr_number: The number of the pull request.
        :param n: The maximum number of reviews to return (0 means all).
        """
        return self._extract_all(url=f"{self._url_pull}/{pr_number}/reviews", n=n)

    def get_issues(
        self, state: IssueState = IssueState.OPEN, n: int = 0
    ) -> list[dict[str, Any]]:
        """List issues in this repository.

        :param state: Filter issues by state: ``open`` (the default),
            ``closed``, or ``all``. Note that GitHub's issues endpoint also
            returns pull requests; each pull request carries a
            ``pull_request`` key.
        :param n: The maximum number of issues to return (0 means all).
        """
        return self._extract_all(url=self._url_issues, params={"state": state}, n=n)

    def _generate_pull_request_content(
        self, base: str, head: str, model: str
    ) -> tuple[str, str] | None:
        """Generate a `(title, body)` for a new PR from the head/base comparison.

        Returns None when there is nothing to describe (an empty comparison), so
        the caller keeps the provided title.

        :param base: The base branch the PR merges into.
        :param head: The head branch containing the changes.
        :param model: The LiteLLM 'provider/model' string for LLM generation.
            When empty, the title and body are generated deterministically from
            the commit messages and changed files; otherwise an LLM is used,
            falling back to deterministic generation on failure.
        """
        compare = self.compare(base=base, head=head)
        if not compare.get("commits"):
            return None
        if model:
            try:
                return generate_pr_content(compare, model=model)
            except Exception as error:
                logger.warning(
                    "LLM PR generation failed (%s); "
                    "falling back to deterministic content.",
                    error,
                    exc_info=True,
                )
        return deterministic_title(compare), deterministic_body(compare)

    def create_pull_request(
        self,
        json: dict[str, Any],
        model: str = "",
    ) -> dict[str, Any] | None:
        """Create a pull request.

        A Conventional-Commits title and a detailed Markdown body are generated
        for a newly created PR. A caller-provided 'title' or 'body' in `json`
        takes precedence; only the missing field(s) are generated. To skip
        generation entirely, provide both 'title' and 'body' in `json`.

        :param json: A dict containing info (e.g., base, head, title, body, etc.)
        about the pull request to be created.
        It's passed to the json parameter of requests.post.
        :param model: The LiteLLM 'provider/model' string. When empty (the
            default), missing title/body are generated deterministically from
            the commit messages and changed files. When non-empty, an LLM is
            used (with the optional 'ai' extra installed and the matching
            provider API key read from the environment), falling back to
            deterministic generation on failure.
        """
        if not ("head" in json and "base" in json):
            raise ValueError("The data dict must contains keys head and base!")
        # return an existing PR
        for pr in self.get_pull_requests():
            if pr["head"]["ref"] == json["head"] and pr["base"]["ref"] == json["base"]:
                return pr
        # generate any title/body not already provided by the caller
        if "title" not in json or "body" not in json:
            content = self._generate_pull_request_content(
                base=json["base"], head=json["head"], model=model
            )
            if content is not None:
                # caller-provided title/body take precedence over generated ones
                json = {"title": content[0], "body": content[1], **json}
        # create a new PR
        resp = self._post(
            url=self._url_pull,
            json=json,
            raise_for_status=False,
        )
        if resp.status_code == 422:
            return None
        resp.raise_for_status()
        return resp.json()

    def merge_pull_request(
        self,
        pr_number: int,
        merge_method: MergeMethod | str = MergeMethod.MERGE,
        sha: str = "",
    ) -> dict[str, Any]:
        """Merge a pull request in this repository.
        :param pr_number: The number of the pull quest to be merged.
        :param merge_method: The merge method to use (``merge``, ``squash`` or
            ``rebase``). Defaults to ``merge``.
        :param sha: When non-empty, the SHA that the PR head must still match for
            the merge to proceed (GitHub rejects the merge with 409 if the head
            has moved). Use it to pin a merge to a previously validated commit.
        """
        body: dict[str, Any] = {"merge_method": str(merge_method)}
        if sha:
            body["sha"] = sha
        resp = self._put(
            url=f"{self._url_pull}/{pr_number}/merge",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    def update_branch(self, update: str, upstream: str) -> dict[str, Any] | None:
        """Update a branch by creating a PR from upstream and then merge it.
        :param update: The branch to update.
        :param upstream: The upstream branch.
        """
        # Provide a title and an (empty) body so no description is generated for
        # this mechanical, immediately merged update PR.
        pr = self.create_pull_request(
            {
                "base": update,
                "head": upstream,
                "title": f"Merge {upstream} into {update}",
                "body": "",
            },
        )
        if pr is None:
            return
        return self.merge_pull_request(pr["number"])

    def _settle_mergeable_state(
        self, pr: dict[str, Any], attempts: int = 3, delay: float = 2.0
    ) -> dict[str, Any]:
        """Re-fetch a PR until GitHub finishes computing ``mergeable_state``.

        GitHub computes ``mergeable_state`` asynchronously, returning ``unknown``
        until it settles. The PR is re-fetched (up to ``attempts`` times, waiting
        ``delay`` seconds between tries) until the state is no longer ``unknown``.

        :param pr: A PR detail dict as returned by ``get_pull_request``.
        :param attempts: The maximum number of fetches before giving up.
        :param delay: The delay in seconds between fetches.
        """
        for _ in range(attempts):
            if pr.get("mergeable_state") not in (None, "unknown"):
                break
            time.sleep(delay)
            pr = self.get_pull_request(pr["number"])
        return pr

    def should_auto_merge(
        self,
        pr_number: int,
        *,
        authors: Sequence[str],
        approvers: Sequence[str],
        allowed_types: Sequence[str] = DEFAULT_AUTO_MERGE_TYPES,
        min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
        marker: str = DEFAULT_AUTO_MERGE_MARKER,
    ) -> str | None:
        """Decide whether a pull request is eligible for automatic merging.

        All of the following must hold: the PR is not a draft; its author is in
        ``authors``; its Conventional-Commits title type is in ``allowed_types``;
        its ``mergeable_state`` is ``clean`` (no merge conflict and no failing or
        pending status checks); its head commit is at least ``min_age_minutes``
        old; and it is approved either by an ``APPROVED`` review or by a
        ``marker`` comment posted after the head commit by an approver in
        ``approvers`` (with no outstanding ``CHANGES_REQUESTED``).

        The age check guards against the race where a brand-new PR momentarily
        reads ``clean`` before its checks register; it relies on the head
        commit's timestamp, which is trustworthy given that ``authors`` is an
        allowlist of trusted automation.

        Checks run cheapest-first, returning ``None`` (with a logged reason) on
        the first failure so extra requests are avoided.

        :param pr_number: The number of the pull request.
        :param authors: Logins whose PRs are eligible for auto-merge.
        :param approvers: Logins whose reviews/marker comments grant approval.
        :param allowed_types: Conventional-Commits title types eligible for auto-merge.
        :param min_age_minutes: The minimum head-commit age in minutes.
        :param marker: The marker substring an approver may comment to approve.
        :return: The validated head SHA when the PR is eligible, else ``None``.
            Passing the SHA to ``merge_pull_request`` pins the merge to the exact
            commit checked here, so a push landing between this gate and the merge
            is rejected rather than silently merged.
        """
        pr = self.get_pull_request(pr_number)

        def skip(reason: str) -> None:
            logger.info("Skipping auto-merge of PR #%s: %s.", pr_number, reason)
            return None

        # Field-only gates first; these need no extra requests.
        field_failure = _field_gate_failure(pr, authors, allowed_types)
        if field_failure:
            return skip(field_failure)
        # Check age before mergeable_state: one commit-date fetch is cheaper than
        # settling mergeable_state, and mergeable_state is only trustworthy once
        # the PR is old enough for its checks to have registered.
        committed = self._head_commit_date(pr["head"]["sha"])
        if not _old_enough(committed, min_age_minutes):
            return skip(f"its head commit is newer than {min_age_minutes} minutes")
        pr = self._settle_mergeable_state(pr)
        if pr.get("mergeable_state") != "clean":
            return skip(f"its mergeable_state is {pr.get('mergeable_state')!r}")
        # The head SHA is read after settling so it matches the mergeable_state
        # just validated, and is returned so the caller can pin the merge to it.
        head_sha = pr["head"]["sha"]
        reviews = self.get_pull_request_reviews(pr_number)
        review_states = _approver_review_states(reviews, approvers)
        # A standing change request vetoes the merge regardless of how approval
        # would otherwise be granted, so it is checked before the marker path.
        if "CHANGES_REQUESTED" in review_states:
            return skip("an approver has requested changes")
        if "APPROVED" in review_states:
            return head_sha
        comments = self.get_issue_comments(pr_number)
        if _marker_approved(comments, approvers, marker, _parse_iso(committed)):
            return head_sha
        return skip("it lacks an approving review or marker comment")

    def auto_merge_pull_requests(
        self,
        *,
        authors: Sequence[str],
        approvers: Sequence[str],
        allowed_types: Sequence[str] = DEFAULT_AUTO_MERGE_TYPES,
        min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
        marker: str = DEFAULT_AUTO_MERGE_MARKER,
        merge_method: MergeMethod | str = MergeMethod.MERGE,
        dry_run: bool = False,
    ) -> list[int]:
        """Auto-merge every open pull request that passes ``should_auto_merge``.

        :param authors: Logins whose PRs are eligible for auto-merge. An empty
            allowlist makes nothing eligible (fail-safe).
        :param approvers: Logins whose reviews/marker comments grant approval.
        :param allowed_types: Conventional-Commits title types eligible for auto-merge.
        :param min_age_minutes: The minimum head-commit age in minutes.
        :param marker: The marker substring an approver may comment to approve.
        :param merge_method: The merge method to use (``merge``, ``squash`` or
            ``rebase``).
        :param dry_run: When True, eligible PRs are logged and returned but not
            merged.
        :return: The numbers of the PRs that were merged (or, under ``dry_run``,
            that would have been merged).
        """
        eligible = []
        for pr in self.get_pull_requests():
            number = pr["number"]
            # Pre-filter on the list payload so PRs failing a field-only gate are
            # dropped without fetching their (more expensive) single-PR detail.
            field_failure = _field_gate_failure(pr, authors, allowed_types)
            if field_failure:
                logger.info("Skipping auto-merge of PR #%s: %s.", number, field_failure)
                continue
            # Both the eligibility checks and the merge issue requests that can
            # fail for a single PR (a force-pushed/deleted head, a PR that became
            # unmergeable, or a transient error). Isolate each PR so one failure
            # skips that PR without aborting the rest of the batch.
            try:
                sha = self.should_auto_merge(
                    number,
                    authors=authors,
                    approvers=approvers,
                    allowed_types=allowed_types,
                    min_age_minutes=min_age_minutes,
                    marker=marker,
                )
                if sha is None:
                    continue
                if dry_run:
                    logger.info("[dry-run] PR #%s is eligible for auto-merge.", number)
                else:
                    logger.info("Auto-merging PR #%s.", number)
                    # Pin the merge to the SHA the gate validated so a push that
                    # landed in between is rejected (409) instead of merged.
                    self.merge_pull_request(number, merge_method, sha=sha)
            except Exception:
                logger.warning(
                    "Skipping PR #%s after an unexpected failure.",
                    number,
                    exc_info=True,
                )
                continue
            eligible.append(number)
        return eligible

    def get_pull_request_files(
        self, pr_number: int, n: int = 0
    ) -> list[dict[str, Any]]:
        """List changed files in the specified GitHub pull request.

        :param pr_number: The number of the pull request.
        """
        return self._extract_all(url=f"{self._url_pull}/{pr_number}/files", n=n)

    def compare(self, base: str, head: str) -> dict[str, Any]:
        """Compare two commits/branches in this repository.

        :param base: The base branch (or commit) of the comparison.
        :param head: The head branch (or commit) of the comparison.
        :return: The comparison result containing `commits` and `files`
            (each file with `filename`, `status`, `additions`, `deletions`,
            and `patch`). See
            https://docs.github.com/en/rest/commits/commits#compare-two-commits.
        """
        # Branch names may contain slashes, so each ref is URL-encoded while the
        # literal `...` separator between them is preserved.
        basehead = f"{quote(base, safe='')}...{quote(head, safe='')}"
        return self._get(url=f"{self._url_compare}/{basehead}").json()

    def _head_commit_date(self, sha: str) -> str:
        """Return the committer date of ``sha`` without fetching its file diff.

        The single-commit endpoint (``GET /commits/{sha}``) embeds the commit's
        full file diff, which is wasteful when only the timestamp is needed. The
        list-commits endpoint omits per-commit files, so requesting a single
        commit starting at ``sha`` stays cheap even for a large diff.

        :param sha: The head commit SHA.
        """
        resp = self._get(
            url=f"{self._url_repo}/commits",
            params={"sha": sha, "per_page": 1},
        )
        resp.raise_for_status()
        commits = resp.json()
        if not isinstance(commits, list) or not commits:
            raise ValueError(f"No commits found for SHA {sha}")
        try:
            return commits[0]["commit"]["committer"]["date"]
        except (KeyError, TypeError) as e:
            raise ValueError(
                f"Unexpected commit payload structure for SHA {sha}"
            ) from e

    def get_branches(self, n: int = 0) -> list[dict[str, Any]]:
        """List branches in this repository."""
        return self._extract_all(url=self._url_branches, n=n)

    def get_branch(self, branch: str) -> dict[str, Any]:
        """Get information about a specific branch.
        :param branch: The name of the branch.
        """
        return self._get(url=f"{self._url_branches}/{branch}").json()

    def delete(self) -> requests.Response:
        """Delete this repository from GitHub."""
        return self._delete(url=self._url_repo)

    def delete_ref(self, ref: str) -> requests.Response:
        """Delete a reference from this repository.
        :param ref: The reference to delete from this repository.
        """
        return self._delete(
            url=f"{self._url_refs}/{ref}",
        )

    def delete_branch(self, branch: str) -> requests.Response:
        """Delete a branch from this repository.
        :param branch: The branch to delete from this repository.
        """
        return self.delete_ref(ref=f"heads/{branch}")

    def get_secrets(self, n: int = 0) -> list[dict[str, Any]]:
        """List secrets in this repository."""
        return self._extract_all(url=self._url_secrets, n=n, key="secrets")

    def delete_secret(self, name: str) -> requests.Response:
        """Delete a secret from this repository.
        :param name: The name of the secret to delete.
        """
        return self._delete(
            url=f"{self._url_secrets}/{name}",
        )

    def get_secret_public_key(self) -> dict[str, Any]:
        """Get the public key for encrypting secrets in this repository."""
        return self._get(url=f"{self._url_secrets}/public-key").json()

    def create_or_update_secret(
        self, name: str, value: str, public_key: dict[str, Any] | None = None
    ) -> requests.Response:
        """Create or update a secret in this repository.
        :param name: The name of the secret.
        :param value: The plaintext value of the secret.
        :param public_key: A public key (as returned by `get_secret_public_key`)
            to encrypt the secret with. If not provided, it is fetched
            automatically. Fetch it once and reuse it to avoid a redundant
            request when creating or updating multiple secrets.
        """
        _validate_secret_name(name)
        if public_key is None:
            public_key = self.get_secret_public_key()
        return self._put(
            url=f"{self._url_secrets}/{name}",
            json={
                "encrypted_value": _encrypt_secret(public_key["key"], value),
                "key_id": public_key["key_id"],
            },
        )

    def pr_has_change(
        self, pr_number: int, pred: Callable[[str], bool] = lambda _: True
    ) -> bool:
        """Check whether a PR has any change satisfying pred.

        :param pr_number: The number of the corresponding pull request.
        :param pred: A boolean predictor (always true by default)
        checking whether a single file has specific changes.
        """
        files = self.get_pull_request_files(pr_number)
        return any(pred(file["filename"]) for file in files)

    def pr_has_rust_change(
        self, pr_number: int, pred: Callable[[str], bool] = _is_rust
    ) -> bool:
        """Check whether a PR has any Rust-related changes.

        :param token: The authorization token for GitHub REST API.
        :param pr_number: The number of the corresponding pull request.
        :param pred: A customized boolean predictor checking Rust-related changes.
        """
        return self.pr_has_change(pr_number=pr_number, pred=pred)

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Sequence[str] = (),
        assignees: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Create an issue in this repository.

        :param title: The title of the new issue.
        :param body: The Markdown body of the new issue.
        :param labels: Names of labels to attach to the new issue, or a single
            name as a bare string. Silently dropped unless the authenticated
            user has push access to the repository. Labels that do not already
            exist are created by GitHub.
        :param assignees: Logins of users to assign to the new issue, or a
            single login as a bare string. Silently dropped unless the
            authenticated user has push access to the repository.
        """
        # A bare string is a Sequence[str] of its own characters, so it would
        # otherwise be sent as one entry per character.
        if isinstance(labels, str):
            labels = [labels]
        if isinstance(assignees, str):
            assignees = [assignees]
        json: dict[str, Any] = {"title": title}
        if body:
            json["body"] = body
        if labels:
            json["labels"] = list(labels)
        if assignees:
            json["assignees"] = list(assignees)
        return self._post(url=self._url_issues, json=json).json()

    def get_issue_comments(self, issue_number: int, n: int = 0) -> list[dict[str, Any]]:
        """List comments on an issue in this repository.

        :param issue_number: The number of the issue.
        :param n: The maximum number of comments to return (0 means all).
        """
        return self._extract_all(url=f"{self._url_issues}/{issue_number}/comments", n=n)

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Add a new comment to an issue.

        :param issue_number: The number of the issue.
        :param body: Body text of the new comment.
        """
        return self._post(
            url=f"{self._url_issues}/{issue_number}/comments",
            json={"body": body},
        ).json()

    def archive(self) -> requests.Response:
        return self._patch(
            url=self._url_repo,
            json={"archived": True},
        )

    def transfer(self, new_owner: str, new_name: str = "") -> requests.Response:
        data = {
            "new_owner": new_owner,
        }
        if new_name:
            data["new_name"] = new_name
        return self._post(url=self._url_transfer, json=data)


class RepositoryType(StrEnum):
    ALL = "all"
    PUBLIC = "public"
    PRIVATE = "private"


class SecretVisibility(StrEnum):
    ALL = "all"
    PRIVATE = "private"
    SELECTED = "selected"


class Owner(GitHub, metaclass=ABCMeta):
    """An abstract owner class representing an organization or user."""

    def __init__(self, token: str, owner: str):
        """Initialize Repository.
        :param token: An authorization token for GitHub REST APIs.
        :param owner: The name of the owner (organization or user).
        """
        super().__init__(token)
        self._owner = owner
        self._url_owner = ""
        self._url_repos = ""
        self._url_create_repo = ""

    @abstractmethod
    def _set_urls(self) -> None:
        pass

    def get_repositories(
        self, type_: RepositoryType = RepositoryType.ALL, n: int = 0
    ) -> list[dict[str, Any]]:
        """Get all accessible repositories.

        :param type_: Type of repositories (e.g., public).
        """
        return self._extract_all(url=self._url_repos, params={"type": type_}, n=n)

    def instantiate_repository(self, repo: str) -> Repository:
        return Repository(token=self._token, repo=f"{self._owner}/{repo}")

    def create_repository(
        self, name: str, description: str = "", private: bool = True, **kwargs
    ) -> dict[str, Any]:
        """Create a repository for this owner.
        :param name: The name of the repository.
        :param description: A short description of the repository.
        :param private: Whether the repository is private.
        :param kwargs: Additional keyword arguments forwarded to `_post`
            (e.g. `params` or `raise_for_status`). Note `json` is already set
            from the other parameters and must not be passed here.
        """
        data = {
            "name": name,
            "description": description,
            "homepage": "https://github.com",
            "private": private,
            "has_issues": True,
            "has_projects": True,
            "has_wiki": True,
        }
        return self._post(url=self._url_create_repo, json=data, **kwargs).json()


class User(Owner):
    """A GitHub user."""

    def __init__(self, token: str, user: str):
        """Initialize a User.
        :param token: An authorization token for GitHub REST APIs.
        :param user: The name of the user.
        """
        super().__init__(token=token, owner=user)
        self._set_urls()

    def _set_urls(self) -> None:
        self._url_owner = f"{URL_API}/users/{self._owner}"
        self._url_repos = f"{self._url_owner}/repos"
        self._url_create_repo = f"{URL_API}/user/repos"


class Organization(Owner):
    """A GitHub organization."""

    def __init__(self, token: str, org: str):
        """Initialize an Organization.
        :param token: An authorization token for GitHub REST APIs.
        :param org: The name of the organization.
        """
        super().__init__(token=token, owner=org)
        self._set_urls()

    def _set_urls(self) -> None:
        self._url_owner = f"{URL_API}/orgs/{self._owner}"
        self._url_repos = f"{self._url_owner}/repos"
        self._url_create_repo = self._url_repos
        self._url_secrets = f"{self._url_owner}/actions/secrets"

    def get_secrets(self, n: int = 0) -> list[dict[str, Any]]:
        """List secrets in this organization."""
        return self._extract_all(url=self._url_secrets, n=n, key="secrets")

    def delete_secret(self, name: str) -> requests.Response:
        """Delete an organization secret.
        :param name: The name of the secret to delete.
        """
        return self._delete(
            url=f"{self._url_secrets}/{name}",
        )

    def get_secret_public_key(self) -> dict[str, Any]:
        """Get the public key for encrypting secrets in this organization."""
        return self._get(url=f"{self._url_secrets}/public-key").json()

    def create_or_update_secret(
        self,
        name: str,
        value: str,
        public_key: dict[str, Any] | None = None,
        visibility: SecretVisibility = SecretVisibility.ALL,
        selected_repository_ids: Sequence[int] = (),
    ) -> requests.Response:
        """Create or update an organization secret.
        :param name: The name of the secret.
        :param value: The plaintext value of the secret.
        :param public_key: A public key (as returned by `get_secret_public_key`)
            to encrypt the secret with. If not provided, it is fetched
            automatically. Fetch it once and reuse it to avoid a redundant
            request when creating or updating multiple secrets.
        :param visibility: Which repositories can access the secret
            (all, private, or selected).
        :param selected_repository_ids: Repository IDs that can access the secret
            when visibility is `selected`.
        """
        _validate_secret_name(name)
        if selected_repository_ids and visibility != SecretVisibility.SELECTED:
            raise ValueError(
                "`selected_repository_ids` can only be provided when `visibility` is 'selected'."
            )
        if public_key is None:
            public_key = self.get_secret_public_key()
        json: dict[str, Any] = {
            "encrypted_value": _encrypt_secret(public_key["key"], value),
            "key_id": public_key["key_id"],
            "visibility": visibility,
        }
        if selected_repository_ids:
            json["selected_repository_ids"] = list(selected_repository_ids)
        return self._put(
            url=f"{self._url_secrets}/{name}",
            json=json,
        )
