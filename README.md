# halia

A **trust-first** general agent — output you can *verify*, actions you can *gate*.
Self-hosted, provider-based (no bundled models). Early scaffold.

## Development

```bash
uv sync                 # create the venv + install deps (uv manages Python 3.12)
uv run halia --help
uv run pytest
uv run ruff check .
uv run mypy halia
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
