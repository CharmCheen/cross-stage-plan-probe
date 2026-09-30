# C1 final allocation/readiness closure

Base: `3493e4de8e86140512bc965fed0c2e8ce5f1504c`. This is a local M0–M2 engineering patch; Sol re-audit is pending and M3 has not started.

## C1 original failure

The previous Sol closure re-audit concluded `APPROVE_WITH_REQUIRED_FIXES` / `ENGINEERING_NOT_READY`. Its sole remaining MAJOR finding was that allocation required membership in a ready event that had not happened yet. A producer-owned input buffer allocated before producer execution was rejected. The independently authored 15-event lifecycle reproduces that rejection against the base validator; its historical errors are retained in `c1_counterexample_replay.json`.

## C1 exact semantic fix

Allocation records physical identity, role, layer, owner, immutable non-empty dependency binding, and live bytes without requiring prior readiness or dependency satisfaction. Ready now looks back at every declared input: it must already be allocated and alive, have the same occurrence and input role, and match its immutable binding to satisfied dependencies. Missing, released, foreign, contradictory, or future inputs cannot establish readiness. Consumer exact input-set, access lease, dependency, and lifetime checks remain unchanged.

**Allocation contributes to live bytes before readiness.** Allocation, readiness, and terminal release remain separate measurement boundaries. Queue byte credits are still acquired only on admission and returned only after terminal release; early producer allocation contributes to host memory even before admission.

## Allocation-before-ready replay

PASS, `errors == []`: allocation → operator begin/end → dependency complete → ready → producer-to-queue transfer → admission request/admission/dequeue → queue-to-consumer transfer → consumer start/end → release → credit return → pipeline end. The independent oracle uses raw event dictionaries and its own clock, byte snapshots, and HMAC draw evidence. It does not use the runtime pipeline, queue, ledger, or validator state helper. Host live bytes are 10 throughout producer work and until release, then 0. The trace and frozen run context are saved separately.

## Negative controls

Six controls PASS (each is rejected with a relevant error):

1. Ready references a never-allocated input.
2. Allocation binding and ready binding differ.
3. Dependency has not been satisfied.
4. Input belongs to another valid frozen occurrence.
5. Required input is allocated after ready.
6. Input was released before ready.

Detailed errors: `c1_negative_controls.json`. No negative assertion was removed or weakened.

## Positive controls

Three controls PASS: producer-owned buffer allocated before producer work; two preallocated inputs with separate satisfied dependencies; one satisfied dependency supporting two preallocated inputs. Detailed results: `c1_positive_controls.json`.

## F1–F5 / N6 regression

51 passed, 21 deselected in the existing adversarial module. Its physical allocation events were moved before ready; the existing dependency, wait, access, membership, aggregate-state, and timestamp negative assertions are unchanged. No F1–F5 contract was redesigned. See `f1_f5_tests.txt` and `f1_f5_regression.json`.

## RF2 / RF6 regression

18 passed, 54 deselected. Typed pairing, identity/interval lifecycle, overlap rejection, full frozen workload/context, and terminal/completion validation remain intact. Neither RF2 nor RF6 implementation was changed. See `rf2_rf6_tests.txt` and `rf2_rf6_regression.json`.

## Full pytest

143 passed, 0 failed, 0 skipped. M0: 5; M1: 9; M2/R1–R4/adversarial: 124; CLI integration: 5. The 10 new tests comprise one exact replay, six negative controls, and three positive controls. See `test_results.txt`.

Development disclosure: the first run of the new oracle had 4 failures and 6 passes because its event dictionaries omitted mandatory `plan_d` / `plan_t` aliases. The aliases were added to the oracle; validator strictness was not reduced. The subsequent suite and full regression passed. This fact is also retained in provenance.

## CLI regression

Doctor, validate-config, validate-manifest, legality-check, smoke, validate-trace, and audit-export all returned 0. They ran in an isolated clone of the base with only the final patched trace/validator sources overlaid; the captured dirty=true accurately describes that overlay. All output and current run artifacts are preserved here. The smoke has 49 events, all inputs allocate before readiness, peak reported host live bytes 120, zero final live bytes, and no validator errors. The greater producer-stage live-byte visibility is intentional measurement correction, not a queue-budget change.

## Evidence and boundary

Historical M0–M2 and previous audit artifacts are unchanged. Source changes are limited to C1 allocation/readiness binding and the synthetic fixture's corresponding physical lifecycle. No M3, real video/backend, PyTorch/CUDA, optimizer, Legal(P), research freeze, or decision threshold was changed. Capability limitations remain disclosed.

Final C1 closure re-audit gate: READY — ready for Sol to audit C1 only; no M3 approval is asserted.
