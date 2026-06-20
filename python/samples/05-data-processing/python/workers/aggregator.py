"""Aggregator worker: summarises the processed batches into one report.

Runs once, in a third sandbox, after the producer and transformer have
drained. It globs ``processed/batch-*.jsonl``, folds every event into a
handful of counters, writes ``summary/report.json`` to the volume, and
prints a single ``RESULT={json}`` line on stdout for the host to parse.

Pure stdlib (``glob`` + ``json`` + ``collections``). No Azure SDK, no
network. The host (``pipeline.py``) reads the ``RESULT=`` line and renders
the final report.

Config (environment, set by ``pipeline.py``):

    MOUNTPOINT  shared volume mount  (default /mnt/shared)
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

MOUNT = Path(os.environ.get("MOUNTPOINT", "/mnt/shared"))
PROCESSED = MOUNT / "processed"
SUMMARY = MOUNT / "summary"

TOP_N_USERS = 10
TOP_N_HOURS = 10


def main() -> int:
    files = sorted(PROCESSED.glob("batch-*.jsonl"))

    events_total = 0
    revenue_events = 0
    total_value = 0.0
    by_type: Counter[str] = Counter()
    by_user: Counter[str] = Counter()
    by_hour: Counter[int] = Counter()

    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            events_total += 1
            by_type[event["type"]] += 1
            by_user[event["user"]] += 1
            by_hour[int(event["hour"])] += 1
            value = float(event.get("value", 0) or 0)
            if value > 0:
                revenue_events += 1
                total_value += value

    report = {
        "files_read": len(files),
        "events_total": events_total,
        "revenue_events": revenue_events,
        "total_value": round(total_value, 2),
        "avg_value": round(total_value / events_total, 4) if events_total else 0.0,
        "events_by_type": dict(by_type.most_common()),
        "top_users": [[u, n] for u, n in by_user.most_common(TOP_N_USERS)],
        "top_hours": [[h, n] for h, n in by_hour.most_common(TOP_N_HOURS)],
    }

    SUMMARY.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY / ".report.json.tmp"
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, SUMMARY / "report.json")

    print("RESULT=" + json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
