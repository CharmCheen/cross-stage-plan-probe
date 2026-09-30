# Failure and Risk Register

1. **Weak baseline masquerading as strong**: H1 lacks large metadata window or byte credits.
2. **Cost hiding**: late conversion or GPU preprocessing cost charged outside measured boundary.
3. **Memory fiction**: peak bytes differ but admission never binds.
4. **Logical/physical window conflation**: decoded queue size incorrectly limits scheduling candidate IDs.
5. **Noncompetitive schedule crossing**: reversal created only by a bad training order.
6. **Cold/warm asymmetry**: cache or decoder warmup differs by plan.
7. **Oracle leakage**: future measured times enter feasible scheduler.
8. **Replay optimism**: simulator/replay misses resource contention or nonlinear kernels.
9. **Semantic drift**: frame sampling, augmentation, batch membership, or loss differs.
10. **Resource unfairness**: remote/extra workers not charged.
11. **Tiny-GPU artifact**: 4 GB local GPU creates pathological pressure not representative of target claim.
12. **Storage noise**: background I/O or filesystem caching drives apparent crossing.
13. **Trace perturbation**: instrumentation changes plan ranking.
14. **Multiple-comparison storytelling**: cherry-picking crossing cells after a large grid.
15. **Scope creep after null result**: adding dimensions until something crosses.

Every final report must state which risks were tested, unresolved, or inapplicable.
