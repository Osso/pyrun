# Command execution

Pyrun exposes immediate `run.*` commands and configurable `cli.*` command builders from `pyrun/runtime.py`.

## What it must do

- [x] `cli.*.run()` forwards stdout and stderr and returns the integer exit code.
- [x] `cli.*.capture().run()` suppresses forwarded output and returns a `CommandResult`.
- [x] Default and captured builder executions remain available through `run.history()`.
- [x] Builder configuration such as `.cwd()`, `.env()`, `.input()`, `.output()`, and `.timeout()` composes with explicit capture.
- [x] Capture mode is visible when a configured builder is serialized.

## How it works

- [Runtime command documentation](../../README.md)

## Implementation inventory

- `pyrun/runtime.py` — command builders, execution, forwarding, capture, and history.

## Tests asserting this spec

- `tests/test_runtime.py`

## Known gaps (current cycle)

None.

## Out of scope

- Changing `spawn()` and process-handle result semantics.
- Adding shell-string command execution.
