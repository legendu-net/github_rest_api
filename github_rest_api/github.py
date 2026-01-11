"""Simple wrapper of GitHub REST APIs."""

from enum import StrEnum
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


class GitHub:
    def __init__(self, token: str):
        self._token = token
        self._headers = build_http_headers(token)

    def get(
        self, url: str, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        resp = requests.get(
            url=url,
            headers=self._headers,
            timeout=10,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def post(
        self, url: str, headers=None, raise_for_status: bool = True, **kwargs
    ) -> requests.Response:
        if headers is None:
            headers = self._headers
        resp = requests.post(
            url=url,
            headers=headers,
            timeout=10,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def delete(self, url, raise_for_status: bool = True) -> requests.Response:
        resp = requests.delete(url=url, headers=self._headers, timeout=10)
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def put(self, url, raise_for_status: bool = True) -> requests.Response:
        resp = requests.put(
            url=url,
            headers=self._headers,
            timeout=10,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp


class Repository(GitHub):
    """Abstraction of a GitHub repository."""

    def __init__(self, token: str, repo: str):
        """Initialize Repository.
        :param token: An authorization token for GitHub REST APIs.
        :param repo: A GitHub repository (in the format of owner/repo).
        """
        super().__init__(token)
        self._repo = repo
        self._url_pull = f"https://api.github.com/repos/{repo}/pulls"
        self._url_branches = f"https://api.github.com/repos/{repo}/branches"
        self._url_refs = f"https://api.github.com/repos/{repo}/git/refs"
        self._url_issues = f"https://api.github.com/repos/{repo}/issues"
        self._url_releases = f"https://api.github.com/repos/{repo}/releases"

    def get_releases(self) -> list[dict[str, Any]]:
        """List releases in this repository."""
        return self.get(url=self._url_releases).json()

    def get_release_latest(self) -> dict[str, Any]:
        return self.get(url=f"{self._url_releases}/latest").json()

    def get_release_assets(self, release: int) -> list[dict[str, Any]]:
        return requests.get(url=f"{self._url_releases}/{release}/assets").json()

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
        if not isinstance(json, dict):
            raise ValueError("A dict value is required for `json`.")
        return self.post(
            url=self._url_releases,
            json=json,
        ).json()

    def upload_release_asset(
        self, release: int, name: str, path: str | Path
    ) -> dict[str, Any]:
        if isinstance(path, str):
            path = Path(path)
        with path.open(mode="rb") as fin:
            return self.post(
                url=f"{self._url_releases.replace('api', 'uploads', 1)}/{release}/assets",
                params={
                    "name": name,
                },
                headers=self._headers
                | {
                    "Content-Type": "application/octet-stream",
                },
                data=fin,
            ).json()

    def get_pull_requests(self) -> list[dict[str, Any]]:
        """List pull requests in this repository."""
        return self.get(url=self._url_pull).json()

    def create_pull_request(self, json: dict[str, str]) -> dict[str, Any] | None:
        """Create a pull request.

        :param json: A dict containing info (e.g., base, head, title, body, etc.)
        about the pull request to be created.
        It's passed to the json parameter of requests.post.
        """
        if not isinstance(json, dict):
            raise ValueError("A dict value is required for `json`.")
        if not ("head" in json and "base" in json):
            raise ValueError("The data dict must contains keys head and base!")
        # return an existing PR
        prs = self.get_pull_requests()
        for pr in prs:
            if pr["head"]["ref"] == json["head"] and pr["base"]["ref"] == json["base"]:
                return pr
        # creat a new PR
        resp = self.post(
            url=self._url_pull,
            json=json,
            raise_for_status=False,
        )
        if resp.status_code == 422:
            return None
        resp.raise_for_status()
        return resp.json()

    def merge_pull_request(self, pr_number: int) -> dict[str, Any]:
        """Merge a pull request in this repository.
        :param pr_number: The number of the pull quest to be merged.
        """
        if not isinstance(pr_number, int):
            raise ValueError("An integer value is required for `pr_number`.")
        return self.put(
            url=f"{self._url_pull}/{pr_number}/merge",
        ).json()

    def update_branch(self, update: str, upstream: str) -> dict[str, Any] | None:
        """Update a branch by creating a PR from upstream and then merge it.
        :param update: The branch to update.
        :param upstream: The upstream branch.
        """
        if not isinstance(update, str):
            raise ValueError("A string value is required for `update`.")
        if not isinstance(upstream, str):
            raise ValueError("A string value is required for `upstream`.")
        pr = self.create_pull_request(
            {
                "base": update,
                "head": upstream,
                "title": f"Merge {upstream} into {update}",
            },
        )
        if pr is None:
            return
        return self.merge_pull_request(pr["number"])

    def get_pull_request_files(self, pr_number: int) -> list[dict[str, Any]]:
        """List changed files in the specified GitHub pull request.

        :param pr_number: The number of the pull request.
        """
        if not isinstance(pr_number, int):
            raise ValueError("An integer value is required for `pr_number`.")
        return self.get(url=f"{self._url_pull}/{pr_number}/files").json()

    def get_branches(self) -> list[dict[str, Any]]:
        """List branches in this repository."""
        return self.get(url=self._url_branches).json()

    def delete_ref(self, ref: str) -> dict[str, Any]:
        """Delete a reference from this repository.
        :param ref: The reference to delete from this repository.
        """
        if not isinstance(ref, str):
            raise ValueError("A string value is required for `ref`.")
        return self.delete(
            url=f"{self._url_refs}/{ref}",
        ).json()

    def delete_branch(self, branch: str) -> dict[str, Any]:
        """Delete a branch from this repository.
        :param branch: The branch to delete from this repository.
        """
        return self.delete_ref(ref=f"heads/{branch}")

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

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Add a new comment to an issue.

        :param issue_number: The number of the issue.
        :param body: Body text of the new comment.
        """
        if not isinstance(issue_number, int):
            raise ValueError("An integer value is required for `issue_number`.")
        if not isinstance(body, str):
            raise ValueError("A string message is required for `body`.")
        return self.post(
            url=f"{self._url_issues}/{issue_number}/comments",
            json={"body": body},
            timeout=10,
        ).json()


class RepositoryType(StrEnum):
    ALL = "all"
    PUBLIC = "public"
    PRIVATE = "private"


class Organization(GitHub):
    def __init__(self, token: str, owner: str):
        """Initialize Repository.
        :param token: An authorization token for GitHub REST APIs.
        :param owner: The owner of the repository.
        """
        super().__init__(token)
        self._owner = owner
        self._url_repos = f"https://api.github.com/orgs/{owner}/repos"

    def get_repositories(
        self, type_: RepositoryType = RepositoryType.ALL
    ) -> list[dict[str, Any]]:
        """Get all accessible repositories.

        :param type_: Type of repositories (e.g., public).
        """
        params = {
            "type": type_,
            "page": 1,
            "per_page": 100,
        }
        repos = []
        while True:
            resp = self.get(url=self._url_repos, params=params)
            resp.raise_for_status()
            data = resp.json()
            repos.extend(data)
            if len(data) < params["per_page"]:  # ty: ignore[unsupported-operator]
                return repos
            params["page"] += 1  # ty: ignore[unsupported-operator]
        return repos

    def instantiate_repository(self, repo: str) -> Repository:
        return Repository(token=self._token, repo=f"{self._owner}/{repo}")
