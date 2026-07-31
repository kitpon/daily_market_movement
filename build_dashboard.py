"""
Daily Market Movement dashboard builder.

Reads set_stocks.duckdb, pre-computes every table for the last N trading days,
and writes a single self-contained index.html (data + logo embedded) that can be
dropped straight onto GitHub Pages.

Usage:
    python build_dashboard.py
    python build_dashboard.py --days 10 --out index.html
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))

DB_PATH = r"C:\Users\kitpo\OneDrive\claw_workspace\Database_main\set_stocks.duckdb"
LOGO_PATH = os.path.join(HERE, "UOBKH_logo.png")
TEMPLATE_PATH = os.path.join(HERE, "template.html")

SIDEBAR_DAYS = 10          # trading days shown in the sidebar
FLOW_DAYS = 10             # day-columns in the NVDR buy/sell grids
HISTORY_DAYS = 40          # trading days pulled so AvgVol5 / 10-day flow windows are complete

MAIN_ROWS = 20             # top 20 by trading value
IMPACT_ROWS = 10           # impact gainers / losers
SUB_ROWS = 30              # rows per sub table
FLOW_ROWS = 20             # top 20 NVDR buy / sell

SUB_THRESHOLDS_MB = [20.0, 50.0, 100.0]   # AvgVal5 filters for sub tables 1-3

MB = 1_000_000.0


# --------------------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------------------

PANEL_SQL = f"""
with trading_days as (
    select date
    from daily_stocks
    where volume > 0
    group by date
    having count(*) >= 100
    order by date desc
    limit {HISTORY_DAYS}
),
base as (
    select
        d.symbol, d.date, d.open, d.high, d.low, d.close,
        d.volume, d.value, d.nvdr_net, d.market_cap,
        m.industry, m.sector
    from daily_stocks d
    join stock_metadata m on m.symbol = d.symbol
    where d.date in (select date from trading_days)
      and m.industry not in ('MARKET_INDEX', 'SECTOR_INDEX')
      and d.symbol not like '.%'
      and not regexp_matches(d.symbol, '-W[0-9]*$')   -- warrants
      and d.symbol not like '%-P'                     -- preferred
      and d.close is not null
)
select
    symbol, date, open, high, low, close, volume, value, nvdr_net, market_cap,
    industry, sector,
    lag(close) over (partition by symbol order by date) as prev_close,
    avg(volume) over (partition by symbol order by date
                      rows between 5 preceding and 1 preceding) as avgvol5,
    avg(value)  over (partition by symbol order by date
                      rows between 5 preceding and 1 preceding) as avgval5,
    count(volume) over (partition by symbol order by date
                        rows between 5 preceding and 1 preceding) as n5
