# M0–M2 Trace Validator State Machine

This document describes the engineering checks used to accept one complete M0–M2 run. It does not alter the frozen workload, Legal(P), consumer/update semantics, research hypothesis, or decision gates.

## Logical occurrence transitions

Each frozen `occurrence_id` has an independent logical state:

```text
PENDING → DEPENDENCIES_SATISFIED → READY → ADMITTED → DEQUEUED → EXECUTING → COMPLETED
```

Repeated dependencies, object admissions, and dequeues may leave the occurrence in its current state. Aggregate `ADMITTED` and `DEQUEUED` milestones are derived from the full set of `ready.metadata.required_inputs`; an individual object's admission/dequeue never regresses the occurrence phase. Every declared input must be admitted and dequeued before consumer start. The validator checks the expected consumer order from `(step_id, sample_order_baseline, occurrence_id)`. Producer and preprocessing intervals may appear in another order. Every frozen occurrence must have exactly one complete consumer interval and all declared required inputs.

## Dependency transitions

A `dependency_complete` event names a unique dependency ID and kind for its logical occurrence. Its `completion_event_id` must refer to an earlier completion already validated by event-stream replay: a correctly paired producer `operator_end`, or an earlier valid dependency completion. A producer root directly proves the matching dependency ID/kind. A dependency-derived completion has its own new ID/kind and names its parent with `source_dependency_id` and `source_dependency_kind`; these fields must match the validated parent identity. The root must match run, sample, occurrence, step, microbatch, and physical object identity. Self references, forward references, cycles, roots from another occurrence, and roots without a closed producer interval are invalid.

`ready.metadata.required_inputs` is a non-empty list of `{object_id, dependency_ids}` bindings. Every input object has at least one distinct, already-satisfied dependency; the union of those dependencies must exactly equal `required_dependency_ids`. One satisfied dependency may bind more than one physical input. `ready` does not make an undeclared or unsatisfied input legal.

## Physical object transitions

Each `object_id` has one allocation and a terminal release:

```text
UNALLOCATED → ALLOCATED(owner, bytes, layer, occurrence, role)
ALLOCATED → ALLOCATED(new owner) → RELEASED
```

Object ID, byte count, resource layer, occurrence, role, and allocation-time dependency bindings are immutable. Each `input` allocation must match its ready declaration and each consumer input set must exactly match the ready-declared set. Ownership transfer checks current and new owner and carries independently checked live-byte and credit snapshots. A host/GPU copy is a separate object or a separately specified copy event; ownership transfer alone does not migrate resources. Consumer access requires a live, admitted and dequeued input object for the same occurrence, with current owner `consumer` or an allocation-time `readable_by` declaration containing `consumer`. A consumer start opens an access lease for every input; a transfer during that interval is legal only if the consumer remains authorized to read the object. Input objects remain live until consumer completion; temporary objects may be released earlier. Every allocated object must reach `RELEASED` before the run ends.

## Queue and resource accounting

The validator rebuilds resource totals by `host`, `pinned`, and `gpu` from allocation and release events. It rebuilds queue item bytes from admission/dequeue and byte credit from admission/terminal release. Dequeue and ownership transfer do not return credit. Emitted snapshots are compared at each event with these independent totals. A full run ends with zero unreleased queue credits/items and the declared live-byte baseline.

## Interval pairing table

| Start | Matching end |
|---|---|
| `operator_begin` | `operator_end` |
| `consumer_start` | `consumer_end` |
| `consumer_wait_start` | `consumer_wait_end` |
| `queue_block_start` | `queue_block_end` |

Each interval ID is globally unique for the run and can be closed once. Pair type, run/sample/occurrence/step/microbatch, worker, operator, device, and object identity must match. The observed duration equals `end.ts_ns - start.ts_ns` using monotonic timestamps.

## Consumer timeline and input waits

The synthetic model has one frozen-order consumer. Closed compute intervals cannot overlap one another; exposed wait intervals cannot overlap one another or any compute interval. An exposed input wait must concern the next frozen consumer occurrence and uses the explicit `input_admission` critical dependency. It begins while at least one required input remains unavailable and ends only after a later-in-stream admission for one of that occurrence's declared inputs. Timestamp ties are resolved by event-stream order: an admission must precede `wait_end`. A wait cannot be attributed to an earlier legal occurrence that is still incomplete. Consumer `ready_time_ns` at both event and metadata level must equal the reconstructed ready-event timestamp; duplicate aliases must agree.

## Full-run validation contract

`validate_trace()` validates a full run and requires both a non-empty frozen `expected_occurrences` mapping and a valid `expected_run_context`. Every event must match the context run ID, context hash, manifest hash, input plan ID, and training plan ID. The manifest keys, manifest hash, seed-derived draw identity, frame identity, workload version, and queue budget are checked before the trace can be considered valid. Exactly one terminal `pipeline_end` must be the last event; all expected occurrences and physical objects must be terminal.
