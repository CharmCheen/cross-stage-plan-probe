# Source of Truth and Conflict Resolution

Priority order:

1. Latest explicit user instruction.
2. `01_RESEARCH_FREEZE.md` and user-approved spec-change files.
3. Legal/fairness and decision-gate documents in this package.
4. Machine-readable milestone/config/schema files.
5. Latest approved Sol audit decision.
6. Implementation docs/comments.
7. Prior research audit/context files.

If implementation and frozen spec conflict, the spec wins and the implementation must be fixed.

If a prior paper or source suggests a different research scope, do not silently change this experiment. Record it as context and request a spec revision.

Evidence labels:
- FACT: directly supported by observed run data or cited source.
- INFERENCE: derived interpretation.
- HYPOTHESIS: testable but unverified.
- UNKNOWN: insufficient evidence.

Luna should mostly produce FACTs about implementation and runs. Sol is responsible for bounded INFERENCE about research implications.
