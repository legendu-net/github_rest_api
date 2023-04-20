"""Util functions for building GitHub Actions for Rust projects.
"""
from ...utils import run_cmd


def build_project(profile: str = "release") -> None:
    """Build the Rust project.
    :param profile: The profile for building.
    """
    run_cmd(f"RUSTFLAGS=-Awarnings cargo build --profile {profile}")
