from alpha.core import run


def public_a():
    return run()


class A:
    pass


def _private():
    return 1
