#!/usr/bin/env python3
"""Fetch daily prices for current Nikkei 225 constituents from Stooq.

Outputs:
- data/prices/stooq/jp/<code>.csv
- data/panels/nikkei225_current_constituents_latest.csv
- data/panels/nikkei225_current_constituents_close_wide_260d.csv
- runtime/update_nikkei225_prices_status.json
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from common_market_io import csv_text, decode_bytes, ensure_dir, fetch_bytes, log, now_iso, write_status, write_text_if_changed

STOOQ_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
PRICE_FIELDNAMES = ["code", "ticker_tse", "symbol_stooq", "date", "open", "high", "low", "close", "volume", "source", "fetched_at"]
LATEST_PANEL_FIELDNAMES = ["code", "ticker_tse", "company_name", "sector", "date", "close", "volume", "price_file"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Nikkei 225 constituent prices from Stooq")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--constituents-csv", default="data/constituents/nikkei225/current.csv")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--max-symbols", type=int, default=0, help="For testing; 0 means all")
    return parser.parse_args()


def read_constituents(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Constituents file not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("code") and r.get("symbol_stooq")]
    if not rows:
        raise ValueError("Constituents CSV is empty")
    return rows


def parse_number(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value:
        return ""
    return f"{float(value):.2f}"


def parse_volume(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value:
        return ""
    return str(int(float(value)))


def normalize_price_csv(raw_text: str, *, code: str, ticker_tse: str, symbol_stooq: str, fetched_at: str) -> list[dict[str, str]]:
    reader = csv.DictReader(raw_text.splitlines())
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError(f"Unexpected Stooq CSV header for {symbol_stooq}: {reader.fieldnames}")
    rows: list[dict[str, str]] = []
    for rec in reader:
        date = rec.get("Date", "").strip()
        if not date:
            continue
        try:
            date = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            continue
        close_val = rec.get("Close", "").strip()
        if close_val in {"", "-"}:
            continue
        rows.append(
            {
                "code": code,
                "ticker_tse": ticker_tse,
                "symbol_stooq": symbol_stooq,
                "date": date,
                "open": parse_number(rec.get("Open", "")),
                "high": parse_number(rec.get("High", "")),
                "low": parse_number(rec.get("Low", "")),
                "close": parse_number(close_val),
                "volume": parse_volume(rec.get("Volume", "")),
                "source": STOOQ_CSV_URL.format(symbol=symbol_stooq),
                "fetched_at": fetched_at,
            }
        )
    if not rows:
        raise ValueError(f"No price rows parsed for {symbol_stooq}")
    rows.sort(key=lambda r: r["date"])
    return rows


def build_latest_panel(constituents: list[dict[str, str]], file_map: dict[str, Path], repo_root: Path) -> list[dict[str, str]]:
    constituent_map = {row["code"]: row for row in constituents}
    latest_rows: list[dict[str, str]] = []
    for code, path in sorted(file_map.items()):
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        last = rows[-1]
        meta = constituent_map[code]
        latest_rows.append(
            {
                "code": code,
                "ticker_tse": meta["ticker_tse"],
                "company_name": meta["company_name"],
                "sector": meta["sector"],
                "date": last["date"],
                "close": last["close"],
                "volume": last["volume"],
                "price_file": str(path.relative_to(repo_root)),
            }
        )
    return latest_rows


def build_close_wide_recent(file_map: dict[str, Path], lookback_days: int = 260) -> tuple[list[str], list[dict[str, str]]]:
    closes_by_date: dict[str, dict[str, str]] = defaultdict(dict)
    all_codes = sorted(file_map)
    all_dates: set[str] = set()
    for code, path in file_map.items():
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for rec in rows[-lookback_days:]:
            date = rec["date"]
            closes_by_date[date][code] = rec["close"]
            all_dates.add(date)
    fieldnames = ["date"] + all_codes
    wide_rows: list[dict[str, str]] = []
    for date in sorted(all_dates):
        row = {"date": date}
        for code in all_codes:
            row[code] = closes_by_date[date].get(code, "")
        wide_rows.append(row)
    return fieldnames, wide_rows


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    constituents_path = repo_root / args.constituents_csv
    status_path = repo_root / "runtime/update_nikkei225_prices_status.json"
    prices_root = repo_root / "data/prices/stooq/jp"
    latest_panel_path = repo_root / "data/panels/nikkei225_current_constituents_latest.csv"
    close_wide_path = repo_root / "data/panels/nikkei225_current_constituents_close_wide_260d.csv"

    ensure_dir(prices_root)
    ensure_dir(latest_panel_path.parent)
    fetched_at = now_iso()

    try:
        constituents = read_constituents(constituents_path)
        if args.max_symbols > 0:
            constituents = constituents[: args.max_symbols]

        changed_files = 0
        failures: list[dict[str, str]] = []
        file_map: dict[str, Path] = {}

        for idx, row in enumerate(constituents, start=1):
            code = row["code"]
            symbol_stooq = row["symbol_stooq"]
            ticker_tse = row["ticker_tse"]
            url = STOOQ_CSV_URL.format(symbol=symbol_stooq)
            log(f"[{idx}/{len(constituents)}] fetching {symbol_stooq}")
            try:
                raw_text = decode_bytes(fetch_bytes(url, timeout=args.timeout, retries=args.retries, sleep_seconds=args.sleep_seconds))
                price_rows = normalize_price_csv(raw_text, code=code, ticker_tse=ticker_tse, symbol_stooq=symbol_stooq, fetched_at=fetched_at)
                price_file = prices_root / f"{code}.csv"
                changed = write_text_if_changed(price_file, csv_text(price_rows, PRICE_FIELDNAMES))
                if changed:
                    changed_files += 1
                file_map[code] = price_file
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "symbol_stooq": symbol_stooq, "error": str(exc)})
                log(f"ERROR {symbol_stooq}: {exc}")

        if not file_map:
            raise RuntimeError("No price files were written")

        latest_panel_rows = build_latest_panel(constituents, file_map, repo_root)
        latest_panel_changed = write_text_if_changed(latest_panel_path, csv_text(latest_panel_rows, LATEST_PANEL_FIELDNAMES))

        close_wide_fields, close_wide_rows = build_close_wide_recent(file_map)
        close_wide_changed = write_text_if_changed(close_wide_path, csv_text(close_wide_rows, close_wide_fields))

        status = {
            "ok": len(failures) == 0,
            "fetched_at": fetched_at,
            "symbols_requested": len(constituents),
            "symbols_succeeded": len(file_map),
            "symbols_failed": len(failures),
            "price_files_changed": changed_files,
            "latest_panel_changed": latest_panel_changed,
            "close_wide_changed": close_wide_changed,
            "latest_panel_output": str(latest_panel_path.relative_to(repo_root)),
            "close_wide_output": str(close_wide_path.relative_to(repo_root)),
            "failures": failures[:20],
            "source_template": STOOQ_CSV_URL,
        }
        write_status(status_path, status)
        log(f"prices success={len(file_map)} failures={len(failures)} changed_files={changed_files}")
        return 1 if failures else 0
    except Exception as exc:  # noqa: BLE001
        write_status(status_path, {"ok": False, "fetched_at": fetched_at, "error": str(exc), "source_template": STOOQ_CSV_URL})
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
