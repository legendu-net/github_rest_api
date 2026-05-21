import argparse
import datetime
from pathlib import Path
import subprocess as sp
import sys
from typing import cast
from dulwich.repo import Repo
from dulwich.refs import Ref
from dulwich.objects import Commit
from dulwich.errors import NotGitRepository
from dulwich.diff_tree import tree_changes
from tenacity import retry, stop_after_attempt, wait_exponential


def _get_commit(name: bytes) -> bytes:
    """Resolve a commit SHA or branch name to a commit SHA string."""
    repo = Repo(".")
    if name in repo:
        return name
    for prefix in [b"refs/heads/", b"refs/remotes/origin/", b"refs/tags/"]:
        ref = cast(Ref, prefix + name)
        if ref in repo.refs:
            return repo.refs[ref]
    raise KeyError(f"Cannot resolve commit or branch: {name}")


def changed_files_between(
    commit1: bytes, commit2: bytes, name1: str = "", name2: str = ""
) -> list[Path]:
    """Get a unique list of changed files between 2 commits.

    :param commit1: The first commit ID.
    :param commit2: The second commit ID.
    :param name1: Optional human-readable name for commit1 used in logging (defaults to the commit SHA).
    :param name2: Optional human-readable name for commit2 used in logging (defaults to the commit SHA).
    :return: A unique list of changed files.
    """
    repo = Repo(".")
    c1 = cast(Commit, repo[commit1])
    c2 = cast(Commit, repo[commit2])
    changes = tree_changes(repo.object_store, c1.tree, c2.tree)
    files = set()
    for change in changes:
        if change.old and change.old.path:
            files.add(change.old.path.decode())
        if change.new and change.new.path:
            files.add(change.new.path.decode())
    paths = sorted(Path(file) for file in files)
    print(
        f"Changed files between {name1 or commit1.decode()[:7]} and {
            name2 or commit2.decode()[:7]
        }:"
    )
    for p in paths:
        print(f"  {p}")
    return paths


def has_relevant_changes(
    commit1: str | bytes,
    commit2: str | bytes,
    image_dirs: list[str],
    name1: str = "",
    name2: str = "",
) -> bool:
    if not commit1 or not commit2:
        return True
    if isinstance(commit1, str):
        commit1 = commit1.encode()
    if isinstance(commit2, str):
        commit2 = commit2.encode()
    dirs = [Path(d).resolve() for d in image_dirs]
    for p in changed_files_between(commit1, commit2, name1=name1, name2=name2):
        if any(p.resolve().is_relative_to(d) for d in dirs):
            return True
    return False


def has_relevant_changes_main_dev(image_dirs: list[str]) -> bool:
    try:
        c_main = _get_commit(b"main")
        c_dev = _get_commit(b"dev")
    except (KeyError, NotGitRepository):
        return True
    return has_relevant_changes(c_main, c_dev, image_dirs, name1="main", name2="dev")


def _tag_date(tag: str) -> str:
    """Suffix a tag with the current date as a 6-digit string.

    :param tag: A tag of a Podman image.
    :return: A new tag.
    """
    return tag + datetime.datetime.now(tz=datetime.timezone.utc).strftime("_%m%d%H")


@retry(
    stop=stop_after_attempt(3), wait=wait_exponential(multiplier=60, min=60, max=300)
)
def _push_image(image: str, tool: str = "podman"):
    sp.run(
        [tool, "push", image],
        shell=False,
        check=True,
    )


def _build_image(
    image_dir: str,
    tags: str | list[str],
    tool: str = "podman",
    registry: str = "quay.io/legendu",
):
    if isinstance(tags, str):
        tags = [tags]
    image = f"{registry}/{image_dir}"
    print(f"\n\nBuilding the {tool} image {image}...", flush=True)
    cmd = [tool, "build", image_dir]
    for tag in tags:
        cmd.append("-t")
        cmd.append(f"{image}:{tag}")
    sp.run(cmd, shell=False, check=True)
    for tag in tags:
        _push_image(f"{image}:{tag}", tool=tool)


def build_images(
    commit1: str,
    commit2: str,
    image_dirs: list[str],
    tool: str = "podman",
    registry: str = "quay.io/legendu",
):
    if not has_relevant_changes(commit1, commit2, image_dirs):
        print(
            f"Skip building {tool} images as there are no relevant changes between {
                commit1
            } and {commit2}.\n"
        )
        return
    tags = ["next"]
    if not has_relevant_changes_main_dev(image_dirs):
        tags.append("latest")
    tags.extend([_tag_date(tag) for tag in tags])
    print(f"Building {tool} images using tags:", ", ".join(tags), "\n", flush=True)
    failures = []
    for image_dir in image_dirs:
        try:
            _build_image(image_dir, tags=tags, tool=tool, registry=registry)
        except (sp.CalledProcessError, FileNotFoundError) as e:
            print(f"Error building {image_dir}: {e}", flush=True)
            failures.append(image_dir)
    if failures:
        sys.exit(f"\n\nError: failed to build images: {', '.join(failures)}\n")


def parse_args():
    """Parse command-line arguments.

    :return: An object containing the parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Build container images.")
    parser.add_argument(
        "-c1",
        "--commit1",
        dest="commit1",
        default="",
        help="The first commit ID (empty by default).",
    )
    parser.add_argument(
        "-c2",
        "--commit2",
        dest="commit2",
        default="",
        help="The second commit ID (empty by default).",
    )
    parser.add_argument(
        "-r",
        "--registry",
        dest="registry",
        default="quay.io/legendu",
        help="Container registry prefix (default: quay.io/legendu).",
    )
    parser.add_argument(
        "-t",
        "--tool",
        dest="tool",
        default="podman",
        choices=["podman", "docker"],
        help="Container tool to use for building and pushing images (default: podman).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-i",
        "--image-dirs",
        dest="image_dirs",
        nargs="+",
        default=None,
        metavar="IMAGE_DIR",
        help="Explicit list of image directories to build.",
    )
    group.add_argument(
        "-f",
        "--file-image-dirs",
        dest="file_image_dirs",
        default=None,
        metavar="FILE",
        help="Path to a file listing image directories to build, one per line.",
    )
    return parser.parse_args()


def _resolve_image_dirs(args: argparse.Namespace) -> list[str]:
    if args.image_dirs:
        return args.image_dirs
    lines = Path(args.file_image_dirs).read_text().splitlines()
    return [line.strip() for line in lines if line.strip()]


def main():
    args = parse_args()
    build_images(
        args.commit1,
        args.commit2,
        _resolve_image_dirs(args),
        tool=args.tool,
        registry=args.registry,
    )


if __name__ == "__main__":
    main()
