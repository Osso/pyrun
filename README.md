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

### JSONL protocol

Each input line is one JSON object. `code` is required and must be a string.
`session_id` is optional and must be a string when present; omitted requests use
`default`.

```json
{"session_id":"optional-session", "code":"1 + 2"}
```

Each output line is either a completed evaluation:

```json
{"type":"completed","executed":"1 + 2","console":[],"value":3}
```

When using a pending-approval `SessionStore`, side-effect helpers return a
`needs_approval` result instead of executing:

```json
{"type":"needs_approval","executed":"fs.write('note.txt', 'hi')","console":[],"approval":{"id":"approval-1","tool":"fs.write","summary":"Write /tmp/note.txt","args":{"path":"/tmp/note.txt"}}}
```

The JSONL adapter constructs the default auto-approve store, matching the
`pyrun-mcp`/`hostrun-mcp` style: side effects execute unless library callers opt
into pending approval.

or an error shape for invalid requests or evaluation failures:

```json
{"type":"error","executed":"1 / 0","error":"division by zero"}
```

Invalid JSON, non-object requests, missing/non-string `code`, and non-string
`session_id` are reported as error objects. Runtime helper objects such as
command builders, command streams, command results, HTTP request/response
objects, bytes, and `hr(...)` wrappers are converted to JSON-compatible values.

### MCP stdio server

`pyrun` also exposes a minimal MCP stdio server:

```sh
python -m pyrun.mcp
# or, when installed from pyproject scripts:
pyrun-mcp
```

The server uses MCP JSON-RPC messages framed with `Content-Length` headers. It
supports `initialize`, `notifications/initialized`, `tools/list`, and
`tools/call`. The single tool is `pyrun_eval`, which evaluates synchronous
Python in a persistent Pyrun session.

Tool input schema:

```json
{
  "type": "object",
  "properties": {
    "session_id": {"type": "string"},
    "code": {"type": "string"}
  },
  "required": ["code"]
}
```

Successful `tools/call` responses include `content` with pretty-printed JSON and
`structuredContent.result` with the raw JSON-compatible Pyrun result. Unknown
tools and invalid params return `isError: true` tool results. The MCP stdio
server also uses an auto-approve `SessionStore`, so filesystem writes, commands,
HTTP requests, and temporary-file operations execute by default.

## Runtime API

Sessions are persistent and keyed by `session_id`. If omitted, the default
session is named `default`.

Library callers can choose approval behavior:

```python
from pyrun.runtime import SessionStore

auto = SessionStore()                  # default: side effects execute
also_auto = SessionStore.new_auto_approve()
pending = SessionStore.pending_approval()
# equivalent: SessionStore(auto_approve=False)
```

Pending mode allows read-only helpers and session-local state changes such as
`ctx` updates or `host.cd(...)`, but gates host side effects. Gated operations
raise internally and are caught by `SessionStore.evaluate`, which returns a
`needs_approval` result. Gated tools include `fs.write`, structured fs writers,
`fs.remove`, `tools.file.replace/patch` writes, command `.run()`/`.spawn()`, HTTP
request execution, HTTP downloads, and temporary file/directory create, write,
and cleanup operations.

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
  `run()`, `text()`, `json()`, `bytes()`, and `to_file(path)`. Relative
  `to_file` paths resolve against the session cwd.
- `http.session(options=None)`: returns a client with optional `base_url` and
  shared default `headers`. Client methods match global HTTP helpers. Relative
  URLs join against `base_url`; per-request headers override or extend session
  headers.
- `fd.find(pattern='.', options=None)`: pure-Python file discovery under
  `options.root` or the session cwd. Returns paths relative to the session cwd
  unless `absolute_path` is true. Options: `root`, `type` (`file` or
  `directory`), `extension`, `max_depth`, `absolute_path`, `glob`, `hidden`,
  `exclude`, and accepted no-op `ignored`. Hidden paths are skipped by default.
  `fd.files(root='.', options=None)` and `fd.dirs(root='.', options=None)` are
  convenience filters.
- `rg.search(pattern, paths=None, options=None)`: pure-Python text search over
  files or directories. Returns a result with `stdout`, `stderr`, `exit_code`,
  `text()`, `lines()`, and `json()`. `rg(pattern, ...)` is an alias for
  `rg.search`. Options: `fixed`, `ignore_case`, `files_with_matches`,
  `max_count`, `glob`, `context`, `hidden`, and `json`. `context` is currently
  accepted but output remains match lines only. `rg.files(...)` returns matching
  file paths. `rg.matches(...)` returns dictionaries with `path`, `line_number`,
  `line`, and `submatches` (`text`, `start`, `end`). Text files are decoded with
  replacement for invalid bytes.
