# halia — end-to-end test log

Manual, full-stack scenarios run against a live model (`deepseek-v4-pro`), verifying
behaviour that unit tests can't (real tool-calling, the conscience on real output,
pause/resume across processes, chat persistence). Unit tests live in `tests/`; this
file records the **behavioural** runs and their outcomes.

- **Provider used:** DeepSeek (`deepseek-v4-pro`) via the OpenAI-compat client.
- **Reproduce:** `halia setup` (provider + key), then run the commands below.
  Non-finance scenarios need no fixtures; the finance scenario uses
  `uv run python tests/e2e/make_finance_fixtures.py`.
- **Legend:** ✅ pass · ⚠️ pass with a known limitation · 🐛 surfaced a bug (fixed).

---

## 1. Number-grounding conscience (rounding-aware)

**Goal:** every figure in an answer must trace to a tool result; correct roundings pass.

```bash
uv run halia run 'Read /tmp/ledger.csv and tell me the average transaction amount, rounded to 2 decimals.'
```

- Model computed `mean = 181.375` via `aggregate_csv`, reported `181.38`.
- **Result:** ✅ no `⚠` — `181.38` recognised as a correct 2-dp rounding of `181.375`.
  A mis-rounding (`181.40`) or invented figure would still be flagged.

## 2. Conscience Stage 2 — self-heal

**Goal:** when an answer contains an ungrounded figure, bounce it back to recompute via tools.

```bash
uv run halia run 'Reconcile /tmp/ledger.csv against /tmp/bank.csv on id/amount, then give me a short summary: total in each file, the net difference, and which transactions differ.'
```

- First draft contained a figure no tool produced → conscience bounced it back once.
- **Result:** ✅ printed `✓ regrounded (conscience bounced back 1× to recompute figures via tools)`;
  final answer fully grounded (`725.50 − 550.50 = 175.00`, all tie out).

## 3. Curated persona presets

**Goal:** `halia finance` lands in the finance posture (skills + persona) out of the box.

```bash
uv run halia finance 'Reconcile /tmp/ledger.csv against /tmp/bank.csv on id/amount and give me a one-paragraph summary with the total of each file and the net difference.'
uv run halia profile list
```

- **Result:** ✅ engaged `reconcile_csv`/`aggregate_csv`/`calculate`, every figure source-tagged.
  `profile list` shows built-in presets + user profiles; user profile of same name overrides.

## 4. Planning (`--plan`)

**Goal:** draft a short plan before executing; plan guides but the loop still adapts.

```bash
uv run halia finance --plan 'Reconcile /tmp/ledger.csv against /tmp/bank.csv on id/amount, then give me the total of each file and the net difference.'
```

- **Result:** ✅ printed a 4-step plan, then executed it (`aggregate_csv ×2`, `reconcile_csv`,
  `calculate`); plan recorded on the run. Composes with persona + conscience.

## 5. Audit trail — receipts, `show`, review filter

**Goal:** the plan + conscience outcome are persisted and inspectable.

```bash
uv run halia runs                    # tags: planned · regrounded×N · ⚠N unverified
uv run halia show <run-id>           # full receipts: prompt, plan, every step, answer
uv run halia runs --unverified       # trust-review set (runs that shipped an unverified figure)
```

- **Result:** ✅ `runs` shows trust tags; `show` expands one run's full provenance
  (by id or unique prefix; ambiguous prefixes refuse to guess); `--unverified` filters
  to the review set (across all real runs so far: "no runs with unverified figures").

## 6. Checkpointer / HITL — pause + resume

**Goal:** an unattended run freezes at a dangerous tool; resume applies the decision.

```bash
uv run halia run --pause-for-approval -q 'Compute 100 + 250.50 with the calculate tool, then write the result to /tmp/halia_paused.txt'
uv run halia checkpoints                     # the approval queue
uv run halia resume <checkpoint-id> --approve
cat /tmp/halia_paused.txt                     # -> 350.5
```

- **Result:** ✅ froze at `write_file` (nothing written); `resume --approve` in a **separate
  process** rehydrated the loop, wrote the file, continued, and the conscience regrounded.
  State genuinely survived the process exit. `--deny` path skips the action (unit-tested).

## 7. Chat Tier 1 — multi-turn context

**Goal:** context persists across turns within a session.

```bash
printf 'What is 15 * 12? Use the calculate tool.\nNow multiply that number by 2.\n/exit\n' | uv run halia chat
```

- **Result:** ✅ second turn resolved "that number" → `180 × 2 = 360`, both grounded via
  `calculate`. `/clear` resets context; `/resume <id>` bridges to paused checkpoints.

## 8. Chat Tier 2 — session persistence across restarts

**Goal:** a conversation survives closing and reopening chat.

```bash
# session 1 (note the session id printed):
printf 'My favorite number is 17. Remember it.\n/exit\n' | uv run halia chat
uv run halia sessions
# session 2 — a FRESH process:
uv run halia chat --resume <id>     # then: "What is my favorite number?"
```

- **Result:** ✅ a fresh process resumed the session and recalled `17`; `sessions` lists
  id, age, turn count, title. Staleness note shown on resume.
