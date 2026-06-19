"""calculator — extends pr-1 with `mod` and `pow`."""

from __future__ import annotations


def add(a: float, b: float) -> float:
    return a + b


def sub(a: float, b: float) -> float:
    return a - b


def mul(a: float, b: float) -> float:
    return a * b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def mod(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("modulo by zero")
    return a % b


def pow_(a: float, b: float) -> float:
    return a ** b
