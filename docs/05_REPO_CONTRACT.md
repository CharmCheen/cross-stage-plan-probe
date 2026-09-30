# Repository and CLI Contract

## Required repository tree

```text
cross-stage-plan-probe/
  pyproject.toml
  README.md
  Makefile
  src/cspp/
    cli.py
    config.py
    manifest.py
    legality.py
    video/
    plans/input/
    plans/training/
    baselines/
    runtime/
    tracing/
    replay/
    analysis/
  configs/
  data/manifests/
  tests/unit/
  tests/integration/
  scripts/
  artifacts/
    runs/
    milestones/
    analysis/
  audits/
  docs/
  BLOCKERS.md
```

## Python/software baseline

- Python 3.11+.
- `pyproject.toml` is mandatory.
- `pytest` mandatory.
- Type hints for public interfaces.
- A single structured config loader; no experiment semantics hidden in ad-hoc CLI flags.
- Prefer Parquet/JSONL for trace data and JSON/YAML for manifests/configs.
- Video baseline must work with CPU decoding first. GPU decode is optional and cannot replace the CPU baseline silently.

## Required CLI

Luna must implement these stable commands:

```bash
python -m cspp.cli doctor --config <yaml>
python -m cspp.cli validate-config --config <yaml>
python -m cspp.cli validate-manifest --manifest <jsonl>
python -m cspp.cli legality-check --config <yaml>
python -m cspp.cli smoke --config <yaml>
python -m cspp.cli calibrate-input --config <yaml>
python -m cspp.cli calibrate-training --config <yaml>
python -m cspp.cli run --config <yaml> --plan-d <id> --plan-t <id> --seed <n>
python -m cspp.cli run-matrix --config <yaml> --matrix <yaml>
python -m cspp.cli analyze --runs <glob> --out <dir>
python -m cspp.cli audit-export --run <run_id> --out <dir>
```

Each command must exit nonzero on invalid research semantics or incomplete artifacts.

## Determinism

Every run writes a complete resolved config and run manifest before executing. IDs, seeds, sample occurrences, and plan definitions must be stable and hashable.

## No hidden state

Environment-dependent behavior that affects measurements (thread counts, CUDA visibility, codec backend, cache mode, filesystem path, CPU affinity if used) must be recorded in the run manifest.
