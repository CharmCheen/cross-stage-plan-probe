# Required Deliverables

Luna must finish with the following, even when the outcome is STOP.

## Repository artifacts
- complete source + tests;
- environment/installation instructions;
- resolved configs;
- frozen dataset manifest and exclusions;
- milestone summaries and checks;
- Sol audit decisions;
- raw traces and run manifests for retained runs;
- analysis scripts and generated tables.

## Scientific artifacts
- `C_matrix.csv` and uncertainty table;
- `ranking_map.csv`;
- dominance table;
- competitive-training-schedule table;
- zero-opportunity results;
- H0–H4 vs REF-feasible holdout table;
- causal trace report for each material crossing;
- `FINAL_DECISION_EVIDENCE.md` using the template.

## Reproducibility

Provide one command or Make target for:
- unit/integration tests;
- smoke run;
- minimal matrix run;
- analysis regeneration from existing raw traces.

Do not delete negative runs. Mark invalid runs with reasons rather than hiding them.
