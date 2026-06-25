"""Transformer worker: drains raw batches, enriches them, archives sources.

Runs concurrently with the producer in a second sandbox on the same
shared volume. It polls ``raw/batch-*.jsonl``; for each new batch it
enriches every event, writes the result to ``processed/batch-NNN.jsonl``
(atomically), then moves the source into ``raw/.done/`` so it is never
re-processed. It exits once the producer has dropped ``.producer-done``
and a couple of poll cycles go by with nothing new to do.

Enrichment is deliberately simple and pure stdlib: it derives the
UTC hour-of-day from the event timestamp and flags revenue events. The
aggregator reads these derived fields, so the contract between the two
workers lives entirely in the JSONL on the volume.

Config (environment, set by ``pipeline.py``):

    MOUNTPOINT  shared volume mount  (default /mnt/shared)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

MOUNT = Path(os.environ.get("MOUNTPOINT", "/mnt/shared"))
RAW = MOUNT / "raw"
DONE = RAW / ".done"
PROCESSED = MOUNT / "processed"
SENTINEL = RAW / ".producer-done"

POLL_INTERVAL_S = 0.25
# Number of consecutive empty polls (after the producer is done) we wait
# before declaring the stream fully drained.
QUIET_POLLS_TO_STOP = 3


def _enrich(event: dict) -> dict:
    ts = float(event.get("ts", 0))
    hour = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).hour
    value = float(event.get("value", 0) or 0)
    return {
        **event,
        "hour": hour,
        "revenue": value > 0,
        "value": value,
    }


def _transform_batch(src: Path) -> int:
    events = [
        _enrich(json.loads(line))
        for line in src.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out = PROCESSED / src.name
    tmp = PROCESSED / f".{src.name}.tmp"
    tmp.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    os.replace(tmp, out)
    # Archive the source rather than deleting it (replayable, idempotent).
    os.replace(src, DONE / src.name)
    return len(events)


def main() -> int:
    for d in (RAW, DONE, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)

    processed_files = 0
    processed_events = 0
    quiet_polls = 0

    while True:
        batches = sorted(RAW.glob("batch-*.jsonl"))
        if batches:
            quiet_polls = 0
            for src in batches:
                count = _transform_batch(src)
                processed_files += 1
                processed_events += count
                print(f"transformed {src.name} -> processed/  ({count} events)")
            continue

        if SENTINEL.exists():
            quiet_polls += 1
            if quiet_polls >= QUIET_POLLS_TO_STOP:
                break
        time.sleep(POLL_INTERVAL_S)

    print(f"transformer done: {processed_files} batches, {processed_events} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
