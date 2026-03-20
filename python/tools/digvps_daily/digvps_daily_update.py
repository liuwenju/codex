#!/usr/bin/env python3
"""
Fetch updates from https://digvps.com/update-log.

Features:
1. Pull and parse update-log page.
2. Detect newly published or changed entries based on local state.
3. Keep local state to avoid duplicate output or duplicate push.
4. Stay silent when there is no new update.
5. Support first-run notification of the latest page content.
6. Can run once or run daily at a fixed time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python < 3.9
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

URL = "https://digvps.com/update-log"
USER_AGENT = "Mozilla/5.0 (daily-update-bot/1.0)"
DEFAULT_STATE_FILE = "digvps_state.json"
DEFAULT_OUTPUT_FILE = "digvps_updates.log"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_SERVERCHAN_TITLE_PREFIX = "digvps update"
DEFAULT_REQUEST_TIMEOUT = 20
DEFAULT_FETCH_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 2
DATE_RE = re.compile(r"^\s*(\d{1,2})\u6708(\d{1,2})\u65e5\s*$")
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
FIXED_TZ_FALLBACKS = {
    # Asia/Shanghai is a fixed UTC+8 offset for modern dates, which is enough here.
    "Asia/Shanghai": timezone(timedelta(hours=8), name="Asia/Shanghai"),
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
}


@dataclass
class UpdateEntry:
    entry_date: date
    lines: List[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()

    @property
    def digest(self) -> str:
        payload = f"{self.entry_date.isoformat()}\n{self.text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass
class FetchResult:
    status_code: int
    html: str = ""
    etag: Optional[str] = None
    last_modified: Optional[str] = None


class VisibleTextParser(HTMLParser):
    """Extract visible text and ignore script/style blocks."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: List[str] = []
        self._parts: List[str] = []
        self._all_parts: List[str] = []
        self._capture_marks: List[bool] = []
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in {"script", "style", "noscript"}:
            self._skip_stack.append(tag)
            self._capture_marks.append(False)
            return

        attrs_dict = dict(attrs)
        class_attr = attrs_dict.get("class", "")
        id_attr = attrs_dict.get("id", "")
        class_text = f" {class_attr} "

        should_capture = False
        if tag == "main":
            should_capture = True
        elif tag in {"article", "section", "div"}:
            should_capture = (
                " entry-content " in class_text
                or " post-content " in class_text
                or " article-content " in class_text
                or id_attr in {"content", "main"}
            )

        self._capture_marks.append(should_capture)
        if should_capture:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
        if self._capture_marks:
            mark = self._capture_marks.pop()
            if mark and self._capture_depth > 0:
                self._capture_depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self._all_parts.append(value)
            if self._capture_depth > 0:
                self._parts.append(value)

    @property
    def lines(self) -> List[str]:
        if self._parts:
            return self._parts
        return self._all_parts


def env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "":
        return fallback
    return value


