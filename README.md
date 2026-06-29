# pyrun

`pyrun` is a Python-backed host automation runtime prototype inspired by
Hostrun. It exists to test whether a persistent Python runtime feels better than
Hostrun's embedded QuickJS model for host-side automation, structured command
execution, filesystem helpers, and reusable scratch state.

This is a local prototype only. It uses the Python standard library and avoids
`shell=True` for command execution.

## Usage

Run code through the JSONL adapter:

```sh
printf '%s\n' \
  '{"session_id":"s","code":"ctx.count = 41"}' \
  '{"session_id":"s","code":"ctx.count += 1\nctx.count"}' |
  python -m pyrun.jsonl
```

Each input line is a JSON object:

```json
{"session_id":"optional-session", "code":"1 + 2"}
```

Each output line is either:

```json
{"type":"completed","executed":"1 + 2","console":[],"value":3}
```

or:

```json
{"type":"error","executed":"1 / 0","error":"division by zero"}
```

## Runtime API

Sessions are persistent and keyed by `session_id`. If omitted, the default
session is named `default`.

```python
ctx.count = 1
ctx.count += 1
ctx.count
```

Available globals:

- `ctx`: persistent dict-like object with attribute access.
- `host.cwd()`, `host.cd(path)`.
- `fs.read(path)`, `fs.write(path, content)`, `fs.exists(path)`,
  `fs.remove(path)`, `fs.glob(pattern)`.
- `cli.<program>(*args)`: command builder. Uses argv-style execution and no
  shell parsing.
- `run.<program>(*args)`: immediate command execution.

Command results expose:

- `stdout`
- `stderr`
- `exit_code`
- `text()`
- `lines()`
- `json()`

Command builders support:

```python
cli.python3('-c', 'print(123)').run()
cli.python3('-c', 'print(123)').text()
cli.python3('-c', 'print(123)').lines()
cli.python3('-c', 'import json; print(json.dumps({"ok": True}))').json()
cli.python3('-c', 'print(open("x").read())').in_('/tmp').run()
cli.python3('-c', 'import sys; print(sys.stdin.read())').stdin_text('hello').run()
```

## Differences from Hostrun

- Runtime language is Python instead of QuickJS JavaScript.
- This prototype does not implement approval gates or sandboxing.
- Helpers are simple Python objects, not JS proxies.
- Statement evaluation returns the final trailing expression when present.
- Print output is captured as `console` lines in completed results.

## Tests

```sh
python -m unittest
```

The test suite uses only the standard library.
