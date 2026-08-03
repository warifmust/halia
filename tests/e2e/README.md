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
- Its real *verification pack* (claim→source grounding, analogous to number-grounding) is
  future depth, not yet built.

## 14. `web_search` — discovery for the research vertical

**Goal:** research shouldn't need the URL up front — it should *find* sources.

```bash
halia research --max-iters 8 'Search the web for why floating point is bad for money, then read one good source and explain in 2 sentences with a citation.'
```

- **Result:** ✅ full loop — `web_search` (keyless DuckDuckGo, no new dependency) discovered
  sources → `fetch_url` read one → answer with a real citation (Modern Treasury article).
- **Cross-vertical bonus:** the conscience flagged `1.1499999` as unverified — the model did
  that subtraction in its head and labelled it "verified." Horizontal number-grounding
  caught head-math in the *research* vertical, exactly as in finance. Skill unit-tested
  offline in `tests/test_web_search.py` (parsing + a mocked HTTP client).

## 15. Egress floor — SSRF protection for web skills

**Goal:** the web access we just added must not be steerable into *internal* addresses.

```bash
uv run python -c "from halia.skills.web import FetchUrl; print(FetchUrl().run({'url':'http://169.254.169.254/latest/meta-data/'}))"
```

- **Result:** ✅ `blocked: … internal address 169.254.169.254 (SSRF floor)`. `fetch_url` now
  resolves the host and refuses loopback / link-local (incl. the cloud **metadata endpoint**)
  / private / reserved IPs and non-http(s) schemes — closing an SSRF hole a prompt-injected
  page could otherwise exploit. Public URLs (example.com) still work. Non-removable floor,
  mirrors the filesystem `permissions/guard.py`. Unit-tested in `tests/test_egress.py`.
- **Coarse per-profile egress already exists** via skill selection (the `finance` preset has
  no `fetch_url`/`web_search` → no network at all). The finer **per-profile domain
  allow/deny** list is the deferred next layer (needs run-scoped policy injected into skills).

## 16. Third vertical — `education` (clerical relief, with a thin real pack)

**Goal:** relieve educators' clerical burden (pupil-data → tables/charts/reports), with
deterministic trust hooks even in a "soft" domain.

```bash
printf 'a\n' | halia education 'Read /tmp/pupils.csv, compute each student average across the three terms (use calculate), then make a bar chart of the averages saved to /tmp/averages.svg. One-line summary of top and bottom performer.'
```

- **Result:** ✅ `read_csv` → per-student averages via `calculate` (grounded) → `make_chart`
  wrote a valid 5-bar SVG (scaled correctly) → clean class table + summary. Real clerical
  output, every figure tool-computed.
- **Thin real verification pack** (not just a persona): `readability` (Flesch-Kincaid grade,
  dependency-free) so reading-level claims are measured + number-grounded; worksheet math
  routed through `calculate` for an exact answer key.
- **`make_chart`** = the "build graph" capability — dependency-free SVG (no matplotlib),
  gated + floor-checked like any file write. Horizontal (finance/research can chart too).
- **Fix surfaced:** the trust-a-directory approval scope now covers *any* file-writing tool
  (make_chart, not just write_file).

## 17. `make_pdf` — printable deliverables (markdown master → PDF render)

**Goal:** turn grounded content into a clean, printable PDF; keep the content editable.

```bash
printf 'a\n' | halia education 'Read /tmp/pupils.csv, compute each student average (use calculate), then produce a one-page class performance report as a PDF at /tmp/class_report.pdf: intro paragraph, a markdown table of averages, and two recommendations.'
```

- **Result:** ✅ wrote `class_report.pdf` (valid PDF, clean render — heading, intro, aligned
  table, bullets) **and kept `class_report.md`** (the editable master). Round-trips back
  through our own `read_pdf`. Every figure was tool-computed upstream.
- **Model:** markdown is the master; PDF is a *render* of it (`fpdf2`, lean — no LibreOffice,
  no browser). Edit the `.md` and re-render; the PDF is a disposable print product.
- Unit-tested in `tests/test_export.py` (incl. a markdown→PDF→text round-trip, unicode
  safety, and the permission floor). `make_pdf` added to finance/research/education presets.
- Follow-ups (deferred): embedding charts in the PDF, full-Unicode PDF output (bundle a TTF
  — currently latin-1 with typographic sanitisation).

## 18. `make_pptx` — slide decks (same markdown-master model)

**Goal:** turn content into a PowerPoint deck of clean content slides; human owns design.

