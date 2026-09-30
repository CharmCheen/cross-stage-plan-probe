# M2 Implementation Note

Each trace row has a monotonic `ts_ns`; interval durations are differences between monotonic samples and are never computed from wall-clock datetimes. `operator_begin`/`operator_end`, `consumer_start`/`consumer_end`, wait start/end, and queue block start/end express intervals as causal event pairs. Duration values are also attached to interval end metadata where directly measured.

The fixture defines `ready` as the instant when every declared dependency needed by its consumer is available. That event follows producer completion and precedes admission; later pipelines can move the call site to their actual dependency boundary. The consumer's input-wait interval starts only when no queue item is available and the synthetic consumer has no other executable work. It is not inferred from time spent in a generic loader call.

The queue counts unreleased admitted bytes as its byte-credit occupancy, including an object held by the consumer after dequeue. This makes admission credit return coincide with the object's terminal release. A separate dequeue event records when the object leaves the queue data structure. The ledger tracks owner transitions (`queue` → `consumer`) and layer totals (`host`, `pinned`, `gpu`).

Tracing overhead uses matched fixture/config/manifest runs, alternating trace-off and trace-on mode for three pairs. `tracing_overhead.json` preserves each wall duration and median relative difference. The intentional delays dominate this tiny smoke; Sol should assess whether the implementation and diagnostic are adequate before M3.
