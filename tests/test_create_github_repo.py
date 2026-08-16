from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.refs import HEADREF

from github_rest_api.scripts.github.create_github_repo import (
    _add_workflow,
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


def test_add_workflow_copies_templates_and_skips_repo_only_ones(tmp_path):
    workflow_dir = tmp_path / "templates"
    workflow_dir.mkdir()
    (workflow_dir / "lint.yaml").write_text("name: lint\n")
    (workflow_dir / "check_workflow_template.yaml").write_text("name: repo-only\n")
    dest = tmp_path / "repo"
    dest.mkdir()

    _add_workflow(dest, workflow_dir=workflow_dir)

    copied = {p.name for p in (dest / ".github" / "workflows").iterdir()}
    assert copied == {"lint.yaml"}


def test_add_workflow_does_not_overwrite_existing_file(tmp_path):
    workflow_dir = tmp_path / "templates"
    workflow_dir.mkdir()
    (workflow_dir / "lint.yaml").write_text("name: new\n")
    dest = tmp_path / "repo"
    dest_workflows = dest / ".github" / "workflows"
    dest_workflows.mkdir(parents=True)
    (dest_workflows / "lint.yaml").write_text("name: customized\n")

    _add_workflow(dest, workflow_dir=workflow_dir)

    assert (dest_workflows / "lint.yaml").read_text() == "name: customized\n"


def test_add_workflow_raises_when_template_dir_missing(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()

    with pytest.raises(FileNotFoundError):
        _add_workflow(dest, workflow_dir=tmp_path / "does-not-exist")


def test_add_workflow_raises_when_template_dir_has_only_repo_only_workflows(tmp_path):
    workflow_dir = tmp_path / "templates"
    workflow_dir.mkdir()
    (workflow_dir / "check_workflow_template.yaml").write_text("name: repo-only\n")
    dest = tmp_path / "repo"
    dest.mkdir()

    with pytest.raises(FileNotFoundError):
        _add_workflow(dest, workflow_dir=workflow_dir)