```bash
printf 'a\n' | halia education 'Create a 3-slide teaching deck on the water cycle as a pptx at /tmp/water_cycle.pptx. Title + intro; the three stages as bullets; a small markdown table of stage vs description. Grade-6 friendly. Use --- between slides.'
```

- **Result:** ✅ wrote a valid 3-slide `.pptx` (titles: *The Water Cycle / Three Main Stages
  / Stage Snapshots*), bullets + a real table, **plus the `.md` master**. `---` separates
  slides (else split on `#`/`##`). The run also **measured each slide's reading level** with
  the `readability` tool (6.1 / 9.9 / 8.2) and explained the science-vocab bump — the
  verification pack in action.
- **Full Unicode** here (python-pptx is XML/UTF-8), unlike the latin-1 PDF path — Malay/CJK
  render fine. Scope = content + arrangement; the educator styles the design in PowerPoint.
- Unit-tested in `tests/test_pptx.py` (reopen-and-verify slides/titles/table, unicode, floor).

## 19. Embedded charts in PDF + PPTX (native, no rasterizer)

**Goal:** put a chart *inside* the deliverable, not as a separate file — without adding a
heavy SVG→PNG rasterizer.

Approach: **chart data is the master**, rendered natively per format. A fenced block in the
markdown master:

    ```chart
    title: Averages
    Aisha: 78.3
    Chong: 90
    ```

- **PDF:** drawn with fpdf2 primitives (rects/lines/text) — **vector**, crisp, no image.
- **PPTX:** a **native, editable PowerPoint chart** (`add_chart`) — the teacher can restyle it.
- No new dependency; no rasterizer (cairo/PNG) needed. Parser `chart.parse_chart_block`
  shared by both renderers (`$`/comma tolerant).

