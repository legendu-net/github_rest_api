"""Util functions for building GitHub Actions for Rust projects.
"""
import datetime
import subprocess as sp


def build_project(profile: str = "release") -> None:
    """Build the Rust project.
    :param profile: The profile for building.
    """
    cmd = f"RUSTFLAGS=-Awarnings cargo build --profile {profile}"
    print("Started building at ", datetime.datetime.now(), sep="")
    sp.run(cmd, shell=True, check=True)
