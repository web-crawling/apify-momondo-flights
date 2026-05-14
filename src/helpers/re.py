from re import *


def first(pattern, string, default=None):
    if not isinstance(string, str):
        return default

    results_list = findall(pattern, string)
    return results_list[0] if len(results_list) else default
