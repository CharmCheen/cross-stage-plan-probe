# Zero-Opportunity Checks

These checks exist to kill the hypothesis early.

## Z0 Ready-input headroom

Compare normal execution against a controlled ready-input comparator that does not unfairly alter the declared memory budget. Estimate the maximum input-side headroom.

If exposed input headroom is consistently <5% in the target natural configuration, recommend early STOP for the current input-plan opportunity.

## Z1 Binding-memory check

A memory-bounded claim requires an actual interval where live allocation/admission reaches the configured realistic budget and blocks or changes an action relevant to the critical path.

Different peak-memory numbers without an admission consequence are insufficient.

## Z2 Metadata-first decoupling check

Implement H1 correctly:
1. inspect metadata over a large logical candidate window;
2. determine the legal consumption schedule;
3. preprocess in demand order;
4. admit materialized objects with byte credits.

If this removes the candidate-window/memory effect, STOP the complex joint-optimizer hypothesis.

## Z3 Compact dominance check

If compact+late expansion (H2), with all late costs included, is uniformly best or within noise across competitive T and realistic budgets, freeze it and STOP representation joint search.

## Z4 Competitive-schedule check

If only one training schedule remains competitive, there is no meaningful downstream-context ranking question for this workload. Stop or narrow the hypothesis.
