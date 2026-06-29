#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
uv tool install --force --editable .
