# Command-Level Acceptance Tests

The repository is not accepted merely because code runs.

## Core software

```bash
python -m pytest -q
python -m cspp.cli doctor --config configs/e_minus_1.local.yaml
python -m cspp.cli validate-config --config configs/e_minus_1.local.yaml
python -m cspp.cli validate-manifest --manifest data/manifests/e1.jsonl
python -m cspp.cli legality-check --config configs/e_minus_1.local.yaml
```

All must pass from a clean environment documented in README.

## Trace consistency tests

Automated tests must verify:
- allocations minus frees equal final live bytes;
- no negative live-byte values;
- required causal IDs exist;
- ready cannot precede required producer completion;
- training consumer start respects readiness and legal order;
- sample/frame/augmentation hashes match across compared plans.

## Byte-credit tests

Create deterministic synthetic cases proving:
- item-count queue and byte-credit queue differ when sizes differ;
- H1 supports a large metadata window with a small physical queue;
- blocked admission unblocks only after relevant release.

## Replay tests

Given a fixed trace/profile, replay must be deterministic within the declared timing model. Any stochastic replay component requires seed recording.

## Analysis tests

Use synthetic matrices to verify:
- ranking and ties;
- crossing detection;
- dominance pruning;
- feasible regret computation;
- REF-oracle is rejected as feasible denominator.
