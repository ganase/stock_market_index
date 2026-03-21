#!/usr/bin/env python3
"""Update Nikkei 225 daily data in a GitHub repository.

This script is designed for GitHub Actions, but it also works locally.
It downloads the official daily CSV published by Nikkei Indexes,
normalizes it, and merges it into a repository-managed master CSV.

Outputs
-------
- data/nikkei225_latest_3years.csv
- data/nikkei225_master.csv
- data/nikkei225_status.json (runtime/status file; optional to commit)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DAILY_CSV_URL = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv"
USER_AGENT = (
    "Mozilla/5.0 (compatible; Nikkei225GitHubUpdater/1.0; +https://github.com/)"
)
CANONICAL_HEADERS = ["date", "open", "high", "low", "close", "source", "fetched_at"]


@dataclass(frozen=True)
class Row:
    date: str
    open: str
    high: str
    low: str
    close: str
    source: str
    fetched_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }

    def same_market_values(self, other: "Row") -> bool:
        return (
            self.date == other.date
            and self.open == other.open
            and self.high == other.high
            and self.low == other.low
            and self.close == other.close
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Nikkei 225 data in a GitHub repo")
    parser.add_argument("--data-dir", default="data", help="Output folder inside the repository")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Download retry count")
    return parser.parse_args()


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def fetch_bytes(url: str, timeout: int, retries: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            log(f"download failed (attempt {attempt}/{retries}): {exc}")
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"download failed after {retries} attempts: {last_error}")


def decode_bytes(blob: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", blob, 0, 1, "Unable to decode CSV")


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


def read_master(path: Path) -> dict[str, Row]:
    if not path.exists():
        return {}

    result: dict[str, Row] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            date = rec.get("date", "").strip()
            if not date:
                continue
            result[date] = Row(
                date=date,
                open=rec.get("open", ""),
                high=rec.get("high", ""),
                low=rec.get("low", ""),
                close=rec.get("close", ""),
                source=rec.get("source", DAILY_CSV_URL),
                fetched_at=rec.get("fetched_at", ""),
            )
    return result


def merge_rows(master: dict[str, Row], latest_rows: list[Row]) -> tuple[list[Row], int, int]:
    inserted = 0
    revised = 0

    for row in latest_rows:
        existing = master.get(row.date)
        if existing is None:
            master[row.date] = row
            inserted += 1
        elif not existing.same_market_values(row):
            master[row.date] = row
            revised += 1
        else:
            # Keep the existing row unchanged so identical market data does not
            # churn fetched_at/source fields and create noisy commits.
            pass

    merged = sorted(master.values(), key=lambda r: r.date)
    return merged, inserted, revised


def rows_to_csv_text(rows: list[Row]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=CANONICAL_HEADERS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_dict())
    return buf.getvalue()


def write_text_if_changed(path: Path, content: str, encoding: str = "utf-8") -> bool:
    old = None
    if path.exists():
        old = path.read_text(encoding=encoding)
    if old == content:
        return False
    path.write_text(content, encoding=encoding)
    return True


def write_status(path: Path, status: dict[str, object]) -> None:
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    ensure_dir(data_dir)

    snapshot_path = data_dir / "nikkei225_latest_3years.csv"
    master_path = data_dir / "nikkei225_master.csv"
    status_path = data_dir / "nikkei225_status.json"

    fetched_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    try:
        log(f"start: data_dir={data_dir}")
        blob = fetch_bytes(DAILY_CSV_URL, timeout=args.timeout, retries=args.retries)
        text = decode_bytes(blob)
        latest_rows = parse_snapshot(text, fetched_at)
        master = read_master(master_path)
        merged_rows, inserted, revised = merge_rows(master, latest_rows)

        snapshot_changed = write_text_if_changed(snapshot_path, rows_to_csv_text(latest_rows))
        master_changed = write_text_if_changed(master_path, rows_to_csv_text(merged_rows))

        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "snapshot_rows": len(latest_rows),
            "master_rows": len(merged_rows),
            "inserted_dates": inserted,
            "revised_dates": revised,
            "snapshot_changed": snapshot_changed,
            "master_changed": master_changed,
            "latest_date_in_snapshot": max(r.date for r in latest_rows),
            "oldest_date_in_snapshot": min(r.date for r in latest_rows),
            "source": DAILY_CSV_URL,
        }
        write_status(status_path, status)

        log(f"snapshot rows : {len(latest_rows)}")
        log(f"master rows   : {len(merged_rows)}")
        log(f"inserted      : {inserted}")
        log(f"revised       : {revised}")
        log(f"files changed : snapshot={snapshot_changed}, master={master_changed}")
        return 0
    except Exception as exc:  # noqa: BLE001
        status = {
            "ok": False,
            "fetched_at": fetched_at,
            "error": str(exc),
            "source": DAILY_CSV_URL,
        }
        write_status(status_path, status)
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
