# Frozen Research State

Status: **INVESTIGATE**.

## Frozen hypothesis

In real compressed-video training, after using metadata-first training order, demand-ordered preprocessing, compact representation, byte-credit admission, and simple cross-stage heuristics, do input representation and execution granularity still alter release-time / live-byte / shared-resource behavior enough to cause a **stable, decision-relevant physical-plan ranking reversal** and measurable end-to-end regret?

This is a hypothesis, not an established gap.

## Scientific unit of evidence

For input plans `d ∈ D` and competitive training schedules `t ∈ T_competitive`, measure wall-clock cost:

`C[d,t] = completion time of identical legal training work under identical resource budget`.

A meaningful crossing requires `C[A,t1] < C[B,t1]` and `C[A,t2] > C[B,t2]`, beyond uncertainty, with neither A nor B trivially dominated by a third simple plan.

The research opportunity is **regret after strong simple baselines**, not crossing by itself.

## Frozen non-goals

Until a positive E−1 gate:

- no generic multimodal optimizer;
- no Cascades/Volcano implementation;
- no MAB/RL planner;
- no PP/TP search;
- no large distributed cluster study;
- no embodied-specific story;
- no paper title/system name;
- no claim that joint preprocessing/training optimization is novel;
- no tuning that changes sample membership, augmentation draw, loss, or model semantics.

## Competitive training schedule filter

Do not manufacture crossing using deliberately bad training schedules. `T_competitive` contains only legal schedules whose standalone training-only cost is within the configured competitiveness margin of the best-known training schedule. Default research threshold: 10%; report sensitivity at 5% where feasible.

## Frozen decision thresholds

These are project investment gates, not community standards.

- Most natural configurations <5–10% regret after H1–H4: **STOP current optimizer hypothesis**.
- Stable ≥15–20% regret on at least two independent realistic workloads/configurations, strict legality, H1–H4 fail, causal mechanism closed: **GO_TO_E0**.
- 5–15% or one unstable workload: **INVESTIGATE once more only**.

Luna may not modify these thresholds. Sol may recommend a change only by creating an explicit `SPEC_CHANGE_REQUEST.md`; the user must approve before implementation.
