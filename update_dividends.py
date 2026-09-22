#!/usr/bin/env python3
"""Download announced ex-right/ex-dividend events from TWSE and TPEx."""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "dividends.json"
HISTORY_DIR = ROOT / "data" / "history"
LOG_FILE = ROOT / "logs" / "update.log"
TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

SOURCES = {
    "TWSE": "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL",
    "TPEx": "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost",
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


def main() -> int:
    setup_logging()
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.info("開始更新官方除權息資料")

    twse_raw = fetch_json(SOURCES["TWSE"])
    tpex_raw = fetch_json(SOURCES["TPEx"])
    items: list[dict[str, Any]] = []

    for source_rows, normalizer in ((twse_raw, normalize_twse), (tpex_raw, normalize_tpex)):
        for row in source_rows:
            try:
                item = normalizer(row)
                if item["symbol"] and item["exDividendDate"]:
                    items.append(item)
            except (ValueError, TypeError) as exc:
                logging.warning("略過無法解析的資料：%s；%s", row, exc)

    unique = {(item["source"], item["symbol"], item["exDividendDate"], item["eventType"]): item for item in items}
    items = sorted(unique.values(), key=lambda x: (x["exDividendDate"], x["symbol"]))
    now = datetime.now(TZ).replace(microsecond=0)
    output = {
        "updatedAt": now.isoformat(),
        "sources": [{"name": name, "url": url} for name, url in SOURCES.items()],
        "counts": {"total": len(items), "TWSE": sum(i["source"] == "TWSE" for i in items), "TPEx": sum(i["source"] == "TPEx" for i in items)},
        "items": items,
    }
    temporary = DATA_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(DATA_FILE)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = HISTORY_DIR / f"{now.date().isoformat()}.json"
    snapshot.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("更新完成：TWSE %s 筆、TPEx %s 筆，共 %s 筆", output["counts"]["TWSE"], output["counts"]["TPEx"], len(items))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("更新失敗")
        raise SystemExit(1)
