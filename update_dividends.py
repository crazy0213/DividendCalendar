#!/usr/bin/env python3
"""Download announced ex-right/ex-dividend events from TWSE and TPEx."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "dividends.json"
DATABASE_FILE = ROOT / "data" / "dividends.db"
HISTORY_DIR = ROOT / "data" / "history"
LOG_FILE = ROOT / "logs" / "update.log"
TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

SOURCES = {
    "TWSE": "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL",
    "TPEx": "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost",
    "TWSE_ETF_HISTORY": "https://www.twse.com.tw/rwd/zh/ETF/etfDiv",
}


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def fetch_json(url: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DividendCalendar/1.0", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
                if not isinstance(payload, list):
                    raise ValueError("官方 API 回傳格式不是陣列")
                return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            logging.warning("下載失敗（第 %s 次）：%s", attempt, exc)
    raise RuntimeError(f"無法下載 {url}: {last_error}")


def roc_date_to_iso(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 7:
        raise ValueError(f"無法辨識民國日期：{value!r}")
    year = int(digits[:3]) + 1911
    return datetime(year, int(digits[3:5]), int(digits[5:7])).date().isoformat()


def number_or_none(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--", "尚未公告"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def security_type(symbol: str) -> str:
    """Classify exchange-traded products conservatively from Taiwan symbol rules."""
    normalized = symbol.upper().strip()
    if re.fullmatch(r"010\d{2}T", normalized):
        return "REIT"
    if normalized.startswith("020"):
        return "ETN"
    if normalized.startswith("00") or re.fullmatch(r"00\d{3}[A-Z]", normalized):
        return "ETF"
    return "STOCK"


def normalize_twse(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("Code", "")).strip()
    event = str(row.get("Exdividend", "")).strip()
    cash = number_or_none(row.get("CashDividend"))
    has_dividend = "息" in event
    return {
        "symbol": symbol,
        "name": str(row.get("Name", "")).strip(),
        "market": "TWSE",
        "type": security_type(symbol),
        "exDividendDate": roc_date_to_iso(row.get("Date")),
        "cashDividend": cash,
        "stockDividendRatio": number_or_none(row.get("StockDividendRatio")),
        "eventType": {"息": "ex-dividend", "權": "ex-rights", "權息": "ex-rights-dividend"}.get(event, event),
        "status": "pending" if has_dividend and cash is None else "announced",
        "source": "TWSE",
    }


def normalize_tpex(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
    event = str(row.get("ExRrightsExDividend", "")).strip()
    cash = number_or_none(row.get("CashDividend"))
    has_dividend = "息" in event
    return {
        "symbol": symbol,
        "name": str(row.get("CompanyName", "")).strip(),
        "market": "TPEx",
        "type": security_type(symbol),
        "exDividendDate": roc_date_to_iso(row.get("ExRrightsExDividendDate")),
        "cashDividend": cash,
        "stockDividendRatio": number_or_none(row.get("StockDividendRatio")),
        "eventType": {"除息": "ex-dividend", "除權": "ex-rights", "除權息": "ex-rights-dividend"}.get(event, event),
        "status": "pending" if has_dividend and cash is None else "announced",
        "source": "TPEx",
    }


def fetch_twse_etf_history(end_year: int) -> list[list[Any]]:
    url = f"{SOURCES['TWSE_ETF_HISTORY']}?startDate=2005&endDate={end_year}&response=json"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DividendCalendar/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("TWSE ETF 歷史收益分配 API 回傳格式異常")
    return rows


def normalize_twse_etf_history(row: list[Any]) -> dict[str, Any]:
    if len(row) < 8:
        raise ValueError("TWSE ETF 歷史收益分配欄位不足")
    cash = number_or_none(row[5])
    return {
        "symbol": str(row[0] or "").strip(),
        "name": str(row[1] or "").strip(),
        "market": "TWSE",
        "type": "ETF",
        "exDividendDate": roc_date_to_iso(row[2]),
        "cashDividend": cash,
        "stockDividendRatio": None,
        "eventType": "ex-dividend",
        "status": "pending" if cash is None else "announced",
        "source": "TWSE",
        "recordDate": roc_date_to_iso(row[3]) if row[3] else None,
        "paymentDate": roc_date_to_iso(row[4]) if row[4] else None,
        "announcementYear": int(row[7]) + 1911 if int(row[7]) < 1911 else int(row[7]),
    }


def open_database() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS dividend_events (
            source TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ex_dividend_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            security_type TEXT NOT NULL,
            cash_dividend REAL,
            stock_dividend_ratio REAL,
            status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            is_in_latest_feed INTEGER NOT NULL DEFAULT 1,
            record_date TEXT,
            payment_date TEXT,
            announcement_year INTEGER,
            PRIMARY KEY (source, symbol, ex_dividend_date, event_type)
        );
        CREATE INDEX IF NOT EXISTS idx_dividend_events_date
            ON dividend_events (ex_dividend_date);
        CREATE INDEX IF NOT EXISTS idx_dividend_events_symbol
            ON dividend_events (symbol);
        CREATE INDEX IF NOT EXISTS idx_dividend_events_type
            ON dividend_events (security_type);
        CREATE TABLE IF NOT EXISTS update_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            updated_at TEXT NOT NULL,
            twse_count INTEGER NOT NULL,
            tpex_count INTEGER NOT NULL,
            feed_total INTEGER NOT NULL,
            database_total INTEGER NOT NULL
        );
        """
    )
    existing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(dividend_events)").fetchall()
    }
    for column, definition in (
        ("record_date", "TEXT"),
        ("payment_date", "TEXT"),
        ("announcement_year", "INTEGER"),
    ):
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE dividend_events ADD COLUMN {column} {definition}")
    return connection