```bash
printf 'a\n' | halia education 'Read /tmp/pupils.csv, compute each average (calculate), produce /tmp/report_v2.pdf with intro, a table, and an EMBEDDED bar chart (```chart block).'
```

- **Result:** ✅ the report PDF contains the bar chart inline (the `.md` master shows the
  `chart` block); the same block yields a native chart in a deck. Unit-tested in
  `tests/test_chart.py` (parse), `tests/test_export.py` (PDF embed), `tests/test_pptx.py`
  (native chart shape).
- **Output story complete:** grounded content → tables, **embedded charts**, PDF + PPTX +
  txt/md, all from one editable markdown master.

## 20. `make_docx` + `make_excel` — Word and Excel deliverables

**make_docx** (Word, markdown-master render, python-docx):

```bash
printf 'a\n' | halia education 'Draft a short parent letter about a conference (15 Aug, 4-7pm) as a Word doc at /tmp/letter.docx: heading, two paragraphs, a bulleted list of what to bring.'
```

- **Result:** ✅ editable `.docx` (heading styles, bold runs, bullets) + `.md` master; full
  Unicode. Unlike PDF, the Word file itself is editable. Tests: `tests/test_docx.py`.

**make_excel** (Excel, DATA-master not markdown, existing openpyxl — no new dep):

```bash
printf 'a\n' | halia finance 'Read /tmp/acme_ledger.csv and export to /tmp/ledger.xlsx, adding a TOTAL row with the sum of the amount column (via aggregate_csv).'
```

- **Result:** ✅ typed `.xlsx` — numbers stored as **numbers** (2500, 340.5 …) so they're
  summable/pivotable, bold frozen header, grounded TOTAL row (6856.25). Opens in Excel and
  Google Sheets. IDs like `CHK-1042` stay text; `$1,200.50` coerces to 1200.5.
- Different model: a spreadsheet's master is tabular data (`rows`/`sheets`), not markdown.
  Same trust division — halia hands over grounded data, the analyst drives in Excel. v1 =
  values, not formulas. Tests: `tests/test_make_excel.py`.
- **Office quartet complete:** PDF · PPTX · DOCX · XLSX (+ charts, + txt/md).

## 21. Full-Unicode PDF

**Goal:** non-Latin text (Malay names, accents, Cyrillic) should render in PDFs, not get
sanitised to `?`.

```bash
printf 'a\n' | halia education 'Tuliskan surat ringkas dalam Bahasa Melayu kepada ibu bapa … sebagai PDF di /tmp/surat.pdf.'
```

- **Result:** ✅ the Malay letter renders with correct characters (round-trips through
  `read_pdf`). Bundled **DejaVuSans** (regular + bold, ~1.4 MB, permissive licence) is
  registered with fpdf2; the latin-1 sanitisation is now only a fallback if the font asset
  is missing. PPTX/DOCX/XLSX already did Unicode (XML/UTF-8) — this closes the PDF gap.
- **Note:** fpdf2's `multi_cell(0, …)` crashes with a subsetted TTF, so the renderer now
  passes explicit widths (`pdf.epw`). Font bundled in the wheel via hatch `artifacts`.
- **CJK still deferred** (Chinese/Japanese/Korean need a much larger font, ~10 MB+).
  Unit-tested in `tests/test_export.py` (Unicode round-trip).

## 22. Fourth vertical — `marketing` (channel-fit as a real trust hook)

**Goal:** a commercial-content vertical — but with a *deterministic* verification hook, not
just a persona. "Good copy" is subjective; **"this headline is 47 chars, over the 40-char
limit" is hard pass/fail** and commercially real.

```bash
halia marketing 'Write 3 Meta ad headline options for an eco water bottle. Each MUST fit the 40-char limit — verify each with count_text and show the counts. Flag any over.'
```

- **Result:** ✅ each headline reported with its verified character count (30/40, 28/40,
  30/40 …) via `count_text`, over-limit ones flagged. The persona is disciplined to
  *verify* fit (not claim it), check reading level with `readability`, and ground any stat
  in a retrieved source. Reuses the whole output quartet (calendars → `make_excel`, briefs
  → PDF/DOCX/PPTX).
- `count_text` skill: char/word counts + limit check (explicit `limit` or a known
  `platform`: twitter/sms/google_ads/meta/…); counts are tool figures the conscience then
  grounds. Unit-tested in `tests/test_textmetrics.py`.
- **Verticals now: finance · research · education · marketing** — all on one engine.

## 23. Fifth vertical — `compliance` (requirement coverage, cite-or-flag)

**Goal:** check documents (contracts/policies) against requirements, cite the clause, flag
gaps — a strong-verification vertical. Closes the read/write asymmetry (`read_docx`).

```bash
printf 'a\n' | halia compliance 'Check /tmp/privacy_policy.docx against: "data retention period", "right to access", "right to erasure", "encryption at rest", "breach notification within 72 hours", "lawful basis for processing". Use check_requirements. Then write a gap-analysis to /tmp/gap_analysis.docx.'
```

- **Result:** ✅ `read_docx` extracted the Word policy; `check_requirements` verified each
  requirement against the **real document** (3 covered with cited snippets, 3 missing:
  erasure / breach-notification / lawful basis); wrote an editable gap-analysis Word report
  + markdown master. Presence is tool-verified; adequacy flagged as judgment.
- **`read_docx`** (python-docx, existing dep) — closes the write-but-can't-read gap; added
  to DEFAULT_SKILLS (horizontal). **`check_requirements`** reads the *file itself* (not
  model-relayed text) so the coverage check is against ground truth; exact-phrase match
  (deterministic, no false "yes"), model interprets phrasing/adequacy on top.
- Unit-tested in `tests/test_compliance.py`. **Verticals: finance · research · education ·
  marketing · compliance (5).**

## 24. Sixth vertical — `data` (business analyst; cross-domain, not "IT")

**Goal:** the sleeper — the finance *engine* (read → aggregate → chart → grounded numbers)
pointed at general business data. Cheapest to build (reuses ~everything), broad demand.

```bash
printf 'a\n' | halia data 'Analyze /tmp/sales_q2.csv. Total sales by region (group_by) and overall (aggregate_csv). Identify the top region. Produce /tmp/sales_report.pdf with a summary, a table, and an EMBEDDED bar chart (```chart).'
```

- **Result:** ✅ `group_by` computed sales by region in exact decimal (North $27,959 top),
  derived shares, embedded a chart in the findings PDF — and flagged **sample-size +
  correlation-vs-causation caveats** unprompted (persona's data-honesty discipline).
- **`group_by`** (new): group a CSV by a key column, aggregate a value per group
  (sum/mean/min/max/count), exact Decimal over all rows, sorted by aggregate. The analyst
  workhorse; added to DEFAULT_SKILLS + finance/data presets. Unit-tested in
  `tests/test_groupby.py`.
- Taxonomy note: `data` is a **flat, cross-domain** profile (finance/marketing/ops analysts
  do the same core work) — deliberately *not* filed under an "IT" umbrella. See req doc
  §"Vertical taxonomy & positioning". **Verticals: finance · research · education ·
  marketing · compliance · data (6).**

## 25. `query_data` — SQL over CSV/Excel files (the analyst power tool)

**Goal:** give the analyst their native language — full SQL (JOIN, WHERE, ORDER BY, GROUP
BY, subqueries) over flat files, deterministically. Subsumes join/filter/sort/pivot tools.

```bash
halia data 'I have /tmp/orders.csv and /tmp/reps.csv. Using query_data, JOIN them and give total sales by rep name and region, highest first.'
```

- **Result:** ✅ halia JOINed the two files with `query_data`, then **cross-verified** the
  totals with `group_by` + `aggregate_csv`, computed gaps via `calculate` — every number
  tool-traced, conscience regrounded once. The full analyst loop, grounded.
- **How:** each CSV/Excel file is loaded into an **in-memory SQLite** table named after its
  filename; columns are type-inferred (numeric → sorting/aggregation work); **read-only**
  (SELECT/WITH only); errors list the available tables (self-correcting). The model *writes*
  the SQL (knowledge), halia *runs* it on the real data (tool) — the JOIN/SORT question
  answered. Added to DEFAULT_SKILLS + finance/data presets. Tests: `tests/test_query_data.py`.
- Also closes much of the *cleaning* gap — dedup/null-handling/casts/filtering are just SQL.

## 26. Line charts (trends)

**Goal:** charts were bar-only; trends need a line. Add line to make_chart + the embedded
```chart block across PDF/PPTX.

