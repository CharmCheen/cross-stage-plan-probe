# Config and Manifest Contract

## Dataset manifest

Use a JSONL manifest. Each occurrence must include or resolve:
- stable `sample_id`;
- video path/content hash;
- duration;
- resolution;
- codec/container when obtainable;
- compressed bytes;
- fixed frame-selection specification or frame IDs;
- text/token metadata if used;
- split/source/group identifiers for leakage-safe holdout.

Never silently skip missing/corrupt videos. Record and freeze exclusions before comparative runs.

## Config principles

All measurement-relevant settings live in YAML and resolve to a saved JSON snapshot:
- resource budgets;
- decoder/backend;
- worker/thread counts;
- queue byte cap;
- representation;
- chunk/granularity;
- training schedule;
- warmup and measurement windows;
- seeds;
- trace level;
- cache policy;
- replay profile source.

## Calibration split

Separate:
- calibration / plan-selection window;
- holdout evaluation window.

Do not tune H3/H4 on the final holdout.

## Competitive schedule selection

Training-only calibration must output schedule costs and identify `T_competitive` before D×T crossing analysis.
