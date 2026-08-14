#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12.7,<4"
# dependencies = [
#     "github-rest-api",
# ]
# ///
"""Run cargo test."""

import subprocess as sp
from argparse import ArgumentParser, Namespace
from pathlib import Path

from github_rest_api.github import Repository


def parse_args(args=None, namespace=None) -> Namespace:
    """Parse command-line arguments.
    :param args: The arguments to parse.
        If None, the arguments from command-line are parsed.
    :param namespace: An inital Namespace object.
    :return: A namespace object containing parsed options.
    """
    parser = ArgumentParser(
        description="Run benchmark for the Rust project and save results to gh-pages."
    )
    parser.add_argument(
        "--token",
        dest="token",
        required=True,
        help="The personal access token for authentication.",
    )
    parser.add_argument(
        "--pr-number",
        dest="pr_number",
        required=True,
        type=int,
        help="The PR number (if any) corresponding to this benchmark.",
    )
    return parser.parse_args(args=args, namespace=namespace)


def _is_rust(file: str) -> bool:
    path = Path(file)
    if path.name in ("Cargo.toml", "Cargo.lock"):
        return True
    if path.match("**/data/test/*.yaml"):
        return True
    if "benches" in path.parts:
        return False
    if path.suffix == ".rs":
        return True
    return False


def main() -> None:
    """Main function of the script."""
    args = parse_args()
    repo = Repository(token=args.token, repo="fun-poker-game/poker-rs")
    if repo.pr_has_rust_change(args.pr_number, _is_rust):
        sp.run("cargo test --locked", shell=True, check=True)


if __name__ == "__main__":
    main()