```bash
printf 'a\n' | halia data 'Read /tmp/revenue.csv. Total + growth first→last. Produce /tmp/trend_report.pdf with an EMBEDDED LINE chart (```chart with type: line).'
```

- **Result:** ✅ the report embeds a native vector **line chart** (drawn with fpdf2
  primitives; PPTX gets a native `LINE_MARKERS` chart). `make_chart` gained `kind=bar|line`;
  the chart block gained `type: bar|line`. Total/growth tool-grounded, conscience regrounded.
- `render_line_svg` + `_draw_line_chart_pdf`; `parse_chart_block` now returns
  `(kind, title, labels, values)`. Line = trends over time, bar = category comparisons.
  Unit-tested in `tests/test_chart.py`. Pie/scatter later.

## 28. Multi-series charts (grouped bar + multi-line)

**Goal:** compare several series at once — the most-used chart type analysts lacked.

The chart block gains an `x:` (categories) line + one `Series: v1, v2, …` line per series:

    ```chart
    type: line
    title: Monthly Sales by Region
    x: Jan, Feb, Mar, Apr
    North: 100, 140, 120, 175
    South: 80, 95, 110, 105
    ```

- **Result:** ✅ grouped bars / multi-line with a **legend**, rendered natively in PDF
  (vector, palette per series), PPTX (native multi-series chart), SVG, and DOCX (a
  column-per-series table). `make_chart` also takes a `series: [{name, values}]` array.
  Live: `halia data` produced a valid multi-line region comparison from the new syntax,
  separating grounded findings from inferred insight.
- Core-model change: `parse_chart_block` → `(kind, title, categories, series)`;
  `render_multi_svg` + `_draw_chart_pdf` handle N series. Single-series still works
  (`label: value`, no `x:`). Unit-tested in `tests/test_chart.py`.

## 29. Pie + scatter charts

**Goal:** shares (pie) and correlation between two numeric variables (scatter).

```chart
type: pie                     ```chart
North: 42                     type: scatter
South: 23                     xlabel: Price
                              ylabel: Units
```                           9, 420
                              12, 360
                              ```
```

- **Result:** ✅ **pie** (vector polygons + a %-legend) and **scatter** (numeric axes +
  x/y labels) drawn natively in the PDF; PPTX gets native **PIE** + **XY_SCATTER** editable
  charts; SVG via `make_chart`. Scatter uses a new data shape — `x, y` per line (or
  `points: [[x,y],…]` in make_chart).
- Chart model moved to a **`ChartSpec` dataclass** (kind, title, categories, series, points,
  x/y labels) — cleanly holds category charts *and* xy scatter. Unit-tested in
  `tests/test_chart.py` (pie/scatter parse, make_chart, PDF embed).
- **Chart set now: bar · line · multi-series · pie · scatter.** (radar/treemap/bubble
  deferred; gantt is PM, skipped.)

## 30. Area + histogram charts

**Goal:** area = cumulative trend (filled line); histogram = frequency distribution of raw
numbers (halia does the binning — a deterministic value-add).

