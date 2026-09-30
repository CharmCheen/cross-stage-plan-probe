# Frozen Decision Gates

## STOP

Recommend STOP for the current joint-plan hypothesis when any of the following is robustly established:
- one input plan dominates across competitive schedules/budgets;
- no decision-relevant crossing or feasibility coupling exists;
- input bottleneck exists but is purely stage-local;
- H1 or H2 eliminates the residual;
- H3/H4 are within roughly 5–10% of REF on natural configurations;
- large effects require cold start, unrealistic memory starvation, extra oracle information, or semantic changes;
- replay opportunity disappears under selected real-training validation;
- residual is actually encoder/LLM execution already covered by the training-side problem class.

## INVESTIGATE once more

Use only when evidence is promising but incomplete, e.g. 5–15% residual, one workload, or unstable CI. One bounded replication/validation round is allowed. Do not expand scope.

## GO_TO_E0

Requires all:
- at least two independent realistic workload/configuration families;
- stable held-out residual roughly ≥15–20% against H1–H4;
- strict legality passes;
- competitive T only;
- causal chain closes to a specific missing cross-stage physical property/interface;
- simple fixes and small joint tuning fail;
- real execution validates any replay/simulation conclusion;
- resource cost remains plausible for the intended research setting.

GO_TO_E0 authorizes designing a narrow method. It does not establish a new mathematical problem or a publishable paper.
