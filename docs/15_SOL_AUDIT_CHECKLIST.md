# Sol Audit Checklist

## M0
- [ ] repo/CLI match contract
- [ ] tests pass
- [ ] experiment semantics only in configs/spec, not hidden constants
- [ ] environment manifest exists

## M2 instrumentation
- [ ] monotonic clocks
- [ ] causal event IDs
- [ ] object lifetimes correct
- [ ] live bytes reconcile with allocations/frees
- [ ] stall attribution excludes executable training work
- [ ] trace overhead measured
- [ ] no missing-event bias across plans

## M5 H1/H2
- [ ] metadata window decoupled from physical queue
- [ ] byte credits use actual/estimated bytes consistently
- [ ] demand ordering is truly implemented
- [ ] H2 charges late conversion/workspace/competition
- [ ] no semantic differences

## M7 matrix
- [ ] same manifests/seeds/resources
- [ ] T_competitive determined before crossing claims
- [ ] run order randomized/counterbalanced
- [ ] warmup/cache controlled
- [ ] dominated plans flagged
- [ ] raw traces support aggregate cells
- [ ] crossing > uncertainty

## M9 strong residual
- [ ] H3/H4 are credible and tuned only on calibration
- [ ] REF-feasible uses same information/resources
- [ ] oracle separate
- [ ] holdout results exist
- [ ] causal mechanism validated by intervention
- [ ] no artificial-starvation dependency

## M11 final
- [ ] apply gate mechanically
- [ ] negative evidence preserved
- [ ] no post-hoc metric/workload substitution
- [ ] conclusion strength matches evidence
