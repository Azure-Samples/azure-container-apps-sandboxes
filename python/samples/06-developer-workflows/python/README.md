# 06-developer-workflows — Python flavor

See the [scenario README](../README.md) for the full architecture,
sequence diagram, and production tips.

## Quick start

```bash
pip install -r requirements.txt

python ci.py                    # build the default 3 PRs (1 + 2 pass, 3 fails)
python ci.py pr-1 pr-2          # build a subset
python ci.py pr-3               # see a failing build in isolation
```

The script's exit code matches CI conventions:

- `0` — every selected PR passed
- `2` — at least one PR failed
- `1` — infrastructure error (sandbox setup, snapshot, pytest install)

## Files

| Path | What it is |
|---|---|
| [`ci.py`](ci.py) | Host orchestrator. Cold-boots a base sandbox, installs pytest, snapshots, then runs the PR sandboxes in parallel from the snapshot. |
| [`prs/pr-1/`](prs/pr-1/) | Initial calculator implementation + tests. Should pass. |
| [`prs/pr-2/`](prs/pr-2/) | Extends pr-1 with `mod()` and `pow()` + new tests. Should pass. |
| [`prs/pr-3/`](prs/pr-3/) | Regression in `mul()` (`a+b` instead of `a*b`). Should fail — the regression is what makes this PR a useful demo of CI catching bugs. |

To plug in your own PR sources, drop them in as `prs/<your-pr-name>/`
(any number of files; they're uploaded into `/workspace/` inside the
PR sandbox) and pass the name on the command line.

## Customising

- `DISK` (constant in `ci.py`) — change the base disk image
  (default `python-3.14`).
- `SETUP_CMD` — what to run on the base sandbox before snapshotting.
  Add your test deps here (`pip install -r common-requirements.txt` etc.).
- `SNAPSHOT_WARMUP_S` — how long to wait after `begin_create_sandbox`
  before exec'ing on a snapshot-restored sandbox.
