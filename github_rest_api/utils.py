"""Some generally useful util functions.
"""
from itertools import tee, filterfalse


def partition(pred, iterable):
    """Use a predicate to partition entries into true entries and false entries.
    :param pred: A function takes one parameter and return a boolean value.
    :param iterable: An iterable of values to partition.
    """
    it1, it2 = tee(iterable)
    return filter(pred, it1), filterfalse(pred, it2)
