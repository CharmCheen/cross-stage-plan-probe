# Command-Level Runbook

Commands below define the intended order. Exact environment setup may vary but must be recorded.

## M0

```bash
python -m pytest -q
python -m cspp.cli doctor --config configs/e_minus_1.local.yaml
python -m cspp.cli validate-config --config configs/e_minus_1.local.yaml
```

## M1

```bash
python -m cspp.cli validate-manifest --manifest data/manifests/e1.jsonl
python -m cspp.cli legality-check --config configs/e_minus_1.local.yaml
```

## M2

```bash
python -m cspp.cli smoke --config configs/e_minus_1.local.yaml
python -m cspp.cli audit-export --run <smoke_run> --out artifacts/milestones/M2/audit_export
```

STOP for Sol audit.

## M3/M4 calibration

```bash
python -m cspp.cli calibrate-input --config configs/e_minus_1.local.yaml
python -m cspp.cli calibrate-training --config configs/e_minus_1.local.yaml
```

## M5 strong baselines

Run matched H1/H2 smoke and calibration configs. Export raw traces. STOP for Sol audit.

## M6 zero-opportunity checks

Execute ready-input comparator, memory-binding check, and H1 test as defined in `11_ZERO_OPPORTUNITY_CHECKS.md`.

## M7 matrix

Start with 2×2. Only after it is valid, expand to the approved D×T matrix.

```bash
python -m cspp.cli run-matrix --config configs/e_minus_1.local.yaml --matrix configs/matrix_e1.yaml
python -m cspp.cli analyze --runs 'artifacts/runs/*' --out artifacts/analysis/e1_matrix
```

STOP for Sol audit.

## M8/M9

Add H3/H4 and feasible reference, then rerun on holdout. No new dimensions.

## M10

Forbidden until explicit Sol approval. Real forward/backward validation should test only a small selected subset of cells necessary to validate replay conclusions.
