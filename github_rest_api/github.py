"""Simple wrapper of GitHub REST APIs.
"""
from typing import Any, Callable
from pathlib import Path
import requests


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


class Repository:
    """Abstraction of a GitHub repository."""

    def __init__(self, token: str, owner: str, repo: str):
        """Initialize Repository.
        :param token: An authorization token for GitHub REST APIs.
        :param owner: The owner of the repository.
        :param repo: The name of the repository.
        """
        self._token = token
        self.owner = owner
        self.repo = repo
        self._url_pull = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        self._url_branches = f"https://api.github.com/repos/{owner}/{repo}/branches"
        self._url_refs = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
        self._url_issues = f"https://api.github.com/repos/{owner}/{repo}/issues"
        self._headers = build_http_headers(token)

    def list_pull_requests(self) -> list[dict[str, Any]]:
        """List pull requests in this repository."""
        resp = requests.get(
            url=self._url_pull,
            headers=self._headers,
            timeout=10,
        )
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()

    def create_pull_request(self, data: dict[str, str]) -> dict[str, Any] | None:
        """Create a pull request.

        :param data: A dict containing information (e.g., base, head, title, body, etc.)
        about the pull request to be created.
        """
        if not ("head" in data and "base" in data):
            raise ValueError("The data dict must contains keys head and base!")
        # return an existing PR
        prs = self.list_pull_requests()
        for pr in prs:
            if pr["head"]["ref"] == data["head"] and pr["base"]["ref"] == data["base"]:
                return pr
        # creat a new PR
        resp = requests.post(
            url=self._url_pull,
            headers=self._headers,
            json=data,
            timeout=10,
        )
        if resp.status_code == 422:
            return None
        return resp.json()

    def merge_pull_request(self, pr_number: int) -> None:
        """Merge a pull request in this repository.
        :param pr_number: The number of the pull quest to be merged.
        """
        resp = requests.put(
            url=f"{self._url_pull}/{pr_number}/merge",
            headers=self._headers,
            timeout=10,
        )
        if not resp.ok:
            resp.raise_for_status()

    def update_branch(self, update: str, upstream: str) -> None:
        """Update a branch by creating a PR from upstream and then merge it.
        :param update: The branch to update.
        :param upstream: The upstream branch.
        """
        pr = self.create_pull_request(
            {
                "base": update,
                "head": upstream,
                "title": f"Merge {upstream} into {update}",
            },
        )
        if pr is None:
            return
        self.merge_pull_request(pr["number"])

    def list_pull_request_files(self, pr_number: int) -> list[dict[str, Any]]:
        """List changed files in the specified GitHub pull request.

        :param pr_number: The number of the pull request.
        """
        resp = requests.get(
            url=f"{self._url_pull}/{pr_number}/files",
            headers=self._headers,
            timeout=10,
        )
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()

    def list_branches(self) -> list[dict[str, Any]]:
        """List branches in this repository."""
        resp = requests.get(url=self._url_branches, headers=self._headers, timeout=10)
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()

    def delete_ref(self, ref: str) -> None:
        """Delete a reference from this repository.
        :param ref: The reference to delete from this repository.
        """
        resp = requests.delete(
            f"{self._url_refs}/{ref}", headers=self._headers, timeout=10
        )
        if not resp.ok:
            resp.raise_for_status()

    def delete_branch(self, branch: str) -> None:
        """Delete a branch from this repository.
        :param branch: The branch to delete from this repository.
        """
        self.delete_ref(ref=f"heads/{branch}")

    def pr_has_change(
        self, pr_number: int, pred: Callable[[str], bool] = lambda _: True
    ) -> bool:
        """Check whether a PR has any change satisfying pred.

        :param pr_number: The number of the corresponding pull request.
        :param pred: A boolean predictor (always true by default)
        checking whether a single file has specific changes.
        """
        files = self.list_pull_request_files(pr_number)
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

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Add a new comment to an issue.

        :param issue_number: The number of the issue.
        :param body: Body text of the new comment.
        """
        resp = requests.post(
            url=f"{self._url_issues}/{issue_number}/comments",
            headers=self._headers,
            json={"body": body},
            timeout=10,
        )
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()
