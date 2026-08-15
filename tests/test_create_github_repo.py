from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dulwich import porcelain
from dulwich.refs import HEADREF

from github_rest_api.github import DEFAULT_TIMEOUT
from github_rest_api.scripts.github.create_github_repo import (
    _add_workflow,
    _download_workflow,
    _ensure_remote,
    _init_local_repo,
)

KEPT_BRANCH = "kept"


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path, monkeypatch):
    """Ignore the ambient Git config, e.g. commit.gpgsign or init.defaultBranch."""
    config = tmp_path / "gitconfig"
    config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)


def _init_local(dir_: Path, branches=("main",)) -> None:
    _init_local_repo(
        repo="owner/name",
        dir_=str(dir_),
        token="",
        protocol="git",
        push=False,
        branches=branches,
        workflows_repo="",
    )


def _branches(dir_: Path) -> set[str]:
    return {b.decode() for b in porcelain.branch_list(dir_)}


def _head(dir_: Path) -> bytes:
    return (dir_ / ".git" / "HEAD").read_bytes().strip()


def _detached_repo(dir_: Path, keep_branch: bool) -> bytes:
    """Build a repo whose HEAD is detached, as in a colocated Jujutsu repo."""
    dir_.mkdir()
    porcelain.init(path=dir_)
    (dir_ / "code.py").write_text("print('hello')\n")
    porcelain.add(repo=dir_, paths=["code.py"])
    porcelain.commit(repo=dir_, message="first commit")
    initial_branch = porcelain.active_branch(dir_).decode()
    with porcelain.open_repo_closing(dir_) as repo:
        head = repo.refs[HEADREF]
    if keep_branch:
        # An explicit name, so that the test does not depend on the initial one.
        porcelain.branch_create(repo=dir_, name=KEPT_BRANCH)
    (dir_ / ".git" / "HEAD").write_bytes(head + b"\n")  # Detach HEAD at the commit.
    porcelain.branch_delete(repo=dir_, name=initial_branch)
    return head


def _orphan_head_repo(dir_: Path) -> None:
    """Build a repo with a branch but an unborn HEAD, as `git switch --orphan` does."""
    dir_.mkdir()
    porcelain.init(path=dir_)
    (dir_ / "code.py").write_text("print('hello')\n")
    porcelain.add(repo=dir_, paths=["code.py"])
    porcelain.commit(repo=dir_, message="first commit")
    initial_branch = porcelain.active_branch(dir_).decode()
    porcelain.branch_create(repo=dir_, name=KEPT_BRANCH)
    (dir_ / ".git" / "HEAD").write_bytes(b"ref: refs/heads/orphan\n")
    porcelain.branch_delete(repo=dir_, name=initial_branch)


def test_init_local_repo_from_scratch(tmp_path):
    dir_ = tmp_path / "repo"
    _init_local(dir_, branches=("main", "dev"))
    assert _branches(dir_) == {"main", "dev"}
    assert porcelain.active_branch(dir_).decode() == "main"
    assert (dir_ / "README.md").exists()


def test_init_local_repo_with_detached_head(tmp_path):
    dir_ = tmp_path / "repo"
    head = _detached_repo(dir_, keep_branch=False)
    assert not _branches(dir_)

    _init_local(dir_, branches=("main", "dev"))

    assert _branches(dir_) == {"main", "dev"}
    with porcelain.open_repo_closing(dir_) as repo:
        assert repo.refs[b"refs/heads/main"] == head
        assert repo.refs[b"refs/heads/dev"] == head
    # HEAD is left detached rather than checked out onto a new branch.
    assert _head(dir_) == head


def test_init_local_repo_with_detached_head_and_existing_branch(tmp_path):
    dir_ = tmp_path / "repo"
    head = _detached_repo(dir_, keep_branch=True)
    assert _branches(dir_) == {KEPT_BRANCH}

    _init_local(dir_, branches=("main",))

    assert _branches(dir_) == {KEPT_BRANCH, "main"}
    with porcelain.open_repo_closing(dir_) as repo:
        assert repo.refs[b"refs/heads/main"] == head
        assert repo.refs[b"refs/heads/" + KEPT_BRANCH.encode()] == head
    assert _head(dir_) == head


def test_init_local_repo_with_unborn_head_and_existing_branch(tmp_path):
    dir_ = tmp_path / "repo"
    _orphan_head_repo(dir_)
    assert _branches(dir_) == {KEPT_BRANCH}

    with pytest.raises(ValueError, match="HEAD does not point at a commit"):
        _init_local(dir_, branches=("main",))

    assert _branches(dir_) == {KEPT_BRANCH}


def test_init_local_repo_with_unborn_head_and_nothing_to_create(tmp_path):
    """An unborn HEAD is fine as long as every requested branch already exists."""
    dir_ = tmp_path / "repo"
    _orphan_head_repo(dir_)

    _init_local(dir_, branches=(KEPT_BRANCH,))

    assert _branches(dir_) == {KEPT_BRANCH}


FETCH_REFSPEC = b"+refs/heads/*:refs/remotes/origin/*"


def _fetch_refspec(dir_: Path) -> bytes | None:
    with porcelain.open_repo_closing(dir_) as repo:
        try:
            return repo.get_config().get((b"remote", b"origin"), b"fetch")
        except KeyError:
            return None


