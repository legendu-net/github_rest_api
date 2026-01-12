"""Some generally useful util functions."""

from itertools import tee, filterfalse


def partition(pred, iterable):
    """Use a predicate to partition entries into true entries and false entries.
    :param pred: A function takes one parameter and return a boolean value.
    :param iterable: An iterable of values to partition.
    """
    it1, it2 = tee(iterable)
    return filter(pred, it1), filterfalse(pred, it2)


def strip_patch_version(version: str) -> str:
    parts = version.split(".")
    match len(parts):
        case 1:
            return parts[0] + ".0.0"
        case 2 | 3:
            return ".".join(parts[:2]) + ".0"
        case _:
            raise ValueError("Invalid version semantic provided!")


def strip_minor_version(version: str) -> str:
    parts = version.split(".")
    match len(parts):
        case 1:
            return parts[0] + ".0.0"
        case 2 | 3:
            return ".".join(parts[:1]) + ".0.0"
        case _:
            raise ValueError("Invalid version semantic provided!")
