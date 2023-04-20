"""Some generally useful util functions.
"""
from itertools import tee, filterfalse
import logging
import subprocess as sp


def partition(pred, iterable):
    """Use a predicate to partition entries into true entries and false entries.
    :param pred: A function takes one parameter and return a boolean value.
    :param iterable: An iterable of values to partition.
    """
    it1, it2 = tee(iterable)
    return filter(pred, it1), filterfalse(pred, it2)


def run_cmd(cmd: list | str, capture_output: bool = False) -> None:
    """Run a shell command.

    :param cmd: The command to run.
    :param capture_output: Whether to capture stdout and stderr of the command.
    """
    proc = sp.run(
        cmd, shell=isinstance(cmd, str), check=True, capture_output=capture_output
    )
    logging.debug(proc.args)
