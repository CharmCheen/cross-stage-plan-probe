# E−1 Luna/Sol Execution Pack v1.1

## Purpose

This package is the executable handoff for a **measurement-first falsification study**. The current research status is `INVESTIGATE`. The job is to determine whether a meaningful cross-stage physical-plan ranking reversal remains after strong simple coordination mechanisms.

The package deliberately separates roles:

- **GPT-6 Luna**: implementation, tests, deterministic execution, artifact production, mechanical debugging.
- **GPT-6.1 Sol**: specification audit, milestone approval, baseline-strength audit, measurement-validity audit, and final STOP / INVESTIGATE / GO_TO_E0 judgment.

Luna must never change research semantics or gates. Sol must never silently rewrite experiment outputs.

## First action

1. Luna reads `01_RESEARCH_FREEZE.md`, `02_LUNA_MASTER_PROMPT.md`, `04_MILESTONE_DAG.md`, and `05_REPO_CONTRACT.md`.
2. Luna creates the experiment repository and completes **M0 only**.
3. Sol audits M0 using `03_SOL_MASTER_AUDIT_PROMPT.md` and `15_SOL_AUDIT_CHECKLIST.md`.
4. Progress only through approved milestones in `machine/milestones.yaml`.

## Non-negotiable research rule

Do not build a general optimizer. Do not claim a research gap from literature absence. Do not weaken H1/H2/H3/H4. The first scientific milestone is a trustworthy cost matrix `C[d,t]` and an explanation of any crossing.

## Package map

- `01_RESEARCH_FREEZE.md`: frozen hypothesis, scope, non-goals, decision thresholds.
- `02_LUNA_MASTER_PROMPT.md`: paste directly into the implementation agent.
- `03_SOL_MASTER_AUDIT_PROMPT.md`: paste directly into the audit agent.
- `04_MILESTONE_DAG.md`: exact execution stages and approval gates.
- `05_REPO_CONTRACT.md`: required repository layout and CLI.
- `06_LEGALITY_AND_FAIRNESS.md`: semantics and resource fairness.
- `07_PLANS_BASELINES_REFERENCE.md`: D/T plans, H0–H4, REF.
- `08_TRACE_AND_MEASUREMENT.md`: instrumentation contract.
- `09_CONFIG_AND_MANIFEST.md`: config, dataset manifest, run manifest.
- `10_RUNBOOK.md`: commands and run order.
- `11_ZERO_OPPORTUNITY_CHECKS.md`: early STOP checks.
- `12_ANALYSIS_SPEC.md`: ranking, crossing, regret, uncertainty.
- `13_BLOCKER_STOP_PROTOCOL.md`: what Luna must stop on.
- `14_DECISION_GATES.md`: frozen research gates.
- `15_SOL_AUDIT_CHECKLIST.md`: audit checkpoints.
- `16_ACCEPTANCE_TESTS.md`: command-level acceptance.
- `17_DELIVERABLES.md`: final files Luna must produce.
- `18_FAILURE_MODES.md`: common invalidation risks.
- `19_SOURCE_OF_TRUTH.md`: evidence hierarchy and conflict resolution.
- `machine/milestones.yaml`: machine-readable gates.
- `schemas/*.json`: run/trace/audit schemas.
- `configs/e_minus_1.local.example.yaml`: local-first example.
- `templates/*`: required report templates.
- `source/*`: frozen research context.
