#!/usr/bin/env bash
# Shared core of the ChunkHound Index Compactor composite action. Provisions an
# isolated venv with the runner's python3, installs the compactor, and invokes
# the CLI. Inputs arrive as CIC_* environment variables (mapped from action
# inputs in action.yml).
set -euo pipefail

: "${CIC_TOOL_SRC:?CIC_TOOL_SRC is required (path or wheel to install the compactor from)}"
: "${CIC_INDEX_PATH:?CIC_INDEX_PATH is required (ChunkHound index directory or DuckDB file)}"

args=(compact "$CIC_INDEX_PATH")
if [ -n "${CIC_TARGET:-}" ]; then args+=("$CIC_TARGET"); fi
if [ "${CIC_REPLACE:-false}" = "true" ]; then args+=(--replace); fi
if [ "${CIC_SKIP_HNSW:-false}" = "true" ]; then args+=(--skip-hnsw); fi

venv="$(mktemp -d)/venv"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check "$CIC_TOOL_SRC"
exec "$venv/bin/chunkhound-index-compactor" "${args[@]}"
