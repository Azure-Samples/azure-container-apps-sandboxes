"""Producer worker: streams synthetic event batches onto the shared volume.

Runs inside a sandbox with ``/mnt/shared`` mounted. Pure stdlib, no
Azure SDK and no network. Each batch is written to a temp file and then
``os.replace``d into place so a reader globbing ``raw/batch-*.jsonl``
never catches a half-written file. When every batch is on disk the
producer drops a ``.producer-done`` sentinel so the transformer knows the
stream has ended.

Config comes from environment variables set by the host (``pipeline.py``):

    MOUNTPOINT        shared volume mount    (default /mnt/shared)
    BATCHES           number of batches      (default 20)
    EVENTS_PER_BATCH  events per batch        (default 100)
    BATCH_DELAY_S     sleep between batches   (default 0.5)
    SEED              RNG seed for repeatable output (default 42)
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

MOUNT = Path(os.environ.get("MOUNTPOINT", "/mnt/shared"))
RAW = MOUNT / "raw"

BATCHES = int(os.environ.get("BATCHES", "20"))
EVENTS_PER_BATCH = int(os.environ.get("EVENTS_PER_BATCH", "100"))
BATCH_DELAY_S = float(os.environ.get("BATCH_DELAY_S", "0.5"))
SEED = int(os.environ.get("SEED", "42"))

# A fixed base epoch keeps the generated timestamps (and therefore the
# hour-of-day histogram) deterministic for a given seed.
BASE_TS = 1_700_000_000  # 2023-11-14T22:13:20Z
WINDOW_S = 7 * 24 * 60 * 60  # spread events across a week

USERS = [f"u{i:04d}" for i in range(100)]
EVENT_TYPES = [
    ("page_view", 0.60),
    ("click", 0.25),
    ("logout", 0.10),
    ("purchase", 0.04),
    ("signup", 0.01),
]


def _weighted_type(rnd: random.Random) -> str:
    roll = rnd.random()
    cumulative = 0.0
    for name, weight in EVENT_TYPES:
        cumulative += weight
        if roll < cumulative:
            return name
    return EVENT_TYPES[-1][0]


def _make_event(rnd: random.Random, batch: int, idx: int) -> dict:
    etype = _weighted_type(rnd)
    event = {
        "id": f"{batch:03d}-{idx:04d}",
        "ts": BASE_TS + rnd.randint(0, WINDOW_S),
        "user": rnd.choice(USERS),
        "type": etype,
    }
    if etype == "purchase":
        event["value"] = round(rnd.uniform(5.0, 500.0), 2)
    return event


def _write_batch(batch: int, events: list[dict]) -> Path:
    """Atomically publish one batch as JSONL."""
    final = RAW / f"batch-{batch:03d}.jsonl"
    tmp = RAW / f".batch-{batch:03d}.jsonl.tmp"
    payload = "".join(json.dumps(e) + "\n" for e in events)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, final)
    return final


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(SEED)

    total = 0
    for batch in range(BATCHES):
        events = [_make_event(rnd, batch, i) for i in range(EVENTS_PER_BATCH)]
        path = _write_batch(batch, events)
        total += len(events)
        print(f"produced {path.name}  ({len(events)} events)")
        if batch < BATCHES - 1:
            time.sleep(BATCH_DELAY_S)

    # Sentinel: written last, atomically, so the transformer only sees it
    # after every batch is durable on the volume.
    sentinel_tmp = RAW / ".producer-done.tmp"
    sentinel_tmp.write_text(str(total), encoding="utf-8")
    os.replace(sentinel_tmp, RAW / ".producer-done")

    print(f"producer done: {BATCHES} batches, {total} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
