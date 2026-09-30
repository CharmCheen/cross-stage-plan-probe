# Analysis and Statistics Specification

## Primary table

Produce measured wall-time matrix `C[d,t]` with uncertainty and sample counts.

## Ranking map

For each competitive training schedule, rank input plans using held-out mean/median wall time with uncertainty-aware ties. Report Spearman/Kendall stability as descriptive diagnostics, not causal proof.

## Crossing test

A candidate crossing must satisfy:
- same workload and legal work;
- same resource budget;
- both training schedules competitive;
- effect larger than uncertainty/noise;
- repeatable across run order/randomization;
- no simple third plan dominance;
- not explained solely by cold start/cache state.

## Regret

Feasible regret:

`Regret = (T_strong - T_REF_feasible) / T_REF_feasible`.

Report on holdout. Do not use trace oracle in this denominator.

## Causal diagnostics

For each crossing, trace:
- which object/request was blocked;
- which budget/resource caused blocking;
- object lifetime and last-use event;
- whether it affected a critical consumer;
- whether extra capacity removes the effect;
- whether demand-order/H1 removes it;
- whether representation or granularity change alters it in the predicted direction.

## Required negative analyses

- ready-input headroom;
- memory non-binding controls;
- warm/cold sensitivity;
- run-order randomization;
- metadata-only prediction/scheduling baseline;
- dominance pruning;
- H1–H4 residual.

## Do not report

- sum of rank-idle time as wall-time savings;
- p99 from tiny samples;
- speedup against an untuned default as the central result;
- simulator-only speedup as real-system evidence.