- `sqlite.query(database, sql, options=None)`: runs SQL with stdlib `sqlite3`.
  Relative database paths resolve against the session cwd. Queries returning
  rows produce a list of dict rows. Non-row statements return
  `{'rows_affected': n}`. `options.json=False` is accepted for parity planning,
  but this prototype still returns rows/dicts rather than formatted CLI text.
- `kubectl.get(resource, options=None)`: returns a `kubectl get` command builder.
  Options include `name`, `namespace`, `all_namespaces`, `selector`, and
  `output` (default `json`).
- `tools.sudo(command_builder)`: wraps a command builder with `authsudo`,
  preserving argv, stdin, cwd, environment overrides, and environment inheritance.
- `tools.powershell(script, options=None)`: returns a `pwsh`/PowerShell builder
  using `-NoProfile -EncodedCommand` with UTF-16LE base64. Use
  `{'executable': 'powershell'}` or another executable to override `pwsh`.
- `tools.ssh(options=None)`: returns an SSH helper with `run(command_builder)`
  and `cli(command_builder_or_string)`. It builds argv-style `ssh` commands,
  optionally via `sshpass -p` for `password_mode='plain'`; it does not execute
  unless the returned builder is run.
- `tools.browser`: command-builder wrappers around `browser-cli`: `open(url)`,
  `get(name)`, `snapshot(options=None)`, `exceptions(options=None)`, and
  `console(options=None)`.
- `tools.git.status(options=None)`: executes `git status --short --branch` and
  returns text. `cwd` or `repo` selects the repository.
- `tools.git.build_commit(options)` builds a safe `git commit --file -` command
  using stdin for the message; `tools.git.commit(options)` executes it. Options
  require `subject` (or `message`) and support `body`, `body_lines`, `paths`/`files`/`path`/
  `file`, `cwd`/`repo`, `amend`, `no_edit`, `allow_empty`, `no_verify`,
  `signoff`, and `all`. Literal newlines in `subject` are rejected.
- `tools.github.pr_view`/`run_view`/`create_pr` return `gh` command builders;
  camelCase aliases are also available for those methods.
- `tools.tmux`: returns tmux command builders for `command(...)`, `open(name)`,
  `close(target)`, `send(target, keys)`, `capture(target)`, and a lightweight
  `run(target, command)` shape containing send/capture builders.
- `text`: string helper namespace. Includes `lines(value, start=None, end=None)`
  with 1-based inclusive ranges, `range(value, start, end=None)` as an alias
  for `lines`, `line_count`, `word_count`, `head`, `tail`,
  `split_row`, `split_words`, `trim`/`trimmed`, `replace_text`, `json`,
  `json_lines`/`jsonl`, `lower`, `upper`, `chars`, `bytes_count`/`byte_count`,
  `byte_array`, and `csv`/`tsv` for parsing delimited text or formatting rows.
- `seq`: list/sequence helper namespace. Includes text filters
  (`containing`, `not_containing`, `starts_with`, `ends_with`, `matching`,
  `not_matching`, `glob`, `not_glob`), collection helpers (`first`, `last`,
  `take`/`head`, `tail`, `join_text`, `unique`, `compact`, `default`, `wrap`,
  `enumerate`, `is_empty`, `is_not_empty`), predicates and aggregates
  (`any`, `all`, `sum`, `avg`, `min`, `max`, `round`, `lengths`), transforms
  (`lower`, `upper`, `sorted`, `reversed`), projection helpers (`get`,
  `pluck`/`values_of`, `select`, `reject`, `where`), and serializers
  (`to_csv`, `to_tsv`, `to_json_lines`).
- `obj`: dict/object helper namespace. Includes dotted-path `get`, `select`,
  `reject`, `rename`, `insert`, `update`, `merge`, `columns`, `values`,
  `entries`, and `items`.
- `hr(value)`: small wrapper factory dispatching to `text`, `seq`, or `obj` by
  value type, for fluent calls like `hr('a\nb').lines()`,
  `hr(rows).where({'kind': 'fruit'}).select('name')`, or
  `hr({'a': 1}).select('a')`. Python cannot safely patch builtins the way
  Hostrun patches JavaScript prototypes, so these helpers are explicit globals
  instead. Wrapper values are unwrapped during JSONL result conversion.
- `run.<program>(*args)`: preferred helper for routine command execution. It uses
  argv-style execution with no shell parsing and returns a `CommandResult`
  directly. Command names are resolved dynamically via attribute access, so
  `dir(run)` may be empty even when `run.niri(...)` or another command works.
- `cli.<program>(*args)`: advanced command-builder helper. Use it when you need
  capture helpers, piping, cwd/env/stdin configuration, streams, spawning,
  redirects, or inspection. Returning a builder from JSONL/session evaluation
  serializes it as `{program, args, cwd, env, stdin}`.

Command results expose:

