"""Initial test suite for calculator."""

from __future__ import annotations

import pytest

from calculator import add, div, mul, sub


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
