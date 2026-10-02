```
╭─ v0.31.18 ───────────────────────────────────────────────────────────────────────────────╮
│                                ██   ██  █████  ██      ██  █████                         │
│                                ██   ██ ██   ██ ██      ██ ██   ██                        │
│                                ███████ ███████ ██      ██ ███████                        │
│                                ██   ██ ██   ██ ██      ██ ██   ██                        │
│                                ██   ██ ██   ██ ███████ ██ ██   ██                        │
│                                                                                          │
│                                 a general, highly capable agent                          │
│                 Enter to send · Option+Enter for a newline · /help for commands          │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```


A **trust-first** general agent — output you can *verify*, actions you can *gate*.
Self-hosted, provider-based (no bundled models). Your model, your API key, your machine.

## 🚀 Install

```bash
curl -LsSf https://raw.githubusercontent.com/warifmust/halia/main/install.sh | bash
halia setup             # pick a provider + paste your API key
halia --help
```

Then just talk to it: `halia chat` (a conversation) or `halia run "<task>"` (one-shot).

## 🤖 What is halia

halia is a general-purpose agent that runs on your machine and puts **trust first**:
every action is *gated*, every answer is *traceable*. It routes your request to the
right tool itself — files, data, web, or the desktop — and stays within the bounds
you set. Loop guards, time budgets, and a per-turn audit trail keep it honest.

## ✨ Capabilities

- 📁 **Files & system** — read, write, search, and shell commands you opt into, plus a
  read-only `disk_usage` for "how much space is left?" questions.
- 📊 **Data** — spreadsheets, CSVs, PDFs, DOCX/PPTX, charts, diagrams, and SQL, with
  one-shot analysis and export.
- 🌐 **Web** — fetch pages, run searches, and call OpenAPI endpoints as tools.
- 🖥️ **Computer use** — drive the desktop (click, type, screenshot, draw) when you want
  it, gated behind approval.
- 🧠 **Memory & procedures** — teach it facts and repeatable multi-step workflows.
- 🔌 **MCP** — bring tools from external MCP servers (stdio or HTTP) into the agent,
  each surfaced as `mcp__<server>__<tool>`.
- 🧾 **Audit** — full provenance of every run, stored locally in SQLite.

## 🔌 MCP

halia can use tools exposed by [Model Context Protocol](https://modelcontextprotocol.io)
servers. Configure them by hand in `~/.halia/mcp.json` (VS Code / Claude shape):

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "ghp_…"}
    },
    "jira": {
      "url": "https://mcp.example.com/jira",
      "headers": {"Authorization": "Bearer …"}
    }
  }
}
```

```bash
uv tool install --force "git+https://github.com/warifmust/halia.git@main" --with mcp   # add MCP support to an installed halia
# from a source checkout: uv sync --extra mcp
halia mcp edit                # open ~/.halia/mcp.json in $EDITOR
halia mcp list                # connection status + tool count per server
halia mcp login <name>        # authenticate a server (OAuth) explicitly
halia mcp remove github       # delete a server by name
```

By default MCP servers **lazy-load**: none connect at startup; the agent calls the
`mcp_connect` tool when it needs one (running OAuth login then). Set `"mode": "eager"`
at the top level of `mcp.json` to load every server at startup instead.

Connecting is best-effort: a broken server or an expired token is reported in the
session banner and `halia mcp list`. It never blocks a run.

## 🛠️ Development

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

## 📂 Layout

```
halia/
  cli/          # typer entrypoint: setup, config, chat/run, slash commands
  core/         # the agent loop, loop guards, checkpoint/resume, planner
  computer/     # CUA backend (cua-driver) — the only computer backend
  skills/       # horizontal skill library (fs, data, web, cua, …)
  mcp/          # MCP server registry + async bridge (mcp__<server>__<tool>)
  eval/         # deterministic computer-use eval harness
  providers/    # LLM providers (OpenAI-compat, Anthropic)
  permissions/  # allow/restrict, egress floor, dangerous-action gate
  audit/        # provenance / audit trail
  memory/       # user-controlled facts + failures
  config/       # config store + setup wizard
  store/        # SQLite persistence
```
