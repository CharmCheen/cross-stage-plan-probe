# M1 Implementation Note

Manifest records are canonical JSON objects in JSONL. The fixture repeats `fixture-A` under two occurrence IDs, updates, and augmentation keys, proving the sample identity does not collapse separate occurrences. Manifest hashing sorts by occurrence ID so serialization or execution order does not change identity. Legality comparison enforces equality of each frozen occurrence field while permitting records to be presented in a different execution order.

`keyed_u64` derives a stable keyed value using HMAC-SHA256. It does not consume mutable process or worker RNG state. The current contract is sufficient to replay a decision and test occurrence independence; it does not yet implement an actual image/video transform.
