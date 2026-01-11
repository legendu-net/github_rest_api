"""Util functions for building GitHub Actions for Rust projects."""

import subprocess as sp


def build_project(profile: str = "release") -> None:
    """Build the Rust project.
    :param profile: The profile for building.
    """
    sp.run(
        f"RUSTFLAGS=-Awarnings cargo build --profile {profile}", shell=True, check=True
    )
