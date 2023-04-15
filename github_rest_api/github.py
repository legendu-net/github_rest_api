"""Simple wrapper of GitHub REST APIs.
"""
from typing import Callable
from pathlib import Path
import json
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
        self._headers = build_http_headers(token)

    def list_pull_requests(self) -> list[dict]:
        """List pull requests in this repository."""
        resp = requests.get(
            url=self._url_pull,
            headers=self._headers,
            timeout=10,
        )
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()

    def create_pull_request(self, data: dict[str, str]) -> dict | None:
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
            data=json.dumps(data),
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

    def update_branch(self, update: str, upstream: str):
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

    def list_pull_request_files(self, pr_number: int) -> dict[str, str]:
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

    def list_branches(self) -> dict[str, str]:
        """List branches in this repository."""
        resp = requests.get(url=self._url_branches, headers=self._headers, timeout=10)
        if not resp.ok:
            resp.raise_for_status()
        return resp.json()

    def pr_has_change(
        self, pr_number: int, pred: Callable[[str], bool] = lambda _: True
    ):
        """Check whether a PR has any change satisfying pred.

        :param pr_number: The number of the corresponding pull request.
        :param pred: A boolean predictor (always true by default)
        checking whether a single file has specific changes.
        """
        files = self.list_pull_request_files(pr_number)
        return any(pred(file["filename"]) for file in files)

    def pr_has_rust_change(
        self, pr_number: int, pred: Callable[[str], bool] = _is_rust
    ):
        """Check whether a PR has any Rust-related changes.

        :param token: The authorization token for GitHub REST API.
        :param pr_number: The number of the corresponding pull request.
        :param pred: A customized boolean predictor checking Rust-related changes.
        """
        return self.pr_has_change(pr_number=pr_number, pred=pred)