from base
order by date, symbol
"""

COLUMNS = [
    "symbol", "date", "open", "high", "low", "close", "volume", "value",
    "nvdr_net", "market_cap", "industry", "sector",
    "prev_close", "avgvol5", "avgval5", "n5",
]


def load_panel(con):
    rows = con.execute(PANEL_SQL).fetchall()
    return [dict(zip(COLUMNS, r)) for r in rows]


def latest_set_level(con):
    """Reference SET index level used to scale Impact into index points."""
    row = con.execute(
        "select close from daily_stocks where symbol = '.SET' and close is not null "
        "order by date desc limit 1"
    ).fetchone()
    return float(row[0]) if row else 1600.0


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def r(x, nd=2):
    """Round, preserving None, and collapse -0.0."""
    if x is None:
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d %b %Y")


def short_date(d: dt.date) -> str:
    return d.strftime("%d %b")


# --------------------------------------------------------------------------------------
# per-day table construction
# --------------------------------------------------------------------------------------

def build_day(day_rows, set_level):
    """day_rows: list of panel dicts for a single date (already filtered to traded names)."""
    traded = [x for x in day_rows if x["volume"] and x["volume"] > 0 and x["value"]]

    total_value = sum(x["value"] for x in traded) or 1.0
    total_mcap = sum(x["market_cap"] for x in traded if x["market_cap"]) or 1.0

    for x in traded:
        prev = x["prev_close"]
        x["_chg"] = (x["close"] - prev) if prev else None
        x["_pct"] = ((x["close"] / prev - 1) * 100) if prev else None
        x["_pctmkt"] = x["value"] / total_value * 100
        if x["_pct"] is not None and x["market_cap"]:
            x["_impact"] = x["_pct"] / 100 * (x["market_cap"] / total_mcap) * set_level
        else:
            x["_impact"] = None

    # ---- summary strip -----------------------------------------------------------
    adv = sum(1 for x in traded if x["_pct"] is not None and x["_pct"] > 0)
    dec = sum(1 for x in traded if x["_pct"] is not None and x["_pct"] < 0)
    unch = sum(1 for x in traded if x["_pct"] is not None and x["_pct"] == 0)
    impact_sum = sum(x["_impact"] for x in traded if x["_impact"] is not None)

    summary = {
        "turnover": r(total_value / MB, 0),          # M THB
        "names": len(traded),
        "adv": adv, "dec": dec, "unch": unch,
        "impact": r(impact_sum, 2),
        "mcap": r(total_mcap / MB / MB, 2),          # THB tn
    }

    # ---- main table: top 20 by trading value -------------------------------------
    top_value = sorted(traded, key=lambda x: x["value"], reverse=True)[:MAIN_ROWS]
    main = [[
        x["symbol"], x["sector"] or "—",
        r(x["close"]), r(x["_chg"]), r(x["_pct"]),
        r(x["open"]), r(x["high"]), r(x["low"]), r(x["prev_close"]),
        int(x["volume"]), r(x["value"] / MB, 2), r(x["_pctmkt"], 2),
    ] for x in top_value]

    # ---- impact gainers / losers -------------------------------------------------
    ranked = sorted(
        (x for x in traded if x["_impact"] is not None),
        key=lambda x: x["_impact"], reverse=True,
    )

    def impact_rows(rows):
        return [[
            x["symbol"], r(x["_impact"], 4), r(x["close"]), r(x["_pct"]),
            r(x["prev_close"]), r(x["open"]), r(x["high"]), r(x["low"]),
            r(x["_pctmkt"], 4), int(x["volume"]), r(x["value"] / MB, 2),
        ] for x in rows]

    gainers = impact_rows([x for x in ranked if x["_impact"] > 0][:IMPACT_ROWS])
    losers = impact_rows([x for x in reversed(ranked) if x["_impact"] < 0][:IMPACT_ROWS])

    # ---- sector impact -----------------------------------------------------------
    sector_map = {}
    for x in traded:
        if x["_impact"] is None:
            continue
        key = x["industry"] or "Unclassified"
        sector_map[key] = sector_map.get(key, 0.0) + x["_impact"]
    sectors = sorted(
        ([k, r(v, 4)] for k, v in sector_map.items()),
        key=lambda kv: kv[1], reverse=True,
    )

    # ---- sub tables: AvgVal5 filter, ranked by %CMPR ------------------------------
    eligible = [
        x for x in traded
        if x["avgvol5"] and x["avgvol5"] > 0 and x["avgval5"] and x["n5"] >= 3
    ]
    for x in eligible:
        x["_cmpr"] = x["volume"] / x["avgvol5"] * 100
        x["_avgval5_mb"] = x["avgval5"] / MB

    subs = []
    for threshold in SUB_THRESHOLDS_MB:
        pool = [x for x in eligible if x["_avgval5_mb"] >= threshold]
        pool.sort(key=lambda x: x["_cmpr"], reverse=True)
        subs.append([[
            x["symbol"], int(round(x["avgvol5"])), int(x["volume"]),
            r(x["_cmpr"], 2), r(x["close"]), r(x["_pct"]),
            r(x["_avgval5_mb"], 1), r(x["value"] / MB, 1),
        ] for x in pool[:SUB_ROWS]])

    return summary, main, gainers, losers, sectors, subs


def build_flow(panel_by_date, window_dates):
    """NVDR net buy/sell: rank on the most recent date in the window that has data,
    then lay out the 10-day series for each ranked name."""
    as_of = None
    for d in reversed(window_dates):          # window_dates is oldest -> newest
        if any(x["nvdr_net"] is not None for x in panel_by_date.get(d, [])):
            as_of = d
            break

    if as_of is None:
        return None, [], []

    ranked_rows = [
        x for x in panel_by_date[as_of]
        if x["nvdr_net"] is not None and x["volume"] and x["volume"] > 0
    ]
    ranked_rows.sort(key=lambda x: x["nvdr_net"], reverse=True)

    buy = [x for x in ranked_rows if x["nvdr_net"] > 0][:FLOW_ROWS]
    sell = [x for x in reversed(ranked_rows) if x["nvdr_net"] < 0][:FLOW_ROWS]

    series = {
        d: {x["symbol"]: x["nvdr_net"] for x in panel_by_date.get(d, [])}
        for d in window_dates
    }

    def rows(picks):
        out = []
        for x in picks:
            sym = x["symbol"]
            vals = []
            for d in window_dates:
                v = series[d].get(sym)
                vals.append(r(v / MB, 1) if v is not None else None)
            out.append([sym, x["sector"] or "—", vals])
        return out

    return as_of, rows(buy), rows(sell)


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--days", type=int, default=SIDEBAR_DAYS)
    ap.add_argument("--out", default=os.path.join(HERE, "index.html"))
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"database not found: {args.db}")

    con = duckdb.connect(args.db, read_only=True)
    print("reading panel ...")
    panel = load_panel(con)
    set_level = latest_set_level(con)
    con.close()

    by_date = {}
    for row in panel:
        by_date.setdefault(row["date"], []).append(row)

    all_dates = sorted(by_date)                       # oldest -> newest
    sidebar_dates = all_dates[-args.days:]            # oldest -> newest
    print(f"{len(all_dates)} trading days loaded, "
          f"building {len(sidebar_dates)}: {sidebar_dates[0]} .. {sidebar_dates[-1]}")

    days_payload = {}
    for d in sidebar_dates:
        summary, main_t, gainers, losers, sectors, subs = build_day(by_date[d], set_level)

        idx = all_dates.index(d)
        window = all_dates[max(0, idx - FLOW_DAYS + 1): idx + 1]
        as_of, flow_buy, flow_sell = build_flow(by_date, window)

        days_payload[d.isoformat()] = {
            "summary": summary,
            "main": main_t,
            "gainers": gainers,
            "losers": losers,
            "sectors": sectors,
            "sub1": subs[0], "sub2": subs[1], "sub3": subs[2],
            "flowDates": [x.isoformat() for x in window],
            "flowLabels": [short_date(x) for x in window],
            "flowAsOf": as_of.isoformat() if as_of else None,
            "flowStale": (as_of != d) if as_of else True,
            "flowBuy": flow_buy,
            "flowSell": flow_sell,
        }

    ordered = [d.isoformat() for d in reversed(sidebar_dates)]   # newest first
    data = {
        "generated": dt.datetime.now().strftime("%d %b %Y %H:%M"),
        "setLevel": r(set_level, 2),
        "subThresholds": SUB_THRESHOLDS_MB,
        "dates": ordered,
        "dateLabels": {d.isoformat(): fmt_date(d) for d in sidebar_dates},
        "dateShort": {d.isoformat(): short_date(d) for d in sidebar_dates},
        "dateDow": {d.isoformat(): d.strftime("%a") for d in sidebar_dates},
        "days": days_payload,
    }

    with open(LOGO_PATH, "rb") as f:
        logo = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    payload = json.dumps(data, separators=(",", ":"), allow_nan=False)
    html = html.replace("/*__LOGO__*/''", json.dumps(logo))
    html = html.replace("/*__DATA__*/null", payload)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(args.out) / 1024
    print(f"wrote {args.out}  ({size_kb:,.0f} KB, payload {len(payload)/1024:,.0f} KB)")


if __name__ == "__main__":
    main()
