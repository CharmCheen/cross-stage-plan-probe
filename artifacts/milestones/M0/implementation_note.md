# M0 Implementation Note

Used setuptools with a `src/cspp` package, PyYAML as the single runtime dependency, and pytest as a development dependency. The CLI works through `python -m cspp.cli` or the installed `cspp` command. The doctor probes Windows CIM, `nvidia-smi`, optional PyTorch, FFmpeg, storage capacity/filesystem, and Git. Probe failures retain an UNKNOWN value and reason. It does not install or configure hardware software.

The original M0 probe ran before Git initialization and recorded Git fields as UNKNOWN. After the user initialized Git, the refreshed M0 environment artifact records the valid `main` worktree, verified origin, staged dirty state, and `PRE-COMMIT` (no HEAD existed at probe time). The historical limitation is retained and marked resolved in `BLOCKERS.md`.
