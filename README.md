```
╭─ v0.31.20 ───────────────────────────────────────────────────────────────────────────────╮
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

## 🖥️ Computer use (CUA)

CUA needs an optional extra. `halia setup --cua` installs it, and it can also be
added to an existing tool install — always keep the `cua-driver` pin, because
`uv tool install --force` rebuilds the environment from the requirement list
alone and would otherwise pull a driver halia cannot talk to:

```bash
uv tool install --force "git+https://github.com/warifmust/halia.git@main" \
  --with mcp --with 'cua-driver==0.29.1'
# from a source checkout: uv sync --extra cua
```

You should rarely need that line: `halia` installs its own extras (see
[Keeping the environment intact](#keeping-the-environment-intact)) and `halia upgrade`
re-applies them, so the manual form is only for setting an install up by hand.

**Why the pin.** `cua-driver` 0.34.0 made `cursor_motion` a required field on its
session-start input. halia does not pass it, so *every* `cua_*` tool fails at
session start with a `StartSessionInput` `TypeError`. The installation is
therefore pinned to the one driver build halia is validated against, in two
places that must agree: `CUA_DRIVER_SPEC` in
[`halia/computer/cua_backend.py`](./halia/computer/cua_backend.py) (read by
`halia setup --cua` and by `halia upgrade`) and the `cua` extra in
[`pyproject.toml`](./pyproject.toml).

A separate `CUA_DRIVER_RANGE` (`>=0.29,<0.34`) is the *guard band*, not the
install pin. It is what a running session tolerates, so a driver you deliberately
install inside the band still works, and it also lets `halia upgrade` keep
re-applying the pin to an install that predates it. Anything outside the band is
refused at session start.

**Staying locked.** Three layers keep CUA from drifting:

- `halia upgrade` re-applies the pin, from halia's own record of what the
  environment needs (see below).
- A session refuses to start on a driver outside the guard band, naming the exact
  repair command instead of surfacing the opaque `cursor_motion` `TypeError`.
- `halia doctor` reports the installed driver version and flags one that is
  outside the band, or inside it but not the pinned build.

To adopt a new driver, validate halia against it (the `StartSessionInput` contract
is the usual breaker), then move `CUA_DRIVER_SPEC` and the `cua` extra together,
and widen `CUA_DRIVER_RANGE` to include it.

Note that `cua-driver update --apply` does **not** affect halia: it installs the
standalone driver app, while halia drives the binary bundled inside the
`cua-driver` wheel. Only changing the installed Python package can break CUA.

### Keeping the environment intact

`uv tool install --force` rebuilds a tool venv from the requirement list it is
handed *and* rewrites the tool receipt to match. So the receipt cannot be the
record of what an environment needs: a single bare `uv tool install --force <halia>`
erases the only trace of `mcp` and `cua-driver`, and nothing afterwards can tell
they were ever wanted — which is how "`mcp` package not installed" and the CUA
`cursor_motion` failure kept coming back.

halia therefore records the specs its environment needs in `tool_extras` inside
`~/.halia/config.json`, which uv never touches, and works from that record:

- `halia setup --cua` installs the pinned driver and records it; the setup wizard's
  MCP step installs the `mcp` package and records it (it previously wrote server
  definitions without ever installing the package).
- `halia mcp …` installs `mcp` on demand rather than telling you to run a uv
  command.
- `halia upgrade` re-applies every recorded spec, re-deriving the driver pin from
  `CUA_DRIVER_SPEC` so an unsupported driver can never be carried forward.
- When halia is already on the latest version, `halia upgrade` restores any
  recorded extra that has gone missing instead of just reporting "up to date": an
  up-to-date version is not a healthy environment, because extras can be stripped
  without the version changing. Nothing is reinstalled when nothing is missing, and
  `--check` stays read-only.
- `halia doctor` reports a recorded extra that has gone missing.

So the only command you need after a mishap is `halia upgrade`. Reinstalling by
hand with `uv tool install --force` no longer has to be remembered, or repeated,
to keep `mcp` and CUA working.

Installs of extras use `uv pip install --python <halia's interpreter>`, not
`uv tool install`, so fetching an extra on demand cannot rewrite the tool receipt
halia itself was installed with.

To turn an extra off deliberately, drop its spec from `tool_extras` — otherwise
`halia upgrade` and `halia doctor` will keep treating it as wanted.

**Reaching windows on another display.** `cua_screenshot` captures the primary
display only — a window on a second monitor, minimized, or hidden is not in
frame. `cua_desktop` lists *every* top-level window with its `pid` and
`window_id`, and the window-scoped tools work by window id, not by display:

- `cua_window(pid, window_id)` — that window's controls, each with an
  `element_token`
- `cua_window(pid, window_id, screenshot=true)` — see the window itself
- `cua_click` / `cua_type` / `cua_scroll` / `cua_drag` — pass `pid` + `window_id`
  (with an `element_token`, or `x`/`y` in that window's own screenshot space)
  instead of desktop coordinates

Coordinates read from a window's own screenshot are used as-is; only the
desktop-scoped path is scaled back to screen pixels.

**Close-ups for evidence.** Pass `x`, `y`, `width` and `height` to
`cua_screenshot` (in the grid's pixel space) to crop to one region — a success
toast, a status message, a form field — instead of capturing the whole display.
The crop is saved to its own `…-crop.png` file (the full capture is kept
alongside), and it's for inspection/verification — its coordinates are not click
coordinates, so re-take a full `cua_screenshot` to interact. `grid:false` gives a
clean capture.

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
