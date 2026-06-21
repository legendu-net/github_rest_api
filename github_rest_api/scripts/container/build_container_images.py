import argparse
import datetime
import subprocess as sp
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import yaml
from dulwich.diff_tree import tree_changes
from dulwich.errors import NotGitRepository
from dulwich.objects import Commit
from dulwich.refs import Ref
from dulwich.repo import Repo
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
    image_dirs: Sequence[str],
    paths_monitoring: Sequence[str] = (),
    name1: str = "",
    name2: str = "",
) -> bool:
    if not commit1 or not commit2:
        return True
    if isinstance(commit1, str):
        commit1 = commit1.encode()
    if isinstance(commit2, str):
        commit2 = commit2.encode()
    monitored = [Path(m).resolve() for m in (*image_dirs, *paths_monitoring)]
    for p in changed_files_between(commit1, commit2, name1=name1, name2=name2):
        p_resolved = p.resolve()
        if any(p_resolved.is_relative_to(m) for m in monitored):
            return True
    return False


def has_relevant_changes_main_dev(
    image_dirs: Sequence[str], paths_monitoring: Sequence[str] = ()
) -> bool:
    try:
        c_main = _get_commit(b"main")
        c_dev = _get_commit(b"dev")
    except (KeyError, NotGitRepository):
        return True
    return has_relevant_changes(
        c_main, c_dev, image_dirs, paths_monitoring, name1="main", name2="dev"
    )


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


def _validate_paths_exist(paths: Sequence[str], label: str) -> None:
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        raise FileNotFoundError(
            f"\nError: the following {label} do not exist:\n{'\n'.join(missing)}"
        )


def build_images(
    commit1: str,
    commit2: str,
    image_dirs: Sequence[str],
    paths_monitoring: Sequence[str] = (),
    tool: str = "podman",
    registry: str = "quay.io/legendu",
):
    _validate_paths_exist(image_dirs, "image dirs")
    _validate_paths_exist(paths_monitoring, "monitored paths")
    if not has_relevant_changes(commit1, commit2, image_dirs, paths_monitoring):
        print(
            f"Skip building {tool} images as there are no relevant changes between {
                commit1
            } and {commit2}.\n"
        )
        return
    tags = ["next"]
    if not has_relevant_changes_main_dev(image_dirs, paths_monitoring):
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
        raise RuntimeError(
            f"\n\nError: failed to build images: {', '.join(failures)}\n"
        )


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
        "-y",
        "--yaml-image-dirs",
        dest="yaml_image_dirs",
        default=None,
        metavar="YAML_FILE",
        help="Path to a YAML file containing a list of image dirs to build.",
    )
    parser.add_argument(
        "--paths-monitoring",
        dest="paths_monitoring",
        nargs="*",
        default=(),
        metavar="PATH",
        help="Extra paths to monitor for changes in addition to the image directories.",
    )
    return parser.parse_args()


def _resolve_image_dirs(args: argparse.Namespace) -> list[str]:
    if args.image_dirs:
        return args.image_dirs
    with Path(args.yaml_image_dirs).open(encoding="utf-8") as fin:
        data = yaml.safe_load(fin)
        if data is None:
            return []
        if not isinstance(data, list) or not all(
            isinstance(item, str) for item in data
        ):
            raise ValueError(
                f"{args.yaml_image_dirs} must contain a YAML list of strings."
            )
        return [s for item in data if (s := item.strip())]


def main() -> int:
    args = parse_args()
    try:
        build_images(
            args.commit1,
            args.commit2,
            _resolve_image_dirs(args),
            paths_monitoring=args.paths_monitoring,
            tool=args.tool,
            registry=args.registry,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
