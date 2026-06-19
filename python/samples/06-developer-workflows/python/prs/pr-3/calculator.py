"""calculator — pr-3 introduces a regression in mul().

This PR should be rejected by CI.
"""

from __future__ import annotations


def add(a: float, b: float) -> float:
    return a + b


def sub(a: float, b: float) -> float:
    return a - b


def mul(a: float, b: float) -> float:
    # Regression: refactor swapped the operator. Test should catch this.
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b
