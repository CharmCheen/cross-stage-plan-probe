# Plans, Strong Baselines, and Reference

## Minimal input-plan dimensions

Start with a small, interpretable set. Do not add dimensions without Sol/user approval.

### Representation axis
- `D_repr_early`: expanded/normalized representation queued early.
- `D_repr_compact`: compact representation queued; equivalent expansion occurs late and all costs are charged.

### Granularity axis
- `D_gran_whole`: process/release at whole-sample or whole-microbatch granularity.
- `D_gran_chunk`: legal chunked processing/release with explicit setup/vectorization overhead.

Combine only after single-axis probes establish that both are valid and nontrivial.

## Training schedules

- `T_fifo`: frozen legal order.
- `T_meta`: metadata-derived training-cost schedule.
- `T_two_cost`: simple prep+train/deadline heuristic, only after H3 milestone.

Only competitive schedules enter reversal claims.

## Strong baselines

### H0 independent stage tuning
Tune input-side throughput/latency and training-only schedule independently, then compose.

### H1 metadata-first + demand-ordered preprocessing + byte credits
Large logical metadata window; small physical tensor queue; decode/process according to planned demand; admission controlled by bytes/live memory.

### H2 compact + late expansion rule
Use compact representation until late conversion. Charge conversion, transfer, workspace, GPU competition and any loss of batching efficiency.

### H3 simple cross-stage greedy
A transparent heuristic using a small fixed set of features such as prep cost, train cost, deadline/slack, and live-byte pressure. No learned model required initially.

### H4 small joint tuning
Finite grid or multi-start coordinate/alternating search on the same candidate set and calibration window. Evaluate on holdout.

## Dominance pruning

Before claiming a crossing, remove a plan if another plan is weakly no worse on all relevant measured physical properties under comparable demand and strictly better on at least one, unless downstream-dependent behavior prevents a valid dominance statement.

## Reference

`REF-feasible`: measured best-known legal combination from the finite D×T candidate set using the same information and resource budget. It is not a global oracle.

`REF-oracle`: hindsight trace schedule using future measured costs. Report only as an upper-bound diagnostic. Never compute feasible regret against it.