- 🐛 **Fixed:** halia used to disclaim "each session starts fresh / no access to earlier
  chats" even mid-resume. System-prompt now tells it the history is its memory.

## 9. Packaging — bare `halia` command

**Goal:** run `halia` without the `uv run` prefix.

```bash
./install.sh
halia --version
halia sessions
```

- **Result:** ✅ installs to `~/.local/bin/halia` (uv tool), wired to the same `~/.halia` DB.
  `--version` flag added (kept the `version` subcommand). Editable install tracks source.

## 10. Chat history window (token-budget cap)

**Goal:** long chats don't grow the context sent to the model without bound.

- **Result:** ✅ the loop sends only the system message + most-recent whole turns within a
  char budget (default 40 000); the full transcript is still persisted. Trims only at
  user-turn boundaries so tool-call/observation pairs are never orphaned. Verified by unit
  tests (`tests/test_window.py`); normal chat unaffected (context still flows).

---

## 11. Finance MVP — PDF bank statement vs CSV ledger (the vertical proof)

**Goal:** the flagship slice — reconcile a realistic **PDF** statement against a **CSV**
ledger, catch every discrepancy, produce a cited report, every number tool-grounded.

```bash
uv run python tests/e2e/make_finance_fixtures.py    # -> /tmp/acme_ledger.csv + /tmp/acme_statement.pdf
yes y | halia finance --max-iters 14 'Reconcile our ledger at /tmp/acme_ledger.csv against the bank statement PDF at /tmp/acme_statement.pdf. First extract the statement into /tmp/acme_statement.csv, then reconcile by check/deposit. Identify every discrepancy. Ground every number in a tool.'
```

**Planted discrepancies:** (1) Check 1042 ledger 1,200 vs bank 1,250 ($50 mismatch);
(2) $35 service charge on the statement only; (3) $800 deposit (DEP-103) in the ledger
but not yet cleared. Keys intentionally messy (`CHK-1042` vs `Check 1042`,
`DEP-101` vs `Deposit - Globex`).

**What halia did (25 steps, all tool-grounded):**
1. `read_csv` ledger + `read_pdf` statement (clean pypdf extraction).
2. **Solved entity resolution itself** — wrote *normalized* CSVs assigning the ledger's
   keys to the statement rows (`Deposit - Globex → DEP-101`, `Service Charge → SVC-JUL`).
3. `reconcile_csv` on the normalized files → `matched: 5, only-ledger: DEP-103,
   only-statement: SVC-JUL, mismatch CHK-1042: -1200 vs -1250`.
4. `aggregate_csv` + `calculate` for the balances; produced a proper bank rec where **both
   sides tie to $13,258.75**.

**Result:** ⚠️ **Substantively correct** — caught all three discrepancies with correct
accounting interpretation (check understatement, deposit in transit, unrecorded fee),
deterministically via tools. A real, sellable finance deliverable.

**Bug surfaced → fixed:** the conscience flagged `1250.00`, `35.00` as "unverified" —
🐛 **sign-sensitivity**: tools express debits as negatives (`-1,250.00`) but prose states
magnitudes (`$1,250.00`). Grounding now matches on **magnitude** (see `conscience/verify.py
_is_grounded`, `tests/test_verify.py::test_negative_tool_figure_grounds_positive_magnitude`).
After the fix, the same figures ground; only genuine head-math (an intermediate subtraction
the model didn't route through `calculate`) is flagged — the conscience working as intended.

**Open follow-ups (not blocking):**
- `reconcile_csv` matching leans on the model to normalize keys; a deterministic
  fuzzy / amount+date match would harden it against weaker models.

---

## 12. Multi-approval UX — trust a directory for the session

**Goal:** a multi-step task shouldn't prompt on *every* write to the same folder.

```bash
printf 'a\n' | halia run "Write 'alpha' to /tmp/trust_a.txt and 'beta' to /tmp/trust_b.txt using write_file."
```

- **Result:** ✅ first write prompts `Allow? yes / all writes to this folder / No`; choosing
  "all" trusts the directory for the session, so the second write runs without a re-prompt.
  Session-scoped (never persisted); the permission floor still denies sensitive paths
  regardless. Logic unit-tested in `tests/test_approver.py`.

## 13. Second vertical — `research` (breadth: halia is not finance-only)

**Goal:** a second built-in vertical on the same horizontal engine, to prove the platform
is general. Research's trust angle is *cite your sources, don't fabricate*.

```bash
halia profile list      # shows finance + research
halia research --max-iters 6 -q 'Fetch https://example.com and tell me, in one sentence, what the site says this domain is for. Cite your source.'
```

- **Result:** ✅ `research` ships alongside `finance`; `halia research …` fetched the page
  (`fetch_url`), summarised, and cited the source URL. A completely different vertical
  (web/document gathering vs. reconciliation), same engine + conscience + audit.
- **Known limitation:** research has `fetch_url` (retrieve a *given* URL) but **no web
  search** — the model must already know the URL. A `web_search` skill is the natural next
  addition for this vertical. And like every preset, its real *verification pack*
  (claim→source grounding, analogous to number-grounding) is future depth, not yet built.
