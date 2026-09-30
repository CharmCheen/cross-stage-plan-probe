# F1–F5 validator closure patch

Base: `31e01e2b751a6003cf2fa05e2681bd269528d8aa`. This is engineering-only M0–M2 validator work and requests a final Sol re-audit; it is not approval to start M3.

## F1 — Derived dependency identity

- Original counterexample: a valid derived dependency with a new dependency ID/kind was rejected because the validator required the source root to have the same identity.
- Fix: producer roots still prove their declared dependency directly. A dependency-derived completion retains its own ID/kind and explicitly names the earlier satisfied source through `source_dependency_id` and `source_dependency_kind`; causal identity and same-occurrence checks remain in force.
- Independent replay: a three-level derived chain with distinct identities validates; a forged source identity is rejected.
- Positive control: PASS. Negative control: PASS.

## F2 — Wait causality and exclusivity

- Original counterexamples: a wait could end before a same-timestamp admission later in the event stream; overlapping waits could be accepted.
- Fix: availability admission must be strictly earlier in event-stream order than `wait_end`, even on timestamp ties. Availability may be any declared required input of that occurrence. The single-consumer timeline rejects overlapping waits while allowing adjacent intervals.
- Independent replay: `wait_end → same-time admission` rejected; `admission → same-time wait_end` accepted; partial, nested, and same-start waits rejected; adjacent waits for two required inputs accepted.
- Positive/negative controls: PASS.

## F3 — Active consumer access lease

- Original counterexample: access was checked at consumer start only, allowing ownership transfer to revoke read permission during compute.
- Fix: consumer start records active input leases. Ownership transfer during compute is rejected if it removes consumer read access; a transfer retaining allocation-time `readable_by` is permitted. Leases close at consumer end.
- Independent replay: revoked access rejected; explicit retained read access accepted.
- Positive/negative controls: PASS.

## F4 — Input-to-dependency binding

- Original counterexample: a second physical input without a satisfied logical dependency could pass based only on object lifecycle.
- Fix: `ready.metadata.required_inputs` binds each required object ID to one or more satisfied dependency IDs. The union must exactly equal `required_dependency_ids`; allocations repeat and match their binding; consumer input IDs must exactly cover the ready-declared set. One satisfied dependency may support multiple physical inputs.
- Independent replay: unsatisfied second dependency, unknown dependency, foreign dependency, omitted input, and forged transfer dependency binding rejected. Two/three inputs with satisfied dependencies and one dependency supporting two physical inputs accepted.
- Positive/negative controls: PASS.

## F5 — Aggregate multi-input state

- Original counterexample: occurrence phase advanced on each object event, so admitting B after dequeuing A appeared to regress `DEQUEUED → ADMITTED`.
- Fix: admitted/dequeued IDs remain per object; aggregate milestones derive only when all ready-declared required inputs reach each milestone. Consumer start still requires every input admitted, dequeued, live, bound, and readable.
- Independent replay: `admit A → dequeue A → admit B → dequeue B → consume {A,B}` accepted; `admit A/B → dequeue B/A → consume` accepted; missing dequeue rejected.
- Positive/negative controls: PASS.

## N6 — Ready timestamp consistency

If a ready event or consumer start contains both top-level and metadata `ready_time_ns`, the values must match; consumer timestamps remain checked against the reconstructed ready event. An independently mutated ready alias mismatch is rejected.

## RF2 regression

Typed interval pairing, cross-identity rejection, global interval ID uniqueness, duplicate/unmatched endpoints, compute overlap rejection, and an explicit adjacent-compute positive control remain covered. Targeted RF2 tests: 11 passed.

## RF6 regression

Mandatory expected workload/context and rejection of missing context, foreign run/context, terminal-only, partial/extra workload, and duplicate logical execution remain covered. Targeted RF6 tests: 7 passed.

## Overall verification

- Full pytest: **133 passed, 0 failed, 0 skipped**.
- Targeted F1–F5/N6 suite: **72 passed**; RF2/RF6 targeted regression: **18 passed**.
- M0: **5 passed**; M1: **9 passed**; M2/R1–R4/adversarial: **114 passed**; CLI integration tests are included in the full suite.
- Isolated CLI sequence on the final patched validator: doctor, validate-config, validate-manifest, legality-check, smoke, validate-trace, audit-export all **PASS**. Final current-schema evidence is in `smoke_run_final/`, `audit_export_final/`, and `cli_results_final.json`.
- No historical audit artifact was overwritten. No M3, video backend, PyTorch/CUDA, real workload, optimizer, or research mechanism was added.

## Gate

**Final Sol closure re-audit gate: READY** — ready for Sol final closure re-audit only. This does not mean APPROVED, ENGINEERING_READY_FOR_M3, or M3 approved.
