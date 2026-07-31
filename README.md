# Daily Market Movement dashboard

Static, self-contained HTML dashboard of daily SET market movement, built from
`Database_main/set_stocks.duckdb`.

## Files

| File | Role |
|---|---|
| `index.html` | **The deliverable.** Everything embedded — data, logo, CSS, JS. This is the only file GitHub Pages needs. |
| `template.html` | Layout/design source. Contains two placeholders the builder fills in. Edit this, not `index.html`. |
| `build_dashboard.py` | Queries DuckDB, pre-computes every table, injects the payload into the template. |
| `UOBKH_logo.png` | Embedded as a base64 data URI at build time. |

## Refresh

```bash
python build_dashboard.py
```

Options: `--days 10` (sidebar trading days), `--out index.html`, `--db <path>`.

Any edit to `template.html` requires a rebuild — `index.html` is generated, so
changes made directly to it are overwritten.

## Deploy to GitHub Pages

Only `index.html` needs to be published.

```bash
git add index.html && git commit -m "Update daily market movement" && git push
```

Then Settings → Pages → deploy from branch (root). No Jekyll config, no build
step, no `fetch()` — the page also works by double-clicking it locally.

## Data architecture

All aggregation happens in Python at build time; the browser only sorts and
renders. The payload is a single JSON object embedded in a `<script>` tag:

```
DATA
├── dates[]                     10 trading days, newest first
├── dateLabels / dateShort / dateDow
├── setLevel                    reference SET level used to scale Impact
└── days[<iso date>]
    ├── summary                 turnover, mcap, impact, adv/dec/unch, names
    ├── main[20]                top 20 by trading value  (+ intraday range bar)
    ├── gainers[10] / losers[10]   impact ranking
    ├── sectors[]               industry-level impact
    ├── sub1[30] / sub2[30] / sub3[30]   AvgVal5 ≥ 20 / 50 / 100 M฿, ranked by %CMPR
    ├── flowDates[10] / flowLabels[10]
    ├── flowAsOf / flowStale    which day the NVDR ranking came from
    └── flowBuy[20] / flowSell[20]   [symbol, sector, [10 daily net values]]
```

Rows are arrays rather than objects to keep the payload small (~120 KB for 10
days; whole file ~230 KB). Scaling to 20–30 sidebar days stays comfortably
under 1 MB. If it ever needs to hold months of history, split `days` into
per-date JSON files and `fetch()` them — but then the page must be served over
http, not opened from disk.

## Definitions

- **%CMPR** = trade volume ÷ average volume of the *previous* 5 trading days × 100
- **AvgVal5** = average trading value of the previous 5 trading days
- **Impact** = %change × (market cap ÷ total market cap) × reference SET level,
  in estimated index points
- **NVDR net** = NVDR buy value − NVDR sell value

## Known data limitations

These come from the database, not the dashboard:

1. **NVDR lags.** The two most recent trading days (27 and 30 Jul 2026) have no
   NVDR figures at all, and historically only ~330 of ~660 traded names carry
   NVDR on any given day. When the selected day has no NVDR, the buy/sell tables
   rank on the most recent day in the 10-day window that does, and show an amber
   banner saying so. The 10-day grid still displays every day that has data.
2. **Missing sessions.** 28 and 29 Jul 2026 are absent from the database
   entirely. "10 trading days" means the 10 most recent dates present, so a gap
   in the source silently widens the window.
3. **Impact uses full market cap**, not free float. The database has no
   free-float factor, so heavily-controlled names are overweighted — DELTA alone
   carries ~16% of total market cap here versus a much smaller real index
   weight. Treat Impact as directional, not as the exchange's official figure.
4. **`company_name` is unusable** — it equals the symbol for 1,020 of 1,024
   rows, so the tables show SET **sector** instead.
5. **Indices live in `daily_stocks`** (`.SET`, `.SET50`, `.ETRON`, `.ICT`) and
   are excluded, along with warrants (`-W*`) and preferred shares (`-P`).
   `.SET` itself has only 2 rows, so it cannot be used as a market benchmark.