def store_items(connection: sqlite3.Connection, items: list[dict[str, Any]], seen_at: str) -> None:
    connection.execute("UPDATE dividend_events SET is_in_latest_feed = 0")
    sql = """
        INSERT INTO dividend_events (
            source, symbol, ex_dividend_date, event_type, name, market,
            security_type, cash_dividend, stock_dividend_ratio, status,
            first_seen_at, last_seen_at, is_in_latest_feed,
            record_date, payment_date, announcement_year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(source, symbol, ex_dividend_date, event_type) DO UPDATE SET
            name = excluded.name,
            market = excluded.market,
            security_type = excluded.security_type,
            cash_dividend = COALESCE(excluded.cash_dividend, dividend_events.cash_dividend),
            stock_dividend_ratio = COALESCE(excluded.stock_dividend_ratio, dividend_events.stock_dividend_ratio),
            status = CASE
                WHEN excluded.status = 'announced' OR dividend_events.status = 'announced' THEN 'announced'
                ELSE 'pending'
            END,
            last_seen_at = excluded.last_seen_at,
            is_in_latest_feed = 1,
            record_date = COALESCE(excluded.record_date, dividend_events.record_date),
            payment_date = COALESCE(excluded.payment_date, dividend_events.payment_date),
            announcement_year = COALESCE(excluded.announcement_year, dividend_events.announcement_year)
    """
    for item in items:
        connection.execute(
            sql,
            (
                item["source"], item["symbol"], item["exDividendDate"], item["eventType"],
                item["name"], item["market"], item["type"], item["cashDividend"],
                item["stockDividendRatio"], item["status"], seen_at, seen_at,
                item.get("recordDate"), item.get("paymentDate"), item.get("announcementYear"),
            ),
        )


def export_database_items(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, symbol, name, market, security_type, ex_dividend_date,
               cash_dividend, stock_dividend_ratio, event_type, status,
               first_seen_at, last_seen_at, is_in_latest_feed,
               record_date, payment_date, announcement_year
        FROM dividend_events
        ORDER BY ex_dividend_date, symbol
        """
    ).fetchall()
    return [
        {
            "symbol": row["symbol"],
            "name": row["name"],
            "market": row["market"],
            "type": row["security_type"],
            "exDividendDate": row["ex_dividend_date"],
            "cashDividend": row["cash_dividend"],
            "stockDividendRatio": row["stock_dividend_ratio"],
            "eventType": row["event_type"],
            "status": row["status"],
            "source": row["source"],
            "firstSeenAt": row["first_seen_at"],
            "lastSeenAt": row["last_seen_at"],
            "isInLatestFeed": bool(row["is_in_latest_feed"]),
            "recordDate": row["record_date"],
            "paymentDate": row["payment_date"],
            "announcementYear": row["announcement_year"],
        }
        for row in rows
    ]


def main() -> int:
    setup_logging()
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.info("開始更新官方除權息資料")

    twse_raw = fetch_json(SOURCES["TWSE"])
    tpex_raw = fetch_json(SOURCES["TPEx"])
    twse_etf_history_raw = fetch_twse_etf_history(datetime.now(TZ).year)
    items: list[dict[str, Any]] = []

    for source_rows, normalizer in ((twse_raw, normalize_twse), (tpex_raw, normalize_tpex)):
        for row in source_rows:
            try:
                item = normalizer(row)
                if item["symbol"] and item["exDividendDate"]:
                    items.append(item)
            except (ValueError, TypeError) as exc:
                logging.warning("略過無法解析的資料：%s；%s", row, exc)

    for row in twse_etf_history_raw:
        try:
            item = normalize_twse_etf_history(row)
            if item["symbol"] and item["exDividendDate"]:
                items.append(item)
        except (ValueError, TypeError, IndexError) as exc:
            logging.warning("略過無法解析的 TWSE ETF 歷史資料：%s；%s", row[:6], exc)

    unique = {(item["source"], item["symbol"], item["exDividendDate"], item["eventType"]): item for item in items}
    feed_items = sorted(unique.values(), key=lambda x: (x["exDividendDate"], x["symbol"]))
    now = datetime.now(TZ).replace(microsecond=0)
    seen_at = now.isoformat()
    with open_database() as database:
        store_items(database, feed_items, seen_at)
        items = export_database_items(database)
        feed_twse = sum(i["source"] == "TWSE" for i in feed_items)
        feed_tpex = sum(i["source"] == "TPEx" for i in feed_items)
        database.execute(
            """INSERT INTO update_runs
               (updated_at, twse_count, tpex_count, feed_total, database_total)
               VALUES (?, ?, ?, ?, ?)""",
            (seen_at, feed_twse, feed_tpex, len(feed_items), len(items)),
        )
    output = {
        "updatedAt": seen_at,
        "sources": [{"name": name, "url": url} for name, url in SOURCES.items()],
        "counts": {
            "total": len(items),
            "TWSE": sum(i["source"] == "TWSE" for i in items),
            "TPEx": sum(i["source"] == "TPEx" for i in items),
            "latestFeed": len(feed_items),
            "twseEtfHistorySourceRows": len(twse_etf_history_raw),
        },
        "items": items,
    }
    temporary = DATA_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_FILE)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = HISTORY_DIR / f"{now.date().isoformat()}.json"
    snapshot.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info(
        "更新完成：本次官方來源 %s 筆，累積資料庫 %s 筆",
        len(feed_items), len(items),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("更新失敗")
        raise SystemExit(1)
