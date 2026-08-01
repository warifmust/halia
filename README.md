# halia

A **trust-first** general agent — output you can *verify*, actions you can *gate*.
Self-hosted, provider-based (no bundled models). Early scaffold.

## Install

```bash
./install.sh            # bootstraps uv if needed, installs the `halia` command
halia setup             # pick a provider + paste your API key
halia --help
```

Then just talk to it: `halia chat` (a conversation), or `halia run "<task>"` (one-shot).

## Development

```bash
uv sync                 # create the venv + install deps (uv manages Python 3.12)
uv run halia --help     # run from source, no install
uv run pytest
uv run ruff check .
uv run mypy halia
```

For a `halia` on your PATH that tracks source edits live, install it editable:

```bash
uv tool install --editable .
```

## Layout

```
halia/
  cli/          # typer entrypoint: setup, help, run/chat
  core/         # the agent loop + orchestrator
  executor/     # command/PTY exec, file edit-patch, sandbox
  providers/    # LLM (OpenAI-compat) + OCR/STT abstractions
  skills/       # horizontal skill library
  conscience/   # verification, citations, validation, audit hooks
  permissions/  # allow/restrict, dangerous-action gate
  audit/        # provenance / audit trail
  memory/       # working + domain knowledge
  config/       # config store + setup wizard
```
