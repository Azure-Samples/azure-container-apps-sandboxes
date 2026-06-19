# 05-data-processing — Python flavor

See the [scenario README](../README.md) for the full architecture, the
when-to-use comparison with `04-swarms/02-shared-blob-memory`, and the
production tips.

## Quick start

```bash
pip install -r requirements.txt
python pipeline.py
```

Tune with environment variables:

```bash
PIPELINE_BATCHES=50 PIPELINE_EVENTS_PER_BATCH=500 python pipeline.py
```

## Files

| File | Where it runs | What it does |
|---|---|---|
| [`pipeline.py`](pipeline.py) | Host (this laptop / CI) | Provisions volume + 3 sandboxes, runs producer/transformer concurrently, then aggregator, parses RESULT. |
| [`workers/producer.py`](workers/producer.py) | Producer sandbox | Writes `raw/batch-NNN.jsonl` files in a loop. |
| [`workers/transformer.py`](workers/transformer.py) | Transformer sandbox | Polls `raw/`, enriches batches, writes `processed/`, archives sources to `raw/.done/`. |
| [`workers/aggregator.py`](workers/aggregator.py) | Aggregator sandbox | Reads `processed/`, computes summary, prints `RESULT={json}`. |

The worker scripts are pure stdlib — they make no Azure SDK calls. They
read and write the AzureBlob volume through `/mnt/shared/...` just as
they would any other Linux directory.
