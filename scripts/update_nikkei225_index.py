#!/usr/bin/env python3
"""Update canonical Nikkei 225 index daily data.

Output:
- data/indexes/nikkei225/daily.csv
- runtime/update_nikkei225_index_status.json
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common_market_io import csv_text, decode_bytes, ensure_dir, fetch_bytes, log, now_iso, write_status, write_text_if_changed

DAILY_CSV_URL = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv"
FIELDNAMES = ["index_id", "date", "open", "high", "low", "close", "source", "fetched_at"]


@dataclass(frozen=True)
class Row:
    index_id: str
    date: str
    open: str
    high: str
    low: str
    close: str
    source: str
    fetched_at: str

    def key(self) -> str:
        return self.date

    def same_market_values(self, other: "Row") -> bool:
        return (self.date, self.open, self.high, self.low, self.close) == (
            other.date,
            other.open,
            other.high,
            other.low,
            other.close,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "index_id": self.index_id,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Nikkei 225 canonical daily CSV")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "").replace("_", "")


def detect_indices(header: list[str]) -> dict[str, int]:
    normalized = [normalize_header(h) for h in header]
    candidates = {
        "date": {"date", "日付", "年月日", "datadate"},
        "open": {"open", "始値"},
        "high": {"high", "高値"},
        "low": {"low", "安値"},
        "close": {"close", "終値"},
    }
    found: dict[str, int] = {}
    for canonical, names in candidates.items():
        for idx, item in enumerate(normalized):
            if item in names:
                found[canonical] = idx
                break
    if len(found) == 5:
        return found
    if len(header) >= 5:
        return {"date": 0, "open": 1, "high": 2, "low": 3, "close": 4}
    raise ValueError(f"Could not determine expected columns from header: {header}")


def parse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def parse_number(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value:
        return ""
    return f"{float(value):.2f}"


def parse_snapshot(text: str, fetched_at: str) -> list[Row]:
    reader = csv.reader(text.splitlines())
    raw_rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not raw_rows:
        raise ValueError("CSV appears to be empty")
    header = raw_rows[0]
    indices = detect_indices(header)
    parsed: list[Row] = []
    for row in raw_rows[1:]:
        try:
            parsed.append(
                Row(
                    index_id="nikkei225",
                    date=parse_date(row[indices["date"]]),
                    open=parse_number(row[indices["open"]]),
                    high=parse_number(row[indices["high"]]),
                    low=parse_number(row[indices["low"]]),
                    close=parse_number(row[indices["close"]]),
                    source=DAILY_CSV_URL,
                    fetched_at=fetched_at,
                )
            )
        except (IndexError, ValueError):
            continue
    if not parsed:
        raise ValueError("No valid rows were parsed from CSV")
    return parsed


def read_existing(path: Path) -> dict[str, Row]:
    if not path.exists():
        return {}
    result: dict[str, Row] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            if not rec.get("date"):
                continue
            row = Row(
                index_id=rec.get("index_id", "nikkei225"),
                date=rec["date"],
                open=rec.get("open", ""),
                high=rec.get("high", ""),
                low=rec.get("low", ""),
                close=rec.get("close", ""),
                source=rec.get("source", DAILY_CSV_URL),
                fetched_at=rec.get("fetched_at", ""),
            )
            result[row.key()] = row
    return result


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    data_path = repo_root / "data/indexes/nikkei225/daily.csv"
    status_path = repo_root / "runtime/update_nikkei225_index_status.json"
    ensure_dir(data_path.parent)

    fetched_at = now_iso()
    try:
        blob = fetch_bytes(DAILY_CSV_URL, timeout=args.timeout, retries=args.retries)
        latest_rows = parse_snapshot(decode_bytes(blob), fetched_at)
        existing = read_existing(data_path)
        inserted = 0
        revised = 0
        for row in latest_rows:
            current = existing.get(row.key())
            if current is None:
                existing[row.key()] = row
                inserted += 1
            elif not current.same_market_values(row):
                existing[row.key()] = row
                revised += 1
        merged = [existing[k] for k in sorted(existing)]
        changed = write_text_if_changed(data_path, csv_text((r.as_dict() for r in merged), FIELDNAMES))
        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "rows_latest": len(latest_rows),
            "rows_total": len(merged),
            "inserted_dates": inserted,
            "revised_dates": revised,
            "changed": changed,
            "source": DAILY_CSV_URL,
            "output": str(data_path.relative_to(repo_root)),
        }
        write_status(status_path, status)
        log(f"nikkei225 index rows total={len(merged)} inserted={inserted} revised={revised} changed={changed}")
        return 0
    except Exception as exc:  # noqa: BLE001
        write_status(status_path, {"ok": False, "fetched_at": fetched_at, "error": str(exc), "source": DAILY_CSV_URL})
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