def env_flag(name: str, fallback: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, fallback: int) -> int:
    value = env_default(name)
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def get_timezone(name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass

    fallback = FIXED_TZ_FALLBACKS.get(name)
    if fallback is not None:
        return fallback

    if ZoneInfo is None:
        raise RuntimeError(
            "Current Python does not support zoneinfo (requires Python 3.9+)."
        )

    raise RuntimeError(
        f"Timezone '{name}' is unavailable. Install the 'tzdata' package "
        "or pass a timezone supported by your system."
    )


def fetch_page(
    url: str,
    *,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    retries: int = DEFAULT_FETCH_RETRIES,
    retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FetchResult:
    retry_count = max(0, retries)
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    for attempt in range(retry_count + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                return FetchResult(
                    status_code=getattr(response, "status", 200),
                    html=response.read().decode(encoding, errors="replace"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except HTTPError as exc:
            if exc.code == 304:
                return FetchResult(
                    status_code=304,
                    etag=etag,
                    last_modified=last_modified,
                )
            if exc.code in TRANSIENT_HTTP_STATUS and attempt < retry_count:
                time.sleep(retry_backoff_seconds * (2 ** attempt))
                continue
            raise
        except URLError:
            if attempt < retry_count:
                time.sleep(retry_backoff_seconds * (2 ** attempt))
                continue
            raise


def month_day_to_date(month: int, day: int, today: date) -> date:
    candidate = date(today.year, month, day)
    # Handle year boundary: e.g. in January, page may include December logs from previous year.
    if candidate - today > timedelta(days=180):
        return date(today.year - 1, month, day)
    return candidate


def parse_update_entries(html: str, today: date) -> List[UpdateEntry]:
    parser = VisibleTextParser()
    parser.feed(html)
    lines = parser.lines

    entries: List[UpdateEntry] = []
    current_date: Optional[date] = None
    current_lines: List[str] = []

    for line in lines:
        m = DATE_RE.match(line)
        if m:
            if current_date and current_lines:
                entries.append(UpdateEntry(entry_date=current_date, lines=current_lines))
            month, day = int(m.group(1)), int(m.group(2))
            try:
                current_date = month_day_to_date(month, day, today)
            except ValueError:
                current_date = None
            current_lines = []
            continue

        if current_date:
            current_lines.append(line)

    if current_date and current_lines:
        entries.append(UpdateEntry(entry_date=current_date, lines=current_lines))

    return entries


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_hashes": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("seen_hashes"), list):
                data["seen_hashes"] = [
                    item for item in data["seen_hashes"] if isinstance(item, str)
                ]
                return data
    except Exception:
        pass
    return {"seen_hashes": []}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{path.name}.tmp"
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def get_iso_datetime(state: dict, key: str) -> Optional[datetime]:
    value = state.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def should_skip_fetch(state: dict, now: datetime, min_interval_seconds: int) -> bool:
    if min_interval_seconds <= 0:
        return False
    last_request_at = get_iso_datetime(state, "last_request_at")
    if last_request_at is None:
        return False
    if last_request_at.tzinfo is None:
        last_request_at = last_request_at.replace(tzinfo=now.tzinfo)
    return (now - last_request_at).total_seconds() < min_interval_seconds


def merge_seen_hashes(existing: Iterable[str], entries: Iterable[UpdateEntry]) -> List[str]:
    merged = list(existing)
    known = set(merged)
    for item in entries:
        if item.digest not in known:
            merged.append(item.digest)
            known.add(item.digest)
    return merged


def latest_entries(entries: List[UpdateEntry]) -> List[UpdateEntry]:
    if not entries:
        return []
    latest_date = max(item.entry_date for item in entries)
    return [item for item in entries if item.entry_date == latest_date]


def format_stdout_entries(entries: Iterable[UpdateEntry]) -> str:
    chunks: List[str] = []
    for item in entries:
        chunks.append(f"[{item.entry_date.isoformat()}]\n{item.text}")
    return "\n\n".join(chunks).strip()


def append_output(path: Path, entries: Iterable[UpdateEntry], tz_name: str) -> None:
    now_str = datetime.now(get_timezone(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    chunks: List[str] = [f"\n===== {now_str} =====\n"]
    for item in entries:
        chunks.append(f"[{item.entry_date.isoformat()}]\n{item.text}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(chunks).rstrip() + "\n")


def build_serverchan_title(entries: List[UpdateEntry], title_prefix: str) -> str:
    if len(entries) == 1:
        return f"{title_prefix} {entries[0].entry_date.isoformat()}"
    return f"{title_prefix} {len(entries)} items"


def build_serverchan_body(entries: List[UpdateEntry]) -> str:
    chunks: List[str] = []
    for item in entries:
        chunks.append(f"### {item.entry_date.isoformat()}\n{item.text}")
    chunks.append(f"[View source]({URL})")
    return "\n\n".join(chunks)


def get_serverchan_endpoint(sendkey: str) -> str:
    if sendkey.startswith("sctp"):
        return f"https://{sendkey}.push.ft07.com/send"
    return f"https://sctapi.ftqq.com/{sendkey}.send"


def send_serverchan(
    sendkey: str,
    title: str,
    desp: str,
    *,
    channel: Optional[str] = None,
    tags: Optional[str] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict:
    payload = {
        "title": title,
        "desp": desp,
    }
    if channel:
        payload["channel"] = channel
    if tags:
        payload["tags"] = tags

    req = Request(
        get_serverchan_endpoint(sendkey),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    with urlopen(req, timeout=timeout) as response:
        encoding = response.headers.get_content_charset() or "utf-8"
        raw = response.read().decode(encoding, errors="replace")

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ServerChan returned non-JSON response: {raw[:200]}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("ServerChan returned an unexpected response.")

    if result.get("code") != 0:
        raise RuntimeError(
            f"ServerChan push failed: {result.get('message', 'unknown error')}"
        )

    return result


def check_once(
    state_path: Path,
    output_path: Path,
    tz_name: str,
    *,
    min_check_interval_seconds: int = 0,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    fetch_retries: int = DEFAULT_FETCH_RETRIES,
    retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    notify_on_first_run: bool = False,
    serverchan_sendkey: Optional[str] = None,
    serverchan_title_prefix: str = DEFAULT_SERVERCHAN_TITLE_PREFIX,
    serverchan_channel: Optional[str] = None,
    serverchan_tags: Optional[str] = None,
) -> int:
    tz = get_timezone(tz_name)
    now = datetime.now(tz)
    today = now.date()
    state = load_state(state_path)

    if should_skip_fetch(state, now, min_check_interval_seconds):
        return 0

    state["last_request_at"] = now.isoformat()
    save_state(state_path, state)

    try:
        fetch_result = fetch_page(
            URL,
            timeout=request_timeout,
            retries=fetch_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            etag=state.get("etag"),
            last_modified=state.get("last_modified"),
        )
    except HTTPError as e:
        print(f"[ERROR] Failed to fetch page: HTTP {e.code}", file=sys.stderr)
        return 2
    except URLError as e:
        print(f"[ERROR] Failed to fetch page: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[ERROR] Unexpected fetch error: {e}", file=sys.stderr)
        return 2

    if fetch_result.etag:
        state["etag"] = fetch_result.etag
    if fetch_result.last_modified:
        state["last_modified"] = fetch_result.last_modified
    state["last_success_at"] = datetime.now(tz).isoformat()

    if fetch_result.status_code == 304:
        save_state(state_path, state)
        return 0

    entries = parse_update_entries(fetch_result.html, today=today)
    if not entries:
        save_state(state_path, state)
        return 0

    seen_hashes = state.get("seen_hashes", [])
    seen = set(seen_hashes)

    if not seen_hashes:
        if notify_on_first_run:
            new_entries = latest_entries(entries)
        else:
            state["seen_hashes"] = merge_seen_hashes([], entries)
            save_state(state_path, state)
            return 0
    else:
        new_entries = [item for item in entries if item.digest not in seen]

    if not seen_hashes:
        state["seen_hashes"] = merge_seen_hashes([], entries)
    else:
        state["seen_hashes"] = merge_seen_hashes(seen_hashes, new_entries)

    stdout_text = format_stdout_entries(new_entries)
    if not stdout_text:
        save_state(state_path, state)
        return 0

    print(stdout_text)

    if serverchan_sendkey:
        try:
            send_serverchan(
                serverchan_sendkey,
                build_serverchan_title(new_entries, serverchan_title_prefix),
                build_serverchan_body(new_entries),
                channel=serverchan_channel,
                tags=serverchan_tags,
                timeout=request_timeout,
            )
        except Exception as exc:
            print(f"[ERROR] Failed to push via ServerChan: {exc}", file=sys.stderr)
            return 3

    append_output(output_path, new_entries, tz_name)
    save_state(state_path, state)
    return 0


def parse_hhmm(value: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{1,2})$", value.strip())
    if not m:
        raise ValueError("time format should be HH:MM")
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("hour must be 0-23 and minute 0-59")
    return hh, mm


def seconds_until_next_run(tz_name: str, hhmm: str) -> int:
    tz = get_timezone(tz_name)
    hour, minute = parse_hhmm(hhmm)
    now = datetime.now(tz)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at = run_at + timedelta(days=1)
    return int((run_at - now).total_seconds())


def run_daily(
    state_path: Path,
    output_path: Path,
    tz_name: str,
    at: str,
    *,
    min_check_interval_seconds: int = 0,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    fetch_retries: int = DEFAULT_FETCH_RETRIES,
    retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    notify_on_first_run: bool = False,
    serverchan_sendkey: Optional[str] = None,
    serverchan_title_prefix: str = DEFAULT_SERVERCHAN_TITLE_PREFIX,
    serverchan_channel: Optional[str] = None,
    serverchan_tags: Optional[str] = None,
) -> int:
    while True:
        wait_seconds = seconds_until_next_run(tz_name, at)
        print(f"Next run in {wait_seconds} seconds (timezone={tz_name}, at={at}).")
        time.sleep(wait_seconds)
        rc = check_once(
            state_path=state_path,
            output_path=output_path,
            tz_name=tz_name,
            min_check_interval_seconds=min_check_interval_seconds,
            request_timeout=request_timeout,
            fetch_retries=fetch_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            notify_on_first_run=notify_on_first_run,
            serverchan_sendkey=serverchan_sendkey,
            serverchan_title_prefix=serverchan_title_prefix,
            serverchan_channel=serverchan_channel,
            serverchan_tags=serverchan_tags,
        )
        if rc != 0:
            print(f"[WARN] Check finished with non-zero code: {rc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch digvps updates and only output or push newly detected content."
    )
    p.add_argument(
        "--state-file",
        default=env_default("DIGVPS_STATE_FILE", DEFAULT_STATE_FILE),
        help="Path to state JSON file.",
    )
    p.add_argument(
        "--output-file",
        default=env_default("DIGVPS_OUTPUT_FILE", DEFAULT_OUTPUT_FILE),
        help="Path to output log file.",
    )
    p.add_argument(
        "--timezone",
        default=env_default("DIGVPS_TIMEZONE", DEFAULT_TIMEZONE),
        help="Timezone name, default Asia/Shanghai.",
    )
    p.add_argument(
        "--min-check-interval-seconds",
        type=int,
        default=env_int("DIGVPS_MIN_CHECK_INTERVAL_SECONDS", 0),
        help="Skip fetch if the last request happened too recently.",
    )
    p.add_argument(
        "--request-timeout",
        type=int,
        default=env_int("DIGVPS_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT),
        help="HTTP request timeout in seconds.",
    )
    p.add_argument(
        "--fetch-retries",
        type=int,
        default=env_int("DIGVPS_FETCH_RETRIES", DEFAULT_FETCH_RETRIES),
        help="Retry count for transient fetch failures.",
    )
    p.add_argument(
        "--retry-backoff-seconds",
        type=int,
        default=env_int("DIGVPS_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS),
        help="Base backoff in seconds for transient fetch failures.",
    )
    p.add_argument(
        "--notify-on-first-run",
        action="store_true",
        default=env_flag("DIGVPS_NOTIFY_ON_FIRST_RUN", False),
        help="Notify with the latest page content on the first successful run.",
    )
    p.add_argument("--daily-at", default=None, help="Run daily loop at HH:MM, e.g. 09:00.")
    p.add_argument(
        "--serverchan-sendkey",
        default=env_default("SERVERCHAN_SENDKEY"),
        help="Optional ServerChan sendkey used to push newly detected content.",
    )
    p.add_argument(
        "--serverchan-title-prefix",
        default=env_default("SERVERCHAN_TITLE_PREFIX", DEFAULT_SERVERCHAN_TITLE_PREFIX),
        help="Title prefix used for ServerChan notifications.",
    )
    p.add_argument(
        "--serverchan-channel",
        default=env_default("SERVERCHAN_CHANNEL"),
        help="Optional ServerChan channel option.",
    )
    p.add_argument(
        "--serverchan-tags",
        default=env_default("SERVERCHAN_TAGS"),
        help="Optional ServerChan tags option.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    state_path = Path(args.state_file)
    output_path = Path(args.output_file)

    try:
        if args.daily_at:
            return run_daily(
                state_path=state_path,
                output_path=output_path,
                tz_name=args.timezone,
                at=args.daily_at,
                min_check_interval_seconds=args.min_check_interval_seconds,
                request_timeout=args.request_timeout,
                fetch_retries=args.fetch_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
                notify_on_first_run=args.notify_on_first_run,
                serverchan_sendkey=args.serverchan_sendkey,
                serverchan_title_prefix=args.serverchan_title_prefix,
                serverchan_channel=args.serverchan_channel,
                serverchan_tags=args.serverchan_tags,
            )
        return check_once(
            state_path=state_path,
            output_path=output_path,
            tz_name=args.timezone,
            min_check_interval_seconds=args.min_check_interval_seconds,
            request_timeout=args.request_timeout,
            fetch_retries=args.fetch_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
            notify_on_first_run=args.notify_on_first_run,
            serverchan_sendkey=args.serverchan_sendkey,
            serverchan_title_prefix=args.serverchan_title_prefix,
            serverchan_channel=args.serverchan_channel,
            serverchan_tags=args.serverchan_tags,
        )
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

