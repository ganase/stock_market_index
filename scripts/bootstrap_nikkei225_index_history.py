#!/usr/bin/env python3
"""Bootstrap older Nikkei 225 history into the canonical daily index CSV.

This is intended as a one-time or occasional backfill tool. It reads a manually
prepared historical file (for example, a CSV exported from Nikkei's historical
page) and merges it into `data/indexes/nikkei225/daily.csv` without disturbing
existing daily update behavior.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common_market_io import csv_text, ensure_dir, log, now_iso, write_status, write_text_if_changed

FIELDNAMES = ["index_id", "date", "open", "high", "low", "close", "source", "fetched_at"]
DEFAULT_SOURCE_LABEL = "https://indexes.nikkei.co.jp/en/nkave/archives/data"


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
    parser = argparse.ArgumentParser(description="Bootstrap older Nikkei 225 history into canonical daily.csv")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--input-path", required=True, help="Historical CSV/TSV file to merge")
    parser.add_argument("--source-label", default=DEFAULT_SOURCE_LABEL, help="Source label to stamp into merged rows")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing rows when the imported row has different values",
    )
    return parser.parse_args()


def normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "").replace("_", "")


def detect_indices(header: list[str]) -> dict[str, int]:
    normalized = [normalize_header(item) for item in header]
    candidates = {
        "date": {"date", "日付", "年月日"},
        "open": {"open", "始値"},
        "high": {"high", "高値"},
        "low": {"low", "安値"},
        "close": {"close", "終値"},
    }
    indices: dict[str, int] = {}
    for name, aliases in candidates.items():
        for idx, item in enumerate(normalized):
            if item in aliases:
                indices[name] = idx
                break
    if "date" not in indices or "close" not in indices:
        raise ValueError(f"Could not find required date/close columns in header: {header}")
    return indices


def parse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%b/%d/%Y", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def parse_number(value: str) -> str:
    value = value.strip().replace(",", "")
    if not value or value in {"-", "NA", "N/A"}:
        return ""
    return f"{float(value):.2f}"


def read_existing(path: Path) -> dict[str, Row]:
    if not path.exists():
        return {}
    rows: dict[str, Row] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for rec in csv.DictReader(handle):
            if not rec.get("date"):
                continue
            row = Row(
                index_id=rec.get("index_id", "nikkei225"),
                date=rec["date"],
                open=rec.get("open", ""),
                high=rec.get("high", ""),
                low=rec.get("low", ""),
                close=rec.get("close", ""),
                source=rec.get("source", DEFAULT_SOURCE_LABEL),
                fetched_at=rec.get("fetched_at", ""),
            )
            rows[row.date] = row
    return rows


def read_bootstrap_rows(path: Path, source_label: str, fetched_at: str) -> list[Row]:
    sample = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    reader = csv.reader(sample.splitlines(), dialect)
    raw_rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not raw_rows:
        raise ValueError(f"Historical input file is empty: {path}")
    indices = detect_indices(raw_rows[0])
    parsed: list[Row] = []
    for row in raw_rows[1:]:
        try:
            parsed.append(
                Row(
                    index_id="nikkei225",
                    date=parse_date(row[indices["date"]]),
                    open=parse_number(row[indices["open"]]) if "open" in indices and indices["open"] < len(row) else "",
                    high=parse_number(row[indices["high"]]) if "high" in indices and indices["high"] < len(row) else "",
                    low=parse_number(row[indices["low"]]) if "low" in indices and indices["low"] < len(row) else "",
                    close=parse_number(row[indices["close"]]),
                    source=source_label,
                    fetched_at=fetched_at,
                )
            )
        except (IndexError, ValueError):
            continue
    if not parsed:
        raise ValueError(f"No valid rows parsed from historical input file: {path}")
    return parsed


def fill_missing_fields(existing: Row, incoming: Row) -> Row:
    return Row(
        index_id=existing.index_id,
        date=existing.date,
        open=existing.open or incoming.open,
        high=existing.high or incoming.high,
        low=existing.low or incoming.low,
        close=existing.close or incoming.close,
        source=existing.source if existing.source else incoming.source,
        fetched_at=existing.fetched_at if existing.fetched_at else incoming.fetched_at,
    )


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    input_path = Path(args.input_path).resolve()
    data_path = repo_root / "data/indexes/nikkei225/daily.csv"
    status_path = repo_root / "runtime/bootstrap_nikkei225_index_history_status.json"
    ensure_dir(data_path.parent)
    fetched_at = now_iso()

    try:
        existing = read_existing(data_path)
        bootstrap_rows = read_bootstrap_rows(input_path, args.source_label, fetched_at)

        inserted = 0
        filled = 0
        overwritten = 0
        skipped = 0

        for row in bootstrap_rows:
            current = existing.get(row.date)
            if current is None:
                existing[row.date] = row
                inserted += 1
                continue

            if args.overwrite_existing and current != row:
                existing[row.date] = row
                overwritten += 1
                continue

            merged = fill_missing_fields(current, row)
            if merged != current:
                existing[row.date] = merged
                filled += 1
            else:
                skipped += 1

        merged_rows = [existing[key] for key in sorted(existing)]
        changed = write_text_if_changed(data_path, csv_text((row.as_dict() for row in merged_rows), FIELDNAMES))
        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "input_path": str(input_path),
            "source_label": args.source_label,
            "overwrite_existing": args.overwrite_existing,
            "rows_input": len(bootstrap_rows),
            "rows_total": len(merged_rows),
            "inserted": inserted,
            "filled_missing_fields": filled,
            "overwritten": overwritten,
            "skipped": skipped,
            "changed": changed,
            "output": str(data_path.relative_to(repo_root)),
        }
        write_status(status_path, status)
        log(
            f"bootstrap nikkei225 history inserted={inserted} filled={filled} "
            f"overwritten={overwritten} skipped={skipped} changed={changed}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        write_status(
            status_path,
            {
                "ok": False,
                "fetched_at": fetched_at,
                "input_path": str(input_path),
                "error": str(exc),
                "source_label": args.source_label,
            },
        )
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
