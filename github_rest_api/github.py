"""Simple wrapper of GitHub REST APIs."""

from abc import ABCMeta, abstractmethod
from base64 import b64encode
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Callable
from pathlib import Path
import requests
from nacl import encoding, public

URL_API = "https://api.github.com"


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
            to `requests.get`.
        """
        resp = requests.get(
            url=url,
            headers=self._headers,
            timeout=10,
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
            to `requests.post`.
        """
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

    def _delete(self, url, raise_for_status: bool = True) -> requests.Response:
        resp = requests.delete(url=url, headers=self._headers, timeout=10)
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
            to `requests.put`.
        """
        resp = requests.put(
            url=url,
            headers=self._headers,
            timeout=10,
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
            to `requests.patch`.
        """
        resp = requests.patch(
            url=url,
            headers=self._headers,
            timeout=10,
            **kwargs,
        )
        if raise_for_status:
            resp.raise_for_status()
        return resp

    def _extract_all(
        self, url: str, params: dict[str, Any] | None = None, n: int = 0
    ) -> list[dict[str, Any]]:
        params = params.copy() if params else {}
        if "per_page" not in params:
            params["per_page"] = min(100, n) if n > 0 else 100
        params["page"] = 1
        res = []
        while True:
            resp = self._get(url=url, params=params.copy())
            resp.raise_for_status()
            data = resp.json()
            res.extend(data)
            if n and len(res) >= n:
                return res[:n]
            if len(data) < params["per_page"]:
                return res
            params["page"] += 1


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

    def create_pull_request(self, json: dict[str, str]) -> dict[str, Any] | None:
        """Create a pull request.

        :param json: A dict containing info (e.g., base, head, title, body, etc.)
        about the pull request to be created.
        It's passed to the json parameter of requests.post.
        """
        if not ("head" in json and "base" in json):
            raise ValueError("The data dict must contains keys head and base!")
        # return an existing PR
        prs = self.get_pull_requests()
        for pr in prs:
            if pr["head"]["ref"] == json["head"] and pr["base"]["ref"] == json["base"]:
                return pr
        # creat a new PR
        resp = self._post(
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
        return self._put(
            url=f"{self._url_pull}/{pr_number}/merge",
        ).json()

    def update_branch(self, update: str, upstream: str) -> dict[str, Any] | None:
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
        return self.merge_pull_request(pr["number"])

    def get_pull_request_files(
        self, pr_number: int, n: int = 0
    ) -> list[dict[str, Any]]:
        """List changed files in the specified GitHub pull request.

        :param pr_number: The number of the pull request.
        """
        return self._extract_all(url=f"{self._url_pull}/{pr_number}/files", n=n)

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
        self, name: str, value: str, public_key: dict[str, Any]
    ) -> requests.Response:
        """Create or update a secret in this repository.
        :param name: The name of the secret.
        :param value: The plaintext value of the secret.
        :param public_key: A public key (as returned by `get_secret_public_key`)
            to encrypt the secret with. Fetch it once and reuse it to avoid a
            redundant request when creating or updating multiple secrets.
        """
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

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Add a new comment to an issue.

        :param issue_number: The number of the issue.
        :param body: Body text of the new comment.
        """
        return self._post(
            url=f"{self._url_issues}/{issue_number}/comments",
            json={"body": body},
            timeout=10,
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
        public_key: dict[str, Any],
        visibility: SecretVisibility = SecretVisibility.ALL,
        selected_repository_ids: Sequence[int] = (),
    ) -> requests.Response:
        """Create or update an organization secret.
        :param name: The name of the secret.
        :param value: The plaintext value of the secret.
        :param public_key: A public key (as returned by `get_secret_public_key`)
            to encrypt the secret with. Fetch it once and reuse it to avoid a
            redundant request when creating or updating multiple secrets.
        :param visibility: Which repositories can access the secret
            (all, private, or selected).
        :param selected_repository_ids: Repository IDs that can access the secret
            when visibility is `selected`.
        """
        if selected_repository_ids and visibility != SecretVisibility.SELECTED:
            raise ValueError(
                "`selected_repository_ids` can only be provided when `visibility` is 'selected'."
            )
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
