#!/usr/bin/env python3
"""Fetch current Nikkei 225 constituents from the official Nikkei components page.

Output:
- data/constituents/nikkei225/current.csv
- runtime/update_nikkei225_constituents_status.json
"""
from __future__ import annotations

import argparse
import re
import sys
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

COMPONENTS_URL = "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225"

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

# 英語ページの業種見出し
INDUSTRY_HEADINGS = {
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


def parse_rows_precise(lines: list[str], fetched_at: str) -> list[dict[str, str]]:
    """業種見出しを追いながら拾う本命ロジック。"""
    rows: list[dict[str, str]] = []
    as_of_date = extract_as_of_date(lines)
    current_sector = ""

    for line in lines:
        if line in INDUSTRY_HEADINGS:
            current_sector = line
            continue

        m = CODE_LINE_RE.match(line)
        if not m:
            continue

        code = m.group(1)
        company_name = m.group(2).strip()

        # セクタ未確定でも拾うが、できれば見出しが付いている方が望ましい
        rows.append(
            {
                "index_id": "nikkei225",
                "as_of_date": as_of_date,
                "sector": current_sector,
                "code": code,
                "company_name": company_name,
                "ticker_tse": f"{code}.T",
                "symbol_stooq": f"{code}.jp",
                "source": COMPONENTS_URL,
                "fetched_at": fetched_at,
            }
        )

    unique = {row["code"]: row for row in rows}
    return [unique[code] for code in sorted(unique)]


def parse_rows_fallback(lines: list[str], fetched_at: str) -> list[dict[str, str]]:
    """業種見出しが取れなくても、4桁コード行だけで最低限復元する保険ロジック。"""
    rows: list[dict[str, str]] = []
    as_of_date = extract_as_of_date(lines)

    for line in lines:
        m = CODE_LINE_RE.match(line)
        if not m:
            continue
        code = m.group(1)
        company_name = m.group(2).strip()
        rows.append(
            {
                "index_id": "nikkei225",
                "as_of_date": as_of_date,
                "sector": "",
                "code": code,
                "company_name": company_name,
                "ticker_tse": f"{code}.T",
                "symbol_stooq": f"{code}.jp",
                "source": COMPONENTS_URL,
                "fetched_at": fetched_at,
            }
        )

    unique = {row["code"]: row for row in rows}
    return [unique[code] for code in sorted(unique)]


def parse_rows(lines: list[str], fetched_at: str) -> list[dict[str, str]]:
    rows = parse_rows_precise(lines, fetched_at)

    # 想定225銘柄に満たない場合は保険ロジックへ
    if len(rows) < 200:
        log(f"precise parser returned only {len(rows)} rows; falling back to broad parser")
        rows = parse_rows_fallback(lines, fetched_at)

    return rows


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = repo_root / "data/constituents/nikkei225/current.csv"
    status_path = repo_root / "runtime/update_nikkei225_constituents_status.json"
    ensure_dir(output_path.parent)
    fetched_at = now_iso()

    try:
        html = decode_bytes(fetch_bytes(COMPONENTS_URL, timeout=args.timeout, retries=args.retries))
        lines = normalize_lines(html)
        rows = parse_rows(lines, fetched_at)

        if len(rows) < 200:
            raise ValueError(f"Too few constituent rows parsed: {len(rows)}")

        changed = write_text_if_changed(output_path, csv_text(rows, FIELDNAMES))
        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "rows": len(rows),
            "changed": changed,
            "as_of_date": rows[0].get("as_of_date", "") if rows else "",
            "source": COMPONENTS_URL,
            "output": str(output_path.relative_to(repo_root)),
        }
        write_status(status_path, status)
        log(f"nikkei225 constituents rows={len(rows)} changed={changed}")
        return 0

    except Exception as exc:  # noqa: BLE001
        write_status(
            status_path,
            {
                "ok": False,
                "fetched_at": fetched_at,
                "error": str(exc),
                "source": COMPONENTS_URL,
            },
        )
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())            value = m.group(1)
            if "/" in value:
                from datetime import datetime

                return datetime.strptime(value, "%b/%d/%Y").strftime("%Y-%m-%d")
            return value.replace(".", "-")
    return ""


def parse_rows(lines: list[str], fetched_at: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    as_of_date = extract_as_of_date(lines)
    for idx, line in enumerate(lines):
        if line != "Code Company Name":
            continue
        sector = lines[idx - 1] if idx > 0 else ""
        cursor = idx + 1
        while cursor < len(lines):
            data_line = lines[cursor]
            m = re.match(r"^(\d{4})\s+(.+)$", data_line)
            if not m:
                break
            code = m.group(1)
            company_name = m.group(2).strip()
            rows.append(
                {
                    "index_id": "nikkei225",
                    "as_of_date": as_of_date,
                    "sector": sector,
                    "code": code,
                    "company_name": company_name,
                    "ticker_tse": f"{code}.T",
                    "symbol_stooq": f"{code}.jp",
                    "source": COMPONENTS_URL,
                    "fetched_at": fetched_at,
                }
            )
            cursor += 1
    unique = {row["code"]: row for row in rows}
    return [unique[code] for code in sorted(unique)]


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = repo_root / "data/constituents/nikkei225/current.csv"
    status_path = repo_root / "runtime/update_nikkei225_constituents_status.json"
    ensure_dir(output_path.parent)
    fetched_at = now_iso()
    try:
        html = decode_bytes(fetch_bytes(COMPONENTS_URL, timeout=args.timeout, retries=args.retries))
        lines = normalize_lines(html)
        rows = parse_rows(lines, fetched_at)
        if len(rows) < 200:
            raise ValueError(f"Too few constituent rows parsed: {len(rows)}")
        changed = write_text_if_changed(output_path, csv_text(rows, FIELDNAMES))
        status = {
            "ok": True,
            "fetched_at": fetched_at,
            "rows": len(rows),
            "changed": changed,
            "as_of_date": rows[0].get("as_of_date", "") if rows else "",
            "source": COMPONENTS_URL,
            "output": str(output_path.relative_to(repo_root)),
        }
        write_status(status_path, status)
        log(f"nikkei225 constituents rows={len(rows)} changed={changed}")
        return 0
    except Exception as exc:  # noqa: BLE001
        write_status(status_path, {"ok": False, "fetched_at": fetched_at, "error": str(exc), "source": COMPONENTS_URL})
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
