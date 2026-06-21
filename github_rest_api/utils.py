"""Some generally useful util functions."""

import re
from collections.abc import Sequence
from itertools import filterfalse, tee


def partition(pred, iterable):
    """Use a predicate to partition entries into true entries and false entries.
    :param pred: A function takes one parameter and return a boolean value.
    :param iterable: An iterable of values to partition.
    """
    it1, it2 = tee(iterable)
    return filter(pred, it1), filterfalse(pred, it2)


def compile_patterns(patterns: str | Sequence[str] | None) -> list[re.Pattern[str]]:
    """Compile a list of regular expression patterns.

    :param patterns: A list of regular expression patterns to compile.
    :return: A list of compiled regular expression patterns.
    """
    if not patterns:
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as e:
            raise ValueError(
                f"Invalid regular expression pattern '{pattern}': {e}"
            ) from e
    return compiled


def strip_patch_version(version: str) -> str:
    """Strip the patch version.

    For example,
    - strip_patch_version("5.4.0") ==> "5.4.0"
    - strip_patch_version("5.4.6") ==> "5.4.0"
    - strip_patch_version("2.3") ==> "2.3.0"
    - strip_patch_version("1") ==> "1.0.0"
    """
    parts = version.strip().split(".")
    n = len(parts)
    if n < 1 or n > 3:
        raise ValueError("Invalid version semantic provided!")
    while len(parts) < 3:
        parts.append("0")
    parts[2] = "0"
    return ".".join(parts)


def next_minor_or_strip_patch(version: str, patch_to_bump: int) -> str:
    """Get the next minor version of strip the patch version.

    If the patch version is less than patch_to_bump, then strip the patch version;
    otherwise, bump version to the next minor one.
    For example,
    - next_minor_or_strip_patch("5.4.6", 4) ==> "5.5.0"
    - next_minor_or_strip_patch("5.4.6", 8) ==> "5.4.0"
    """
    parts = version.strip().split(".")
    n = len(parts)
    if n < 1 or n > 3:
        raise ValueError("Invalid version semantic provided!")
    while len(parts) < 3:
        parts.append("0")
    major, minor, patch = map(int, parts)
    if patch >= patch_to_bump:
        minor += 1
    return f"{major}.{minor}.0"


def strip_minor_version(version: str) -> str:
    parts = version.split(".")
    match len(parts):
        case 1:
            return parts[0] + ".0.0"
        case 2 | 3:
            return ".".join(parts[:1]) + ".0.0"
        case _:
            raise ValueError("Invalid version semantic provided!")
