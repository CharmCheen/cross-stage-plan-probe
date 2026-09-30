# Structural Separability Audit — Frozen Summary

This experiment starts from the following conclusions supplied by the research audit:

- Interaction between input physical plans and training schedules can exist, but interaction does not establish the need for a new optimizer.
- Non-additivity is insufficient; independent minimization may still be globally optimal.
- The strongest negative baseline is metadata-first scheduling + demand-ordered preprocessing + byte-credit admission, which separates the logical scheduling window from the materialized tensor queue.
- Compact representation + late expansion may be a stable rule; all late conversion/workspace/resource contention must be charged.
- Ranking reversal is only decision-relevant if it persists among competitive schedules, survives dominance pruning, exceeds uncertainty, and yields regret after H1–H4.
- Memory-bounded attribution requires actual binding live-byte/admission constraints on the critical path; peak-memory differences alone are insufficient.
- The first experiment should be a small matrix and should actively attempt to falsify the need for cross-stage optimization.

This file is context, not a replacement for `01_RESEARCH_FREEZE.md`.
