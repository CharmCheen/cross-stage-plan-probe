# RF1–RF6 final validator convergence evidence

## Result

The explicit full-run state machine and current M0–M2 regression completed successfully on the working tree based on `e0d32755aa85abb598f03299d945b7c26d897fb7`. This evidence is prepared for Sol targeted re-audit only; it is not approval to start M3.

- Full suite: **115 passed, 0 failed, 0 skipped**.
- New adversarial module: **54 pytest cases**; the inventory contains **58 rejected scenarios** across 45 fixed RF attacks and 13 deterministic single-mutation classes.
- Positive controls: **8** independent hand-built scenarios.
- CLI doctor, config validation, manifest validation, legality check, smoke, validate-trace, and audit-export: **all PASS** in an isolated clone so historical tracked M0 artifacts were not overwritten.
- Current-schema CLI smoke trace: valid, 46 events, final live bytes zero; see `cli_run_rerun/` and `audit_export_rerun/`.

## RF closure scope

RF1–RF6 are itemized in `fixed_attack_cases.json`. `state_machine_attack_results.json` records the deterministic mutation campaign and oracle independence. `positive_controls.json` records supported valid trace forms. `trace_validation_results.json` points to the current-schema smoke validation.

## Evidence handling

Historical M0–M2 and first/second audit evidence was not rewritten. The first exploratory CLI run generated before dependency-root identity enforcement remains distinguishable as preliminary (`rf-final-5fb8eedce7ef/` and `audit_export/`); final current-schema evidence is in `cli_run_rerun/` and `audit_export_rerun/`.

No M3 plans, real video workload, GPU training, PyTorch/CUDA, optimizer, or research mechanisms were introduced.