- `stdout`
- `stderr`
- `exit_code`
- `text()`
- `lines()`
- `json()`

Use `run` first for routine command execution:

```python
run.ls('-la')
run.python3('-c', 'print(123)')
run.python3('-c', 'print(123)').text()
run.python3('-c', 'print(123)').lines()
run.python3('-c', 'import json; print(json.dumps({"ok": True}))').json()
```

Use `cli` when you need command-builder features:

```python
cli.python3('-c', 'print(open("x").read())').in_('/tmp').run()
cli.python3('-c', 'import os; print(os.environ["NAME"])').env('NAME', 'value').run()
cli.python3('-c', 'import os; print(os.environ)').env_clear().env('NAME', 'value').run()
cli.python3('-c', 'import sys; print(sys.stdin.read())').stdin_text('hello').run()

producer = cli.python3('-c', 'print("hello")')
consumer = cli.python3('-c', 'import sys; print(sys.stdin.read().upper())')
consumer.stdin_from(producer).run()
producer.pipe_to(consumer)

stderr_producer = cli.python3('-c', 'import sys; print("warning", file=sys.stderr)')
consumer.stdin_from(stderr_producer.stderr_stream()).run()
```

`env(name, value)` and `env(dict)` add or override environment variables.
Commands inherit `os.environ` by default. Use `env_inherit(False)` or
`env_clear()` to run with only explicit overrides, or an empty environment when
no overrides are set.

`stdin_from(source, stream='stdout')` accepts another `CommandBuilder`, a
`CommandStream` from `.stream()`, `.stdout_stream()`, or `.stderr_stream()`, an
existing `CommandResult`, or plain `str`/`bytes`. Builder and stream sources run
when the downstream command runs. Upstream non-zero exits do not raise by
default; the downstream `CommandResult.upstream_results` tuple records upstream
`stdout`, `stderr`, and `exit_code` evidence.

## Hostrun Feature Parity

| Hostrun feature area | Pyrun status | Notes and caveats |
| --- | --- | --- |
| Persistent sessions | Implemented | `SessionStore` keeps sessions keyed by `session_id`; omitted IDs use `default`. |
| `ctx` scratch state | Implemented | Persistent dict-like object with attribute access. Python objects stay live in-process. |
| Host cwd and `cd` | Implemented | `host.cwd()` and `host.cd(path)` persist per session; relative helpers resolve against that cwd. |
| Filesystem helpers | Partial | Read/write/exists/remove/glob/open plus JSON, JSONL, CSV, TSV, TOML-read support. YAML is not supported with stdlib only. |
| `tools.file.replace` / `patch` | Implemented | Exact replacement and unified-diff hunk application are present; deletion patches are rejected. |
| Temporary files/directories | Implemented | `tmp.file()` and `tmp.dir()` create cleanup-capable handles; pending approval gates create/write/cleanup side effects. |
| Command execution / builder | Partial | `run.<program>` is the preferred immediate-execution API. `cli.<program>` returns advanced builders for cwd/env/stdin helpers, capture helpers, redirects, and JSON/text/line helpers. Hostrun stream-selector syntax is not mirrored. |
| Spawn and pipeline helpers | Partial | `.spawn()` returns process handles; pipeline helpers are capture-then-feed composition, not OS pipe FD streaming. |
| HTTP and sessions | Partial | Stdlib `urllib` request builders, response helpers, `to_file`, base URL, and shared headers exist. No retry, cookie jar, TLS option, or streaming support yet. |
| `rg`, `fd`, and `sqlite` wrappers | Partial | Pure-Python subsets cover common search/discovery/query flows; they are not full CLI-compatible facades. |
| `kubectl` wrapper | Implemented | Builds `kubectl get` argv with namespace, selector, all-namespaces, name, and output options. |
| Tool wrappers | Partial | Thin builders exist for sudo/authsudo, PowerShell, SSH, browser-cli, git, GitHub, and tmux. They do not install/probe tools and most return builders until `.run()`/`.text()`/`.json()`. |
| Structured helpers | Partial | `text`, `seq`, `obj`, and `hr(...)` cover common shaping operations. Python cannot patch builtins like Hostrun patches JS prototypes. |
| JSONL adapter | Implemented | One JSON request per line, persistent sessions, validation errors, JSON-compatible helper serialization, default auto-approve. |
| MCP stdio server | Implemented | Minimal `initialize`, `notifications/initialized`, `tools/list`, and `tools/call` with `pyrun_eval`. Default auto-approve. |
| Approval mode | Partial | Library callers can choose pending approval; helper-mediated side effects return `needs_approval`. There is no approval resume protocol in JSONL/MCP yet. |
| Sandboxing | Not implemented | Approval gating is not sandboxing. Arbitrary Python executes in-process unless an external sandbox/restriction is supplied. |
| Docs and caveats | Partial | This prototype documents implemented slices and known gaps, but Hostrun remains the fuller reference surface. |

