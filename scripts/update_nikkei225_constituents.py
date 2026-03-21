#!/usr/bin/env python3
"""Fetch current Nikkei 225 constituents from the official Nikkei components page.

Outputs:
- data/constituents/nikkei225/current.csv
- runtime/update_nikkei225_constituents_status.json
- runtime/update_nikkei225_constituents_debug.txt
"""
from __future__ import annotations

import argparse
import csv
import sys
import re
from pathlib import Path

from bs4 import BeautifulSoup

from common_market_io import (
    csv_text,
    decode_bytes,
    ensure_dir,
    fetch_bytes,
    log,
    now_iso,
    write_status,
    write_text_if_changed,
)

SOURCE_CANDIDATES = [
    {
        "label": "official_en",
        "url": "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225",
        "headers": {
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            "Referer": "https://indexes.nikkei.co.jp/en/nkave/index/profile?idx=nk225",
        },
    },
    {
        "label": "official_ja",
        "url": "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225",
        "headers": {
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225",
        },
    },
]

FIELDNAMES = [
    "index_id",
    "as_of_date",
    "sector",
    "code",
    "company_name",
    "ticker_tse",
    "symbol_stooq",
    "source",
    "fetched_at",
]

INDUSTRY_HEADINGS_EN = {
    "Pharmaceuticals",
    "Electric Machinery",
    "Automobiles & Auto parts",
    "Precision Instruments",
    "Communications",
    "Banking",
    "Other Financial Services",
    "Securities",
    "Insurance",
    "Fishery",
    "Foods",
    "Retail",
    "Services",
    "Mining",
    "Textiles & Apparel",
    "Pulp & Paper",
    "Chemicals",
    "Petroleum",
    "Rubber",
    "Glass & Ceramics",
    "Steel",
    "Nonferrous Metals",
    "Trading Companies",
    "Construction",
    "Machinery",
    "Shipbuilding",
    "Transportation Equipment",
    "Other Manufacturing",
    "Real Estate",
    "Railway & Bus",
    "Land Transport",
    "Marine Transport",
    "Air Transport",
    "Warehousing",
    "Electric Power",
    "Gas",
}

CODE_LINE_RE = re.compile(r"^(\d{4})\s+(.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch current Nikkei 225 constituents")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def normalize_lines(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text("\n")
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def extract_as_of_date(lines: list[str]) -> str:
    patterns = [
        re.compile(r"Update[:：]\s*([A-Za-z]{3}/\d{1,2}/\d{4})"),
        re.compile(r"更新日付[:：]\s*(\d{4}[./-]\d{2}[./-]\d{2})"),
    ]
    for line in lines:
        for pattern in patterns:
            m = pattern.search(line)
            if not m:
                continue
            value = m.group(1)
            if "/" in value and value[:3].isalpha():
                from datetime import datetime
                return datetime.strptime(value, "%b/%d/%Y").strftime("%Y-%m-%d")
            return value.replace(".", "-").replace("/", "-")
    return ""


def parse_rows(lines: list[str], fetched_at: str, source_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    as_of_date = extract_as_of_date(lines)
    current_sector = ""

    for raw_line in lines:
        line = raw_line.removeprefix("### ").strip()

        if line in INDUSTRY_HEADINGS_EN:
            current_sector = line
            continue

        if line == "Code Company Name":
            continue

        m = CODE_LINE_RE.match(line)
        if not m:
            continue

        code = m.group(1)
        company_name = m.group(2).strip()

        rows.append(
            {
                "index_id": "nikkei225",
                "as_of_date": as_of_date,
                "sector": current_sector,
                "code": code,
                "company_name": company_name,
                "ticker_tse": f"{code}.T",
                "symbol_stooq": f"{code}.jp",
                "source": source_url,
                "fetched_at": fetched_at,
            }
        )

    unique = {row["code"]: row for row in rows}
    return [unique[code] for code in sorted(unique)]


def read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("code")]


def write_debug(debug_path: Path, label: str, lines: list[str]) -> None:
    sample = "\n".join(lines[:250]) + "\n"
    write_text_if_changed(debug_path, f"[{label}]\n{sample}", encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = repo_root / "data/constituents/nikkei225/current.csv"
    status_path = repo_root / "runtime/update_nikkei225_constituents_status.json"
    debug_path = repo_root / "runtime/update_nikkei225_constituents_debug.txt"

    ensure_dir(output_path.parent)
    fetched_at = now_iso()

    try:
        last_error = None

        for source in SOURCE_CANDIDATES:
            label = source["label"]
            url = source["url"]
            headers = source["headers"]

            try:
                html = decode_bytes(fetch_bytes(url, timeout=args.timeout, retries=args.retries, headers=headers))
                lines = normalize_lines(html)
                rows = parse_rows(lines, fetched_at, url)

                log(f"{label}: parsed {len(rows)} rows")
                write_debug(debug_path, label, lines)

                if len(rows) >= 200:
                    changed = write_text_if_changed(output_path, csv_text(rows, FIELDNAMES))
                    status = {
                        "ok": True,
                        "fetched_at": fetched_at,
                        "rows": len(rows),
                        "changed": changed,
                        "as_of_date": rows[0].get("as_of_date", "") if rows else "",
                        "source": url,
                        "source_label": label,
                        "output": str(output_path.relative_to(repo_root)),
                    }
                    write_status(status_path, status)
                    log(f"nikkei225 constituents rows={len(rows)} changed={changed}")
                    return 0

                sample = " | ".join(lines[:20])
                log(f"{label}: too few rows; first lines sample: {sample[:1200]}")

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log(f"{label}: ERROR while fetching/parsing: {exc}")

        existing_rows = read_existing_rows(output_path)
        if len(existing_rows) >= 200:
            status = {
                "ok": True,
                "fetched_at": fetched_at,
                "rows": len(existing_rows),
                "changed": False,
                "stale_fallback": True,
                "source": "existing_csv_fallback",
                "output": str(output_path.relative_to(repo_root)),
            }
            write_status(status_path, status)
            log("live fetch failed; kept existing current.csv as fallback")
            return 0

        raise ValueError(f"Too few constituent rows parsed from all sources. last_error={last_error}")

    except Exception as exc:  # noqa: BLE001
        write_status(
            status_path,
            {
                "ok": False,
                "fetched_at": fetched_at,
                "error": str(exc),
                "sources": [s["url"] for s in SOURCE_CANDIDATES],
            },
        )
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
