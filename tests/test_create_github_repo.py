from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.refs import HEADREF

from github_rest_api.scripts.github.create_github_repo import _init_local_repo

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
        language="",
        dir_=str(dir_),
        token="",
        protocol="git",
        push=False,
        branches=branches,
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
