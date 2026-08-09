# Web page text reader

Pyrun exposes `tools.read_web(url, options=None)` for reading static HTTP/HTTPS pages through the external `w3m` text browser. It returns bounded plain text and numbered link references without requiring callers to construct HTTP headers. See [`docs/wiki/systems/read-web.md`](../wiki/systems/read-web.md) for implementation notes.

## What it must do

### API and output

- [x] Accept an absolute `http://` or `https://` URL.
- [x] Accept an optional options dictionary.
- [x] Use `width=120` when `width` is omitted.
- [x] Use `timeout=30` seconds when `timeout` is omitted.
- [x] Use `max_chars=100000` when `max_chars` is omitted.
- [x] Return an attribute-accessible object with exactly `url`, `text`, and `truncated` fields.
- [x] Preserve `w3m` numbered link references in rendered text, including reference lines mapping numbers to URLs.
- [x] Truncate returned text to `max_chars` and set `truncated` to `True` only when truncation occurred.
- [x] Read plain-text responses as text without requiring HTML markup.

### Approval and execution

- [x] Require helper approval under the `tools.read_web` approval name before invoking `w3m`.
- [x] In pending-approval mode, return `needs_approval` with the URL and normalized options without fetching the page.
- [x] Require the external `w3m` executable and report a clear error when it is unavailable.
- [x] Enforce the configured timeout and report timeout failures with the URL and duration.
- [x] Report non-zero `w3m` exits as read failures with available diagnostic output.

### Validation

- [x] Reject non-string, relative, empty, `file:`, and other non-HTTP(S) URLs before execution.
- [x] Reject non-dictionary options.
- [x] Reject unsupported options; only `width`, `timeout`, and `max_chars` are accepted.
- [x] Require positive integers for `width` and `max_chars`.
- [x] Require a positive finite number for `timeout`.

## How it works

- [`docs/wiki/systems/read-web.md`](../wiki/systems/read-web.md)

## Implementation inventory

- `pyrun/runtime.py` — validates inputs, requests approval, builds the `w3m` command, executes it, and returns the attribute-accessible page result.

## Tests asserting this spec

- `tests/test_runtime.py` — approval, defaults, validation, dependency, timeout, navigation failure, plain text, HTML links, width, truncation, and attribute access.

## Known gaps (current cycle)

None.

## Out of scope

- JavaScript-rendered pages or pages requiring Chromium.
- Authenticated or cookie-backed browsing.
- Automatic fallback to `browser-cli`, Chromium, another browser, or direct HTTP parsing.
- Caller-provided request headers or other per-request browser options.
- Protocols other than static HTTP and HTTPS.
- A final URL, page title, response headers, or raw HTTP response in the result; the contract is limited to `url`, `text`, and `truncated`.