## Sandbox and Approval Caveats

Pyrun separates approval gating from sandboxing:

- **Approval gating** is a helper-level policy. In pending mode, Pyrun helpers
  such as `fs.write`, command `.run()`/`.spawn()`, HTTP execution, downloads,
  temporary-file operations, and `tools.file.replace`/`patch` writes return a
  `needs_approval` result instead of performing the side effect.
- **Sandboxing** would be an OS/runtime boundary that prevents arbitrary user
  code from reaching the host directly. Pyrun does not provide that boundary.

Current stance:

- The Python runtime evaluates arbitrary Python in-process in auto-approve mode.
- Pending approval prevents helper-mediated side effects, but it is not a
  security boundary. If arbitrary code can import stdlib modules or access
  objects that expose `os`, `subprocess`, file descriptors, or network APIs, it
  can bypass helper approval unless the caller also restricts or sandboxes the
  execution environment.
- The JSONL adapter and MCP stdio server default to auto-approve to match the
  spawned `hostrun-mcp` style where the outer harness owns tool-call approval.
  Library callers can still use `SessionStore.pending_approval()` when they need
  helper-level intent collection.
- Pyrun cannot safely sandbox arbitrary Python with only the standard library.

Future sandbox options, none implemented yet:

- run evaluations in a subprocess and destroy/recreate it after limits or denial;
- restrict filesystem and process visibility with namespaces/seccomp/cgroups;
- launch the subprocess under an external sandbox such as `bwrap`;
- expose only explicit host capabilities across the process boundary and keep
  arbitrary code away from host filesystem, process, and network authority.

## Differences from Hostrun

- Runtime language is Python instead of QuickJS JavaScript.
- Approval gating exists for runtime helper side effects, but it is not a
  sandbox. Python code itself still executes in-process and is not isolated;
  malicious code can use Python stdlib APIs directly unless the caller provides
  an external sandbox.
- Helpers are simple Python objects and namespaces, not JS proxies or patched
  builtins. Use `text.lines(value)` or `hr(value).lines()` instead of JS-style
  prototype methods on every string/list/object.
- Statement evaluation returns the final trailing expression when present.
- Print output is captured as `console` lines in completed results.
- HTTP uses stdlib `urllib.request`; no retry, cookie jar, or streaming support
  exists yet.
- Filesystem helpers cover the first structured-data slice only. YAML is not
  supported without a future non-stdlib decision.
- `fd`, `rg`, and `sqlite` are pure-Python parity wrappers, not subprocess
  facades. They intentionally cover a small Hostrun-like subset and do not
  implement full `fd`, ripgrep, or sqlite CLI behavior.
- Tool command wrappers are thin argv builders around local CLIs. They do not
  install or probe external tools, and most wrappers do not execute until callers
  explicitly call `.run()`, `.text()`, `.json()`, etc. `tools.git.status()` and
  `tools.git.commit()` are the exceptions because they are execution helpers.
- Command pipeline helpers are capture-then-feed composition, not OS pipe file
  descriptor streaming. The full upstream output is captured before the
  downstream command starts.

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

fd.find('*.py', {'root': 'src', 'glob': True, 'extension': 'py'})
fd.files('src')
fd.dirs('.', {'hidden': True})

rg.search('TODO', ['src'], {'ignore_case': True}).lines()
rg.files('TODO', ['src'])
rg.matches('TODO', ['src'], {'fixed': True})

sqlite.query('scratch.db', 'CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)')
sqlite.query('scratch.db', "INSERT INTO items (name) VALUES ('apple')")
sqlite.query('scratch.db', 'SELECT id, name FROM items')

kubectl.get('pods', {'namespace': 'prod'})
tools.browser.open('https://example.test')
tools.sudo(cli.systemctl('restart', 'example.service'))
tools.powershell('Write-Output hello')
remote = tools.ssh({'host': 'server.test', 'user': 'alice', 'port': 2222})
remote.run(cli.echo('hello'))
tools.github.pr_view(12, {'json': ['number', 'title']})
tools.tmux.open('scratch')

text.lines('a\nb\nc', 2, 3)
text.range('a\nb\nc', 2)
text.json_lines('{"a":1}\n{"a":2}\n')
seq.where([{'kind': 'fruit'}, {'kind': 'veg'}], {'kind': 'fruit'})
seq.select([{'name': 'apple', 'count': 2}], 'name')
obj.rename({'name': 'apple'}, {'name': 'label'})
hr([{'count': 2}, {'count': 3}]).where(lambda row: row['count'] > 2).select('count')
```

## Tests

```sh
python -m unittest
```

The test suite uses only the standard library.
