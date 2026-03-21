#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def fetch_bytes(
    url: str,
    timeout: int = 30,
    retries: int = 3,
    sleep_seconds: float = 0.0,
    headers: dict[str, str] | None = None,
) -> bytes:
    last_error: Exception | None = None
    request_headers = dict(DEFAULT_HEADERS)
    if headers:
        request_headers.update(headers)

    for attempt in range(1, retries + 1):
        try:
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            request = Request(url, headers=request_headers)
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            log(f"download failed (attempt {attempt}/{retries}) for {url}: {exc}")
            if attempt < retries:
                time.sleep(min(2**attempt, 10))

    raise RuntimeError(f"download failed after {retries} attempts: {last_error}")


def decode_bytes(blob: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp", "latin-1"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", blob, 0, 1, "Unable to decode content")


def write_text_if_changed(path: Path, content: str, encoding: str = "utf-8") -> bool:
    old = None
    if path.exists():
        old = path.read_text(encoding=encoding)
    if old == content:
        return False
    ensure_dir(path.parent)
    path.write_text(content, encoding=encoding)
    return True


def write_status(path: Path, status: dict[str, object]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def csv_text(rows: Iterable[dict[str, object]], fieldnames: list[str]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()
