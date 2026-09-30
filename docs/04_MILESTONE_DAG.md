# Milestone DAG

The experiment is intentionally staged to maximize information gain and minimize wasted compute.

| ID | Milestone | Main output | Sol audit? | May early-stop? |
|---|---|---|---|---|
| M0 | Repository/bootstrap | reproducible env, CLI skeleton, tests | yes | no |
| M1 | Dataset/config/legality layer | manifest + deterministic legal work | no | yes on unavailable media |
| M2 | Instrumentation | trustworthy event trace/live-byte accounting | **yes** | yes if measurement invalid |
| M3 | Input physical plans | D candidates + dominance metadata | no | yes if one plan trivially dominates |
| M4 | Replay training schedules | competitive T set + training-only calibration | no | yes if only one competitive schedule |
| M5 | H1/H2 strong baselines | metadata-first byte-credit; compact/late | **yes** | **yes** |
| M6 | Zero-opportunity checks | ready-input headroom; memory-binding; H1 test | no | **yes** |
| M7 | Minimal 2×2 then D×T matrix | `C[d,t]`, rank maps, raw traces | **yes** | **yes** |
| M8 | H3/H4 + feasible reference | simple coordination vs finite joint enum | no | **yes** |
| M9 | Holdout/statistics/causal checks | regret CI + causal diagnostics | **yes** | **yes** |
| M10 | Optional real GPU validation | confirm replay-selected cells | yes | yes |
| M11 | Final decision pack | STOP / INVESTIGATE / GO_TO_E0 evidence | **yes** | final |

## Hard progression rule

A later milestone cannot be used to retroactively redefine an earlier metric or plan. Any semantic change creates a new spec version and invalidates affected comparisons.

## Default local-first stopping

M0–M9 should be possible without multi-GPU hardware. M10 is forbidden unless M9 is approved and evidence justifies the cost.
