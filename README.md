# cross-stage-plan-probe

The repository implements milestones M0–M2 of the frozen E−1 execution pack. It contains a
bootstrap, a strict deterministic workload identity contract, and a synthetic instrumentation
fixture. It does not run an E−1 matrix or implement physical plans or research mechanisms.

## Setup

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run software and acceptance checks with `python -m pytest -q`. The required module CLI is
`python -m cspp.cli`; `cspp` is also installed as a console script.

## M0–M2 commands

```powershell
python -m cspp.cli doctor --config configs/m0_m2.local.yaml
python -m cspp.cli validate-config --config configs/m0_m2.local.yaml
python -m cspp.cli validate-manifest --manifest data/manifests/e1.jsonl
python -m cspp.cli legality-check --config configs/m0_m2.local.yaml
python -m cspp.cli smoke --config configs/m0_m2.local.yaml
python -m cspp.cli audit-export --run m2-synthetic-smoke --out artifacts/milestones/M2/audit_export
```

`doctor` writes `artifacts/environment.json` and a readable summary. The config is deliberately a
small deterministic fixture, not the E−1 workload configuration. Missing host capabilities are
recorded as `UNKNOWN` with a reason.

## Data and trace contract

The JSONL manifest freezes sample and occurrence identity, update and microbatch membership, order,
frame IDs/specification, keyed augmentation randomness, weight, and workload version. A SHA-256
digest identifies canonical manifest content. Trace timestamps and durations use
`time.perf_counter_ns`; UTC wall time is not used for interval measurements. The synthetic
producer-consumer trace includes bounded queue credits and per-resource live-byte events.

See `docs/` for the frozen research and engineering contract. M2 output is synthetic correctness
evidence only and is not an experimental result.
