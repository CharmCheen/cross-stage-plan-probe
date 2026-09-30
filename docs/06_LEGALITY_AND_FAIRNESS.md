# Legality and Resource Fairness Contract

All comparisons must execute identical legal training work.

## Strict work identity

For each optimizer update / replay step:
- `(sample_id, occurrence_id, weight)` multiset is identical;
- selected frame timestamps/IDs are identical;
- augmentation keys/draws are identical;
- text/tokenizer/template version is identical;
- loss weights and valid-token accounting are identical;
- no sample is dropped, duplicated, or moved across updates unless the frozen config explicitly defines that behavior for every plan.

## Allowed schedule freedom

E−1 may change only explicitly listed legal order/grouping dimensions. Batch membership is frozen in the first probe unless a later approved milestone explicitly permits regrouping.

## Stateful/barrier operations

Treat as non-reorderable unless an explicit equivalent implementation is tested:
- batch-dependent augmentation;
- MixUp/CutMix;
- contrastive negatives;
- stateful decoders/iterators;
- cross-sample attention/packing semantics;
- model updates and optimizer steps.

## Resource fairness

All feasible comparisons use the same declared budget:
- CPU cores/threads;
- host memory budget;
- pinned memory budget;
- GPU count/visibility;
- GPU memory reservation where applicable;
- storage and data cache policy;
- network/remote resources if enabled.

A remote/disaggregated plan must count remote CPU/memory/network resources in the budget. A reference plan may not receive extra information or resources.

## Warm/cold state

Cache state and warmup are controlled explicitly. Do not compare cold plan A to warm plan B.

## Validation artifact

Every run produces `legality_report.json` with hashes of sample manifest, transform draws, frame IDs, and resolved plan semantics.
