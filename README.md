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
- `fs.write_json(path, value, indent=2)`: writes JSON plus a trailing newline.
- `fs.write_json_lines(path, values)` / `fs.write_jsonl(path, values)`: writes
  one JSON value per line.
- `fs.write_csv(path, rows)` / `fs.write_tsv(path, rows)`: writes list rows or
  dict rows. Dict headers are the ordered union of keys across all rows.
- `fs.open(path, format=None)`: reads text and parses by explicit format or file
  extension for `json`, `jsonl`, `csv`, `tsv`, `txt`/`text`, and `toml` when
  stdlib `tomllib` is available. Unsupported formats raise `ValueError`.
- `tools.file.replace(path, from_or_options, to=None)`: exact text replacement.
  By default it requires exactly one match. Options dict supports `from`, `to`,
  `all`, and `occurrence`.
- `tools.file.patch(path_or_patch, maybe_patch=None)`: applies unified diff
  hunks. With one argument, parses `---`/`+++` file headers, normalizes `a/`
  and `b/` prefixes, and supports new files from `/dev/null`. With two
  arguments, the first is the explicit target path and the second may start
  directly with `@@` hunks. Context and removal lines must match exactly;
  deletion patches are rejected.
- `tmp.file(prefix='tmp', suffix='')` and `tmp.dir(prefix='tmp')`: temporary
  handles with `cleanup()`. File handles also support `write`, `write_json`,
  `write_json_lines` / `write_jsonl`, `write_csv`, and `write_tsv`.
- `http.request(method, url, options=None)` plus `http.get/post/put/patch/delete/head`.
  Options support `headers`, raw `body`, `json`, and `form`. Builders expose
  `run()`, `text()`, `json()`, `bytes()`, and `to_file(path)`.
- `http.session(options=None)`: returns a client with optional `base_url` and
  shared default `headers`. Client methods match global HTTP helpers. Relative
  URLs join against `base_url`; per-request headers override or extend session
  headers.
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
- HTTP uses stdlib `urllib.request`; no retry, cookie jar, or streaming support
  exists yet.
- Filesystem helpers cover the first structured-data slice only. YAML is not
  supported without a future non-stdlib decision.

## Examples

```python
fs.write_json('data.json', {'ok': True})
fs.open('data.json')

fs.write_csv('items.csv', [{'name': 'apple'}, {'name': 'pear', 'count': 2}])
fs.open('items.csv')

tools.file.replace('note.txt', {'from': 'old', 'to': 'new', 'occurrence': 1})
tools.file.patch('note.txt', '@@ -1 +1 @@\n-old\n+new\n')
tools.file.patch('''--- a/note.txt
+++ b/note.txt
@@ -1 +1 @@
-old
+new
''')

f = tmp.file(prefix='pyrun-', suffix='.jsonl')
f.write_jsonl([{'id': 1}, {'id': 2}])
fs.open(str(f))
f.cleanup()

http.get('http://127.0.0.1:8000/status').text()
http.post('http://127.0.0.1:8000/items', {'json': {'name': 'apple'}}).json()
client = http.session({'base_url': 'http://127.0.0.1:8000', 'headers': {'X-App': 'pyrun'}})
client.get('/status', {'headers': {'X-Trace': '1'}}).json()
```

## Tests

```sh
python -m unittest
```

The test suite uses only the standard library.
