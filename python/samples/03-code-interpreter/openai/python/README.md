# 03 — Code Interpreter — Azure OpenAI (Python)

The runnable demo. See the
[parent README](../README.md) for prerequisites, AOAI configuration,
example output, and customization tips.

## Quick start

```bash
pip install -r requirements.txt
python run.py
```

The default prompt analyses [`data/sales.csv`](data/sales.csv). Override
with any data-analysis question:

```bash
python run.py "What's the YoY trend by channel?"
python run.py --model gpt-4o-mini "..."
python run.py --max-turns 8 "..."
```

Plots the model saves under `/workspace/out/` inside the sandbox land in
`./out/` on the host.
