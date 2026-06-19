"""Offline gate: every tracked Python file under python/ compiles.

One parametrized test per file. This catches syntax errors across all
samples without importing or executing anything, so it stays fast and
needs no Azure.
"""

import py_compile

import pytest

from conftest import REPO_ROOT, tracked_py_files

PY_FILES = tracked_py_files("python/**/*.py")


@pytest.mark.parametrize(
    "py_file",
    PY_FILES,
    ids=[str(p.relative_to(REPO_ROOT)) for p in PY_FILES],
)
def test_python_file_compiles(py_file):
    py_compile.compile(str(py_file), doraise=True)
