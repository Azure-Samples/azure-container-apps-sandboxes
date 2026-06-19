# Tests

Two tiers, both driven by pytest from this single directory.

## Offline gate (default, no Azure)

Fast invariant checks meant to block merge in CI: every sample Python
file compiles, the SDK and the 08 editable extension import, and the
tree-level conformance rules hold.

```bash
uv run --group test pytest
```

The default run excludes e2e (see `addopts` in pyproject.toml). The
three audit specs in `test_conformance.py` show up as xfail: they track
confirmed gaps and flip to xpass once the corresponding fixes land.

## On-demand e2e (real Azure runs)

Opt-in only. Runs real sample executions end to end.

Prerequisites:

```bash
az login
uv run python/samples/setup/setup.py   # provisions python/samples/.env
```

Then:

```bash
RUN_E2E=1 uv run --group test pytest tests/e2e
```

Without `RUN_E2E=1` these tests are collected but skipped.

## Layout

```
tests/
  conftest.py            shared helpers, e2e skip gate
  test_compile.py        py_compile every python/ source (offline)
  test_imports.py        SDK + editable extension import (offline)
  test_conformance.py    tree invariants + 3 xfail audit specs (offline)
  e2e/
    test_live_samples.py real sample runs (on-demand)
```