```chart
type: area          ```chart
Jan: 120            type: histogram
Feb: 260            bins: 6
Mar: 410            values: 52, 61, 63, 68, 70, …
```                 ```
```

- **Result:** ✅ **area** (filled polygon under the line, translucent) and **histogram**
  (adjacent bars over binned data) render natively in PDF (vector), PPTX (native AREA /
  column), and SVG. `histogram_bins()` bins raw numbers into equal-width ranges + counts —
  the model passes raw `values`, halia computes the distribution. Unit-tested in
  `tests/test_chart.py`.
- **Chart set now: bar · line · multi-series · pie · scatter · area · histogram** — the
  analyst's working set is complete. (radar/treemap/bubble/donut deferred; gantt = PM.)

## 31. Seventh vertical — `qa` (manual testing; clerical relief + completeness hook)

**Goal:** the underserved IT-family wedge — relieve manual testers' clerical load (test
cases, bug reports, regression checklists), with a deterministic hook (meta-QA).

```bash
halia qa 'Feature: password reset via emailed link, expires after 30 min. Write 2 structured test cases and a bug report for an expired link still working. VERIFY each with check_qa_artifact.'
```

- **Result:** ✅ generated structured test cases + a bug report, **verified each with
  `check_qa_artifact`** (bug report needs steps-to-reproduce/expected/actual/environment;
  test case needs preconditions/steps/expected), and *explicitly separated* the
  deterministic completeness check from its own **adequacy judgment** — the presence-vs-
  adequacy discipline.
- **`check_qa_artifact`** (new hook): deterministic field-presence check per artifact type
  (variant-matching: "Repro:"/"Browser:" count). Read-only, auto-joins the default.
- **`qa` preset**: persona centered on manual-test clerical relief + traceability (reuses
  `check_requirements` for requirement→test coverage); explicitly NOT test-code authoring
  (that's AQA/coding, off-thesis). Playwright browser assist deferred. Unit-tested in
  `tests/test_qa.py`. **Verticals: finance · research · education · marketing · compliance ·
  data · qa (7).**

## 32. `http_request` — call/test an API endpoint (grounded status)

**Goal:** the missing HTTP primitive for API/endpoint testing (eng-dev + QA) — the base
that the future "teach a test procedure" system composes on. `fetch_url` only does GET
page-reads; this does any method with headers + body, and reports the exact status.

```bash
# behavioural: point halia (qa/general) at a real test endpoint and assert on the status
halia qa 'POST to https://postman-echo.com/post with JSON {"email":"a@b.com"} and confirm it returns 200.'
```

- **Live smoke (non-mocked, real httpx path):** POST JSON to `postman-echo.com/post` →
  `200 OK`, body echoed the sent `{"email":"a@b.com"}`, `Content-Type: application/json`
  set automatically, timing reported. A `/status/404` returned `→ 404` exactly. ✅
- **Trust discipline:** the **HTTP status code is the grounded fact** to assert on (reported
  exactly, never guessed). Request headers (auth tokens) are **never echoed** into the
  observation (`test_auth_header_not_echoed_in_output`) — secrets don't leak into logs/audit.
- **Safety:** `dangerous=True` (POST/PUT/DELETE mutate → approval-gated); the **egress floor**
  (SSRF) applies to every call — `169.254.169.254`/loopback/private hosts blocked before any
  request is built. Redirects **not** followed by default (a 3xx is reported as-is). Supports
  GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS, raw or JSON body. Auto-joins the generalist; added to
  the `qa` preset. Unit-tested in `tests/test_http.py` (12 tests, mocked transport).

## 27. `clean_csv` — transform-and-save (the last wrangling gap)

**Goal:** the cleaning SQL can't do cleanly (standardise casing/dates, trim, dedupe, fill/
drop blanks, rename, remap) → emit a cleaned file, with an auditable report of every change.

```bash
printf 'a\n' | halia data 'The file /tmp/raw_sales.csv is messy. Use clean_csv to trim rep+region, titlecase region, fill blank amount with 0, drop duplicates → /tmp/clean_sales.csv. Then query_data the CLEANED file for total by region, highest first.'
```

- **Result:** ✅ `clean_csv` trimmed/cased/filled/deduped and saved the cleaned file with a
  per-op report (`trim: 2 cells`, `titlecase: 3 changed`, `drop_duplicates: 1 removed`, `rows
  5→4`); `query_data` then grouped the **cleaned** file (North 3,500 / South 600). halia also
  flagged an honest nuance — a same-rep pair dedup didn't catch (exact-row match only).
- Ordered, deterministic ops: trim · lower/upper/titlecase · fill_blank · drop_missing ·
  drop_duplicates(±columns) · drop_empty_rows · replace(map) · rename · standardize_date
  (optional explicit `from` format for ambiguous dates). Every step reports its change =
  auditable, not a black box. Unit-tested in `tests/test_clean.py`.
- **The `data` loop is now end-to-end:** gather → **clean** → **query (SQL)** → aggregate/
  group → **visualise (bar + line)** → report (PDF/DOCX/PPTX/XLSX), all grounded.
