"""Extended test suite covering the new mod / pow operations."""

from __future__ import annotations

import pytest

from calculator import add, div, mod, mul, pow_, sub


def test_add():
    assert add(2, 3) == 5


def test_sub():
    assert sub(10, 4) == 6


def test_mul():
    assert mul(3, 7) == 21


def test_div():
    assert div(20, 4) == 5


def test_div_by_zero():
    with pytest.raises(ZeroDivisionError):
        div(1, 0)


def test_mod():
    assert mod(10, 3) == 1


def test_mod_by_zero():
    with pytest.raises(ZeroDivisionError):
        mod(1, 0)


def test_pow():
    assert pow_(2, 10) == 1024
    assert pow_(5, 0) == 1
