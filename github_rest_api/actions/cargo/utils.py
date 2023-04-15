"""Util functions for building GitHub Actions for Rust projects.
"""
from typing import Callable
from pathlib import Path
import datetime
import subprocess as sp
from ...github import has_change


def has_rust_change(
    token: str, pr_number: int, pred: Callable[[str], bool] | None = None
):
    """Check whether a PR has any Rust-related changes.

    :param token: The authorization token for GitHub REST API.
    :param pr_number: The number of the corresponding pull request.
    :param pred: A customized boolean predictor checking Rust-related changes.
    """

    def _is_rust(file: str) -> bool:
        path = Path(file)
        if path.name in ("Cargo.toml", "Cargo.lock"):
            return True
        if path.suffix == ".rs":
            return True
        return False

    if pred is None:
        pred = _is_rust
    return has_change(token=token, pr_number=pr_number, pred=pred)


def build_project(profile: str = "release") -> None:
    """Build the Rust project.
    :param profile: The profile for building.
    """
    cmd = f"RUSTFLAGS=-Awarnings cargo build --profile {profile}"
    print("Started building at ", datetime.datetime.now(), sep="")
    sp.run(cmd, shell=True, check=True)
