# Trace and Measurement Contract

## Required timing rule

Use monotonic high-resolution clocks. Cross-process/machine traces require explicit synchronization or causal event linking.

## Required event classes

At minimum record:
- run/step/rank/sample/occurrence/microbatch IDs;
- input plan and training plan IDs;
- operator begin/end;
- decode begin/end;
- transform begin/end;
- queue admission/request/block/unblock;
- object allocation/ownership transfer/free;
- bytes in/out and representation ID;
- host/pinned/GPU live bytes and workspace where observable;
- ready event for each sample/microbatch;
- training consumer request/start/end;
- stall start/end with reason;
- transfer/copy events if used;
- metadata features used by scheduling.

## Exposed stall

`next(loader)` latency is not automatically GPU-exposed stall. Exposed data stall is time when the training consumer has no legal executable work and the critical unsatisfied dependency is data readiness.

Do not double-count rank idle, barrier idle, and data stall as separate wall-time savings.

## Live bytes

Track allocation from actual admission/allocation until the last valid release/ownership transfer. Do not assume all input tensors live until the end of training.

Required layers when measurable:
- ordinary host;
- pinned host;
- GPU input/preprocessing;
- preprocessing workspace.

## Measurement overhead

M2 must estimate tracing overhead using tracing-off vs tracing-on matched smoke runs. If overhead is material or plan-dependent, Sol must review before proceeding.

## Run repetition

Screening may use short windows. Any research decision must use multiple independent runs/seeds and held-out samples/configuration windows as specified later.