def test_init_local_repo_sets_origin_fetch_refspec(tmp_path):
    dir_ = tmp_path / "repo"
    _init_local(dir_)
    assert _fetch_refspec(dir_) == FETCH_REFSPEC


def test_ensure_remote_repairs_fetch_refspec_for_matching_existing_origin(tmp_path):
    dir_ = tmp_path / "repo"
    dir_.mkdir()
    porcelain.init(path=dir_)
    porcelain.remote_add(dir_, "origin", "git@github.com:owner/name.git")
    assert _fetch_refspec(dir_) is None

    _ensure_remote(dir_, "owner/name", "git")

    assert _fetch_refspec(dir_) == FETCH_REFSPEC


def test_ensure_remote_leaves_mismatched_existing_origin_untouched(tmp_path):
    dir_ = tmp_path / "repo"
    dir_.mkdir()
    porcelain.init(path=dir_)
    porcelain.remote_add(dir_, "origin", "git@github.com:other/repo.git")

    _ensure_remote(dir_, "owner/name", "git")

    assert _fetch_refspec(dir_) is None
    with porcelain.open_repo_closing(dir_) as repo:
        url = repo.get_config().get((b"remote", b"origin"), b"url")
    assert url == b"git@github.com:other/repo.git"


def _mock_response(json_data=None, text=""):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def _workflow_entry(name, type_="file"):
    return {
        "name": name,
        "type": type_,
        "download_url": f"https://raw.githubusercontent.com/owner/name/main/.github/workflows/{name}",
    }


def _fake_get(listing, contents):
    def get(url, timeout, headers=None):
        if url.endswith("/contents/.github/workflows"):
            return _mock_response(json_data=listing)
        name = url.rsplit("/", 1)[-1]
        return _mock_response(text=contents[name])

    return get


def test_add_workflow_downloads_templates_and_skips_non_files(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    listing = [
        _workflow_entry("lint.yaml"),
        _workflow_entry("check-workflow-template.yaml"),
        _workflow_entry("subdir", type_="dir"),
    ]
    contents = {"lint.yaml": "name: lint\n"}

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get",
        side_effect=_fake_get(listing, contents),
    ):
        _add_workflow(dest, workflows_repo="owner/name", token="")

    workflows_dir = dest / ".github" / "workflows"
    assert {p.name for p in workflows_dir.iterdir()} == {"lint.yaml"}
    assert (workflows_dir / "lint.yaml").read_text() == "name: lint\n"


def test_add_workflow_does_not_overwrite_existing_file(tmp_path):
    dest = tmp_path / "repo"
    dest_workflows = dest / ".github" / "workflows"
    dest_workflows.mkdir(parents=True)
    (dest_workflows / "lint.yaml").write_text("name: customized\n")
    listing = [_workflow_entry("lint.yaml")]
    contents = {"lint.yaml": "name: new\n"}

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get",
        side_effect=_fake_get(listing, contents),
    ):
        _add_workflow(dest, workflows_repo="owner/name", token="")

    assert (dest_workflows / "lint.yaml").read_text() == "name: customized\n"


def test_add_workflow_falls_back_to_contents_api_without_download_url(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    listing = [{"name": "lint.yaml", "type": "file", "download_url": None}]
    contents = {"lint.yaml": "name: lint\n"}

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get",
        side_effect=_fake_get(listing, contents),
    ):
        _add_workflow(dest, workflows_repo="owner/name", token="")

    workflows_dir = dest / ".github" / "workflows"
    assert (workflows_dir / "lint.yaml").read_text() == "name: lint\n"


def test_download_workflow_omits_authorization_for_download_url():
    entry = _workflow_entry("lint.yaml")

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get",
        return_value=_mock_response(text="name: lint\n"),
    ) as mock_get:
        _download_workflow("owner/name", entry, token="secret-token")

    mock_get.assert_called_once_with(url=entry["download_url"], timeout=DEFAULT_TIMEOUT)


def test_download_workflow_fallback_sends_authenticated_raw_accept_header():
    entry = {"name": "lint.yaml", "type": "file", "download_url": None}

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get",
        return_value=_mock_response(text="name: lint\n"),
    ) as mock_get:
        _download_workflow("owner/name", entry, token="secret-token")

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["headers"]["Accept"] == "application/vnd.github.raw+json"


def test_add_workflow_raises_when_repo_has_no_templates(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()

    with (
        patch(
            "github_rest_api.scripts.github.create_github_repo.requests.get",
            side_effect=_fake_get([], {}),
        ),
        pytest.raises(FileNotFoundError),
    ):
        _add_workflow(dest, workflows_repo="owner/name", token="")


def test_add_workflow_raises_when_repo_has_only_repo_only_workflows(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    listing = [_workflow_entry("check-workflow-template.yaml")]

    with (
        patch(
            "github_rest_api.scripts.github.create_github_repo.requests.get",
            side_effect=_fake_get(listing, {}),
        ),
        pytest.raises(FileNotFoundError),
    ):
        _add_workflow(dest, workflows_repo="owner/name", token="")


def test_add_workflow_skips_when_workflows_repo_is_empty(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()

    with patch(
        "github_rest_api.scripts.github.create_github_repo.requests.get"
    ) as mock_get:
        _add_workflow(dest, workflows_repo="", token="")

    mock_get.assert_not_called()
    assert not (dest / ".github").exists()
