#!/usr/bin/env sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -X utf8 "$repo_dir/install.py" "$@"
