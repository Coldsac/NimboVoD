#!/usr/bin/env python3
"""NimboVoD local cleaner.

Build a .strm folder from an M3U playlist, compare it with an Emby library,
and delete only the .strm files from this playlist that Emby already has.
An existing .strm file is rewritten only when its address differs from the plan.

Nothing is written or deleted unless you pass --apply.
A home-network Emby address is fine here; this runs on your machine.

Examples:
  nimbovod-cleaner.exe
  nimbovod-cleaner.exe --apply
  python3 nimbovod-cleaner.py --config nimbovod.config.json --apply
  python3 nimbovod-cleaner.py --m3u https://example.com/a.m3u --m3u https://example.com/b.m3u --out "D:\\Media\\strm" --apply
  python3 nimbovod-cleaner.py --emby http://192.168.1.20:8096 --api-key KEY --export library.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

QUALITY_TAGS = (
    "2160p",
    "1080p",
    "720p",
    "576p",
    "480p",
    "4k",
    "uhd",
    "hdr10",
    "hdr",
    "dv",
    "dolby vision",
    "web-dl",
    "webrip",
    "bluray",
    "blu-ray",
    "bdrip",
    "brrip",
    "dvdrip",
    "hdtv",
    "hdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "aac",
    "dts-hd",
    "dts",
    "atmos",
    "truehd",
    "remux",
    "proper",
    "repack",
    "extended",
    "unrated",
    "amzn",
    "nf",
    "hmax",
    "[WEB]",
    "[x265]",
    "[x264]",
    "[TS]",
    "Megusta",
    "[ ]",
)
QUALITY = re.compile(
    r"\[\s*(?:web|x26[45]|ts)\s*\]"
    r"|\[\s*\]"
    r"|\b(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dv|dolby(?:\s*vision)?|"
    r"web-?dl|web-?rip|bluray|blu-ray|bdrip|brrip|dvdrip|hdtv|hdrip|x264|x265|"
    r"h\.?264|h\.?265|hevc|aac|dts(?:-hd)?|atmos|truehd|remux|proper|repack|"
    r"extended|unrated|amzn|nf|hmax|megusta)\b",
    re.I,
)
EMPTY_BRACKETS = re.compile(r"\[\s*\]")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
EPISODE = re.compile(
    r"^(.*?)\s+(?:s(\d{1,2})\s*e(\d{1,3})|(\d{1,2})x(\d{2,3}))\b\s*(.*)$",
    re.I,
)
ATTR = re.compile(r"""([A-Za-z0-9-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)
PAGE_SIZE = 400
ITEM_LIMIT = 500_000


def compile_extra_tags(tags: object) -> re.Pattern[str] | None:
    parts: list[str] = []
    if isinstance(tags, str):
        parts = re.split(r"[\n,;]+", tags)
    elif isinstance(tags, (list, tuple)):
        for item in tags:
            if isinstance(item, str):
                parts.extend(re.split(r"[\n,;]+", item))
    escaped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = part.strip()
        if len(tag) < 2:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        escaped.append((tag, re.escape(tag).replace(r"\ ", r"\s+")))
    if not escaped:
        return None
    escaped.sort(key=lambda item: len(item[0]), reverse=True)
    body = "|".join(token for _tag, token in escaped)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.I)


def strip_quality(value: str, extra: re.Pattern[str] | None = None) -> str:
    text = QUALITY.sub(" ", value)
    if extra is not None:
        text = extra.sub(" ", text)
    text = EMPTY_BRACKETS.sub(" ", text)
    return re.sub(r"\s+", " ", text.replace(".", " ").replace("_", " ")).strip()


def normalize_title(value: str, extra: re.Pattern[str] | None = None) -> tuple[str, int | None]:
    years = [int(match.group(0)) for match in YEAR.finditer(value)]
    year = years[-1] if years else None
    text = value
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = QUALITY.sub(" ", text)
    if extra is not None:
        text = extra.sub(" ", text)
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = YEAR.sub(" ", text)
    text = re.sub(r"^(?:the|a|an)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text and year is not None:
        text = str(year)
    return text, year


def parse_episode(title: str, extra: re.Pattern[str] | None = None) -> tuple[str, int, int, str] | None:
    match = EPISODE.match(strip_quality(title, extra))
    if not match:
        return None
    show = (match.group(1) or "").strip()
    season = int(match.group(2) or match.group(4))
    episode = int(match.group(3) or match.group(5))
    rest = re.sub(r"^[-–—:\s]+", "", (match.group(6) or "").strip())
    if not show:
        return None
    return show, season, episode, rest


def parse_m3u(source: str) -> list[dict]:
    entries: list[dict] = []
    pending: dict | None = None
    ext_group = ""
    for raw in source.replace("\ufeff", "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.upper().startswith("#EXTGRP:"):
                ext_group = line.split(":", 1)[1].strip()
            elif line.upper().startswith("#EXTINF:"):
                body = re.sub(r"^#EXTINF:", "", line, count=1, flags=re.I)
                quoted = False
                comma = -1
                for index, char in enumerate(body):
                    if char == '"':
                        quoted = not quoted
                    elif char == "," and not quoted:
                        comma = index
                        break
                meta = body if comma < 0 else body[:comma]
                title = "" if comma < 0 else body[comma + 1 :].strip()
                attrs: dict[str, str] = {}
                for match in ATTR.finditer(meta):
                    key = match.group(1).lower()
                    attrs.setdefault(key, match.group(2) if match.group(2) is not None else match.group(3) or "")
                pending = {
                    "title": title or attrs.get("tvg-name") or "Untitled",
                    "group": attrs.get("group-title") or ext_group or "Ungrouped",
                    "url": "",
                }
            continue
        if pending is not None:
            pending["url"] = line
            entries.append(pending)
            pending = None
    return entries


def safe_segment(name: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")[:150] or "untitled"
    if RESERVED.match(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def relative_path(entry: dict, layout: str, strip: bool, extra: re.Pattern[str] | None = None) -> str:
    title = strip_quality(entry["title"], extra) if strip else re.sub(r"\s+", " ", entry["title"]).strip()
    title = title or entry["title"] or "Untitled"
    if layout == "flat":
        return f"{safe_segment(title)}.strm"
    if layout == "library":
        episode = parse_episode(title, extra)
        if episode:
            show, season, number, rest = episode
            label = (
                f"{show} - S{season:02d}E{number:02d} - {rest}"
                if rest
                else f"{show} - S{season:02d}E{number:02d}"
            )
            return f"TV/{safe_segment(show)}/Season {season:02d}/{safe_segment(label)}.strm"
        return f"Movies/{safe_segment(title)}.strm"
    return f"{safe_segment(entry.get('group') or 'Ungrouped')}/{safe_segment(title)}.strm"


def unique_path(path: str, used: set[str]) -> str:
    if path.lower() not in used:
        used.add(path.lower())
        return path
    dot = path.rfind(".")
    stem = path if dot < 0 else path[:dot]
    ext = "" if dot < 0 else path[dot:]
    number = 2
    while True:
        candidate = f"{stem} ({number}){ext}"
        if candidate.lower() not in used:
            used.add(candidate.lower())
            return candidate
        number += 1


def years_ok(left: int | None, right: int | None, use_years: bool) -> bool:
    if not use_years or left is None or right is None:
        return True
    return left == right


def remember(index: dict[str, list[dict]], key: str, label: str, year: int | None) -> None:
    if not key:
        return
    index.setdefault(key, []).append({"label": label, "year": year})


def find_match(index: dict[str, list[dict]], key: str, year: int | None, use_years: bool) -> dict | None:
    for item in index.get(key, []):
        if years_ok(year, item["year"], use_years):
            return item
    return None


def is_strm(item: dict) -> bool:
    path = item.get("path") or item.get("Path") or ""
    return isinstance(path, str) and path.lower().endswith(".strm")


def show_keys(show: str, extra: re.Pattern[str] | None = None) -> list[str]:
    """Names to try, most specific first. A subtitle after a colon is tried second."""
    keys: list[str] = []

    def add(value: str) -> None:
        key, _year = normalize_title(value, extra)
        if key and key not in keys:
            keys.append(key)

    add(show)
    head = re.split(r"\s*[:|]\s*", show, maxsplit=1)[0].strip()
    if head and head != show:
        add(head)
    trimmed = re.sub(r"\s+season\s+\d{1,2}\s*$", "", show, flags=re.I).strip()
    if trimmed and trimmed != show:
        add(trimmed)
    return keys


def owned_show_key(
    show: str,
    series: dict,
    seasons: dict[str, set[int]],
    extra: re.Pattern[str] | None = None,
) -> str:
    keys = show_keys(show, extra)
    for key in keys:
        if key in series or key in seasons:
            return key
    return keys[0] if keys else ""


def index_library(
    items: list[dict],
    match_filenames: bool,
    extra: re.Pattern[str] | None = None,
) -> tuple[dict, dict, dict, dict, dict[str, set[int]]]:
    movies: dict[str, list[dict]] = {}
    series: dict[str, list[dict]] = {}
    episodes: dict[str, list[dict]] = {}
    filenames: dict[str, list[dict]] = {}
    seasons: dict[str, set[int]] = {}
    for item in items:
        # .strm files are this program's own copies. They are not the library.
        if is_strm(item):
            continue
        name = str(item.get("name") or item.get("Name") or "").strip()
        if not name:
            continue
        kind = str(item.get("type") or item.get("Type") or "Other")
        named_key, named_year = normalize_title(name, extra)
        year = item.get("year", item.get("ProductionYear", named_year))
        year = int(year) if isinstance(year, int) or (isinstance(year, str) and year.isdigit()) else named_year
        label = f"{kind} \"{name}\"" + (f" ({year})" if year else "")
        if kind == "Movie":
            remember(movies, named_key, label, year)
        elif kind == "Series":
            remember(series, named_key, label, year)
        elif kind == "Episode":
            show_name = str(item.get("seriesName") or item.get("SeriesName") or "")
            show_key, _show_year = normalize_title(show_name, extra)
            season = item.get("season", item.get("ParentIndexNumber"))
            episode = item.get("episode", item.get("IndexNumber"))
            if show_key and isinstance(season, int) and isinstance(episode, int):
                remember(episodes, f"{show_key}|{season}|{episode}", label, year)
                seasons.setdefault(show_key, set()).add(season)
        path = item.get("path") or item.get("Path")
        if match_filenames and isinstance(path, str) and path.strip():
            base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", path.replace("\\", "/").split("/")[-1])
            file_key, file_year = normalize_title(base, extra)
            remember(filenames, file_key, label, year if isinstance(year, int) else file_year)
    return movies, series, episodes, filenames, seasons


def disk_duplicate_rows(folder: Path, planned: list[dict]) -> list[dict]:
    if not directory_reachable(folder):
        return []
    known = {row["path"].replace("\\", "/").lower() for row in planned}
    kept_names: dict[str, set[str]] = {}
    for row in planned:
        if row["disposition"] != "keep":
            continue
        relative = Path(row["path"].replace("\\", "/"))
        kept_names.setdefault(relative.parent.as_posix(), set()).add(relative.name.lower())
    found: list[dict] = []
    numbered = re.compile(r"^(?P<stem>.+) \((?P<number>\d+)\)(?P<suffix>\.[^.]+)$", re.I)
    for parent_key, names in kept_names.items():
        parent = folder if parent_key == "." else folder / parent_key
        try:
            if not parent.is_dir():
                continue
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            match = numbered.match(child.name)
            if match is None or int(match.group("number")) < 2:
                continue
            try:
                if not child.is_file():
                    continue
            except OSError:
                continue
            plain = f"{match.group('stem')}{match.group('suffix')}"
            if plain.lower() not in names:
                continue
            sibling = child.name if parent_key == "." else f"{parent_key}/{child.name}"
            if sibling.lower() in known:
                continue
            known.add(sibling.lower())
            found.append(
                {
                    "title": child.stem,
                    "url": "",
                    "path": sibling,
                    "group": "",
                    "disposition": "drop",
                    "reason": "Duplicate .strm file already in the folder",
                }
            )
    return found


def plan_entries(entries: list[dict], library: list[dict] | None, options: dict) -> list[dict]:
    extra = compile_extra_tags(options.get("extra_tags"))
    drop_duplicate_strm = bool(options.get("drop_duplicate_strm", True))
    movies, series, episodes, filenames, seasons = (
        index_library(library, options["match_filenames"], extra)
        if library is not None
        else ({}, {}, {}, {}, {})
    )
    used: set[str] = set()
    seen: set[str] = set()
    planned: list[dict] = []
    for entry in entries:
        natural = relative_path(entry, options["layout"], options["strip_quality"], extra)
        path = natural if drop_duplicate_strm else unique_path(natural, used)
        url = entry["url"].strip()
        row = {"title": entry["title"], "url": url, "path": path, "disposition": "keep", "reason": ""}
        if url in seen:
            row["disposition"] = "drop"
            row["reason"] = "Duplicate stream URL in the playlist"
            planned.append(row)
            continue
        seen.add(url)
        if drop_duplicate_strm:
            key = natural.lower()
            if key in used:
                row["disposition"] = "drop"
                row["reason"] = "Duplicate .strm file"
                planned.append(row)
                continue
            used.add(key)
        if library is None:
            planned.append(row)
            continue
        cleaned = strip_quality(entry["title"], extra) if options["strip_quality"] else entry["title"]
        episode = parse_episode(cleaned, extra)
        reason = ""
        if episode:
            show, season, number, _rest = episode
            show_key = owned_show_key(show, series, seasons, extra)
            hit = (episodes.get(f"{show_key}|{season}|{number}") or [None])[0]
            if hit:
                reason = f"Already in Emby · {hit['label']}"
            elif season in seasons.get(show_key, set()):
                # One episode of a season means that season is already in the library.
                label = (series.get(show_key) or [{"label": show}])[0]["label"]
                reason = f"Already in Emby · Season {season} · {label}"
        else:
            key, year = normalize_title(cleaned, extra)
            hit = (
                find_match(movies, key, year, options["use_years"])
                or find_match(series, key, year, options["use_years"])
                or (
                    find_match(filenames, key, year, options["use_years"])
                    if options["match_filenames"]
                    else None
                )
            )
            if hit:
                reason = f"Already in Emby · {hit['label']}"
        if reason:
            row["disposition"] = "drop"
            row["reason"] = reason
        planned.append(row)
    if drop_duplicate_strm:
        raw_out = str(options.get("out") or "").strip()
        if raw_out:
            planned.extend(disk_duplicate_rows(Path(raw_out), planned))
    return planned


def name_resolves(host: str, timeout: float = 3.0) -> bool:
    """Name lookup with a limit. urllib's timeout does not cover this wait."""
    if not host.strip():
        return False
    done = threading.Event()
    outcome: dict[str, bool] = {}

    def work() -> None:
        try:
            socket.getaddrinfo(host, None)
            outcome["ok"] = True
        except OSError:
            outcome["ok"] = False
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    if not done.wait(timeout):
        return False
    return bool(outcome.get("ok"))


def read_response(
    request: urllib.request.Request,
    timeout: int = 20,
    limit_seconds: int = 120,
    max_bytes: int = 80 * 1024 * 1024,
    on_progress=None,
) -> bytes:
    """Read a response. A stalled connection ends, and progress can be reported."""
    host = urllib.parse.urlparse(request.full_url).hostname or ""
    if host and not name_resolves(host, 3):
        raise SystemExit("The connection took too long.")
    outcome: dict = {}

    def work() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                chunks: list[bytes] = []
                total = 0
                started = time.time()
                while True:
                    if time.time() - started > limit_seconds:
                        raise SystemExit("The connection took too long.")
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > max_bytes:
                        raise SystemExit("The download is over 80 MB.")
                    chunks.append(block)
                    if on_progress is not None:
                        on_progress(total)
                outcome["body"] = b"".join(chunks)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(limit_seconds + 5)
    if thread.is_alive():
        raise SystemExit("The connection took too long.")
    if "error" in outcome:
        raise outcome["error"]
    body = outcome.get("body")
    if not isinstance(body, bytes):
        raise SystemExit("The connection took too long.")
    return body


def directory_reachable(path: Path, timeout: float = 2.0) -> bool:
    """Return whether a folder answers quickly. A disconnected drive does not."""
    if not str(path).strip():
        return False
    outcome: dict[str, bool] = {}

    def check() -> None:
        try:
            outcome["ok"] = path.is_dir()
        except OSError:
            outcome["ok"] = False

    thread = threading.Thread(target=check, daemon=True)
    thread.start()
    thread.join(timeout)
    return bool(outcome.get("ok"))


def read_json(url: str, api_key: str):
    request = urllib.request.Request(
        url,
        headers={"X-Emby-Token": api_key, "Accept": "application/json"},
    )
    payload = json.loads(read_response(request).decode("utf-8"))
    if not isinstance(payload, (dict, list)):
        raise SystemExit("Emby did not return a library list.")
    return payload


def request_json(url: str, api_key: str) -> dict:
    payload = read_json(url, api_key)
    if not isinstance(payload, dict):
        raise SystemExit("Emby did not return a library list.")
    return payload


def emby_origin(base_url: str) -> str:
    origin = base_url.rstrip("/")
    origin = re.sub(r"/web(/index\.html?)?$", "", origin, flags=re.I)
    origin = re.sub(r"/emby$", "", origin, flags=re.I)
    return origin


def library_rows(payload: object) -> list[dict]:
    raw_items: list = []
    if isinstance(payload, dict):
        items = payload.get("Items")
        if isinstance(items, list):
            raw_items = items
    elif isinstance(payload, list):
        raw_items = payload
    found: list[dict] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        item_id = str(item.get("Id") or item.get("ItemId") or "").strip()
        if not name or not item_id or item_id in seen:
            continue
        seen.add(item_id)
        kind = item.get("CollectionType")
        found.append({"id": item_id, "name": name, "type": kind if isinstance(kind, str) else ""})
    found.sort(key=lambda row: row["name"].lower())
    return found


def list_emby_libraries(base_url: str, api_key: str) -> list[dict]:
    origin = emby_origin(base_url)
    last_error = "Emby could not be reached."
    for prefix in ("/emby", ""):
        try:
            payload = read_json(f"{origin}{prefix}/Library/MediaFolders", api_key)
            rows = library_rows(payload)
            if rows:
                return rows
            last_error = "Emby did not return any libraries."
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise SystemExit("Emby refused that API key.") from error
            last_error = f"Emby responded with {error.code}."
        except RuntimeError as error:
            last_error = str(error)
        except urllib.error.URLError as error:
            raise SystemExit(f"Could not reach Emby: {error.reason}") from error
    raise SystemExit(last_error)


def fetch_emby(
    base_url: str,
    api_key: str,
    user_id: str,
    on_progress=None,
    libraries: list[dict] | None = None,
) -> list[dict]:
    origin = emby_origin(base_url)
    user_path = f"/Users/{urllib.parse.quote(user_id)}/Items" if user_id else "/Items"
    chosen: list[dict] = []
    for item in libraries or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        chosen.append({"id": item_id, "name": str(item.get("name") or item_id).strip()})
    targets = chosen or [{"id": "", "name": ""}]
    last_error = "Emby could not be reached."
    for prefix in ("/emby", ""):
        try:
            items: list[dict] = []
            seen: set[str] = set()
            reported = 0.0

            def report(done: int, total_count: int | None, label: str, force: bool = False) -> None:
                nonlocal reported
                if on_progress is None:
                    return
                now = time.time()
                if not force and now - reported < 0.25:
                    return
                reported = now
                on_progress(done, total_count, label or None)

            for target in targets:
                if len(items) >= ITEM_LIMIT:
                    break
                start = 0
                total = None
                fetched = 0
                while len(items) < ITEM_LIMIT:
                    params = {
                        "Recursive": "true",
                        "IncludeItemTypes": "Movie,Series,Episode",
                        "Fields": "Path,ProductionYear",
                        "StartIndex": start,
                        "Limit": PAGE_SIZE,
                        "EnableImages": "false",
                        "EnableUserData": "false",
                    }
                    if target["id"]:
                        params["ParentId"] = target["id"]
                    query = urllib.parse.urlencode(params)
                    payload = request_json(f"{origin}{prefix}{user_path}?{query}", api_key)
                    batch = payload.get("Items")
                    if not isinstance(batch, list):
                        raise RuntimeError("Emby did not return a library list.")
                    for raw in batch:
                        if not isinstance(raw, dict) or not raw.get("Name"):
                            continue
                        item_id = raw.get("Id")
                        if isinstance(item_id, str) and item_id:
                            if item_id in seen:
                                continue
                            seen.add(item_id)
                        kind = raw.get("Type") if raw.get("Type") in {"Movie", "Series", "Episode"} else "Other"
                        items.append(
                            {
                                "name": raw.get("Name"),
                                "year": raw.get("ProductionYear"),
                                "type": kind,
                                "path": raw.get("Path"),
                                "seriesName": raw.get("SeriesName"),
                                "season": raw.get("ParentIndexNumber"),
                                "episode": raw.get("IndexNumber"),
                            }
                        )
                        fetched += 1
                    start += len(batch)
                    if isinstance(payload.get("TotalRecordCount"), int):
                        total = payload["TotalRecordCount"]
                    finished = (
                        len(batch) < PAGE_SIZE
                        or (total is not None and start >= total)
                        or len(items) >= ITEM_LIMIT
                    )
                    report(fetched if target["id"] else len(items), total, target["name"], force=finished)
                    if finished:
                        break
            return items
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise SystemExit("Emby refused that API key.") from error
            last_error = f"Emby responded with {error.code}."
        except RuntimeError as error:
            last_error = str(error)
        except urllib.error.URLError as error:
            raise SystemExit(f"Could not reach Emby: {error.reason}") from error
    raise SystemExit(last_error)


def fetch_text(url: str, on_progress=None) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("The playlist URL must start with http or https.")
    request = urllib.request.Request(url, headers={"User-Agent": "NimboVoD/1.0"})
    return read_response(request, on_progress=on_progress).decode("utf-8", errors="replace")


def inside(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise SystemExit(f"Refusing an unsafe path: {relative}")
    root = root.resolve()
    target = (root / relative).resolve()
    target.relative_to(root)
    return target


def strm_text(path: Path) -> str | None:
    """Stripped text of an existing .strm file. None when that file is absent."""
    try:
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(8192)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def write_plan(out: Path, planned: list[dict], apply: bool, on_progress=None) -> dict[str, int]:
    """Add missing .strm files, replace changed addresses, and delete planned drops.

    A file whose text already matches the planned URL is left untouched. A path
    that a keep row writes is never deleted. Files that are not in the plan are
    not scanned and not removed. apply=False counts the same actions and writes
    nothing. on_progress, when set, is called as on_progress(done, total).
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}
    kept_paths = {row["path"].replace("\\", "/").lower() for row in planned if row["disposition"] == "keep"}
    total = len(planned)
    reported = 0.0

    def report(done: int) -> None:
        nonlocal reported
        if on_progress is None or total == 0:
            return
        now = time.time()
        if done != total and now - reported < 0.25:
            return
        reported = now
        on_progress(done, total)

    for index, row in enumerate(planned, start=1):
        if row["disposition"] != "keep" and row["path"].replace("\\", "/").lower() in kept_paths:
            report(index)
            continue
        target = inside(out, row["path"])
        if row["disposition"] == "keep":
            wanted = str(row.get("url", "")).strip()
            current = strm_text(target)
            if current is None:
                counts["added"] += 1
                if apply:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(wanted + "\n", encoding="utf-8", newline="\n")
            elif current == wanted:
                counts["unchanged"] += 1
            else:
                counts["updated"] += 1
                if apply:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(wanted + "\n", encoding="utf-8", newline="\n")
        elif target.is_file():
            counts["removed"] += 1
            if apply:
                target.unlink()
            print(f"{'deleted' if apply else 'would delete'}  {row['path']}  ({row['reason']})")
        report(index)
    kept = sum(1 for row in planned if row["disposition"] == "keep")
    dropped = len(planned) - kept
    print("")
    print(f"Playlist entries: {len(planned)}")
    print(f"Already in Emby or duplicate: {dropped}")
    print(f".strm files to keep: {kept}")
    print(f"Already matching: {counts['unchanged']}")
    print(f"New .strm files: {counts['added']}")
    print(f"Replaced because the address changed: {counts['updated']}")
    print(f"Existing duplicate files {'removed' if apply else 'that would be removed'}: {counts['removed']}")
    if not apply:
        if os.environ.get("NIMBOVOD_HOME"):
            print("Dry run only. Nothing was written. When the plan looks right, run: nimbovod-cleaner.exe --apply")
        else:
            print("Dry run only. Pass --apply to write the new files and remove duplicates already in the folder.")
    else:
        print(
            f"Added {counts['added']}, updated {counts['updated']}, "
            f"left {counts['unchanged']} unchanged, removed {counts['removed']} under {out}"
        )
    return counts


def load_config(path: str | None) -> dict:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Config file must be a JSON object.")
    return payload


def implicit_config() -> str | None:
    candidates: list[Path] = []
    home = os.environ.get("NIMBOVOD_HOME", "").strip()
    if home:
        candidates.append(Path(home) / "nimbovod.config.json")
    candidates.append(Path.cwd() / "nimbovod.config.json")
    candidates.append(Path(sys.argv[0]).resolve().parent / "nimbovod.config.json")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return str(path)
    return None


def missing_config_message() -> str:
    if os.environ.get("NIMBOVOD_HOME"):
        return (
            "No nimbovod.config.json found next to nimbovod-cleaner.exe.\n"
            "\n"
            "1. On the NimboVoD page, turn on \"Put the API key in the downloaded config\".\n"
            "2. Download Config and this program.\n"
            "3. Put nimbovod.config.json in the same folder as nimbovod-cleaner.exe.\n"
            "4. Double-click nimbovod-cleaner.exe for a dry run.\n"
            "\n"
            "A 192.168 Emby address works here. To write the .strm files:\n"
            "  nimbovod-cleaner.exe --apply"
        )
    return "Provide at least one --m3u and --out, or a --config file with those fields."


def pause_if_needed() -> None:
    if os.environ.get("NIMBOVOD_PAUSE") != "1":
        return
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


def source_list(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                found.append(item.strip())
        return found
    return []


def load_playlist_entries(sources: list[str]) -> list[dict]:
    entries: list[dict] = []
    for source in sources:
        print(f"Playlist: {source}")
        if source.startswith("http://") or source.startswith("https://"):
            text = fetch_text(source)
        else:
            text = Path(source).read_text(encoding="utf-8", errors="replace")
        found = parse_m3u(text)
        if not found:
            print(f"No streams in {source}")
            continue
        entries.extend(found)
    return entries


def self_test() -> None:
    assert normalize_title("The Matrix (1999) 1080p") == ("matrix", 1999)
    assert normalize_title("1917") == ("1917", 1917)
    assert parse_episode("The Office S01E03 Diversity Day") == ("The Office", 1, 3, "Diversity Day")
    entries = parse_m3u(
        '#EXTM3U\n#EXTINF:-1 group-title="Movies",Dune (1984)\nhttps://example.com/dune\n'
        '#EXTINF:-1 group-title="Movies",The Matrix (1999)\nhttps://example.com/matrix\n'
    )
    library = [
        {"name": "The Matrix", "year": 1999, "type": "Movie"},
        {"name": "Dune", "year": 2021, "type": "Movie"},
    ]
    planned = plan_entries(
        entries,
        library,
        {"layout": "group", "strip_quality": True, "use_years": True, "match_filenames": True},
    )
    by_title = {row["title"]: row["disposition"] for row in planned}
    assert by_title["Dune (1984)"] == "keep", by_title
    assert by_title["The Matrix (1999)"] == "drop", by_title
    assert source_list("https://example.com/a.m3u") == ["https://example.com/a.m3u"]
    assert source_list([" https://a ", "", "https://b"]) == ["https://a", "https://b"]
    extra = compile_extra_tags("YTS, RARBG")
    assert strip_quality("The.Matrix.YTS.1080p", extra) == "The Matrix"
    tagged = parse_m3u(
        '#EXTM3U\n#EXTINF:-1 group-title="Movies",The Matrix YTS 1080p\nhttps://example.com/yts\n'
    )
    tagged_plan = plan_entries(
        tagged,
        [{"name": "The Matrix", "year": 1999, "type": "Movie"}],
        {
            "layout": "flat",
            "strip_quality": True,
            "use_years": True,
            "match_filenames": False,
            "extra_tags": ["YTS"],
        },
    )
    assert tagged_plan[0]["path"] == "The Matrix.strm", tagged_plan[0]
    assert tagged_plan[0]["disposition"] == "drop", tagged_plan[0]
    shows = parse_m3u(
        "#EXTM3U\n"
        '#EXTINF:-1 group-title="The Office",The Office S01E01\nhttps://example.com/e1\n'
        '#EXTINF:-1 group-title="The Office",The Office S01E03\nhttps://example.com/e3\n'
        '#EXTINF:-1 group-title="The Office",The Office S02E01\nhttps://example.com/e4\n'
        '#EXTINF:-1 group-title="The Agency: Central Intelligence",The Agency: Central Intelligence S01E05\nhttps://example.com/a1\n'
        '#EXTINF:-1 group-title="The Agency: Central Intelligence",The Agency: Central Intelligence S02E01\nhttps://example.com/a2\n'
        '#EXTINF:-1 group-title="The Agency: Unfiltered",The Agency: Unfiltered S02E01\nhttps://example.com/u2\n'
        '#EXTINF:-1 group-title="TV VOD",Brand New Show S01E01\nhttps://example.com/new\n'
    )
    show_plan = plan_entries(
        shows,
        [
            {"name": "The Office", "year": 2005, "type": "Series"},
            {
                "name": "Pilot",
                "year": 2005,
                "type": "Episode",
                "seriesName": "The Office",
                "season": 1,
                "episode": 1,
                "path": "Z:/TV/The Office/Season 1/Pilot.mkv",
            },
            {"name": "The Agency (2024)", "year": 2024, "type": "Series"},
            {
                "name": "The Bends",
                "year": 2024,
                "type": "Episode",
                "seriesName": "The Agency (2024)",
                "season": 1,
                "episode": 1,
                "path": "Z:/TV/The Agency (2024)/Season 1/The Bends.mkv",
            },
            {
                "name": "Kick the Wasp's Nest",
                "year": 2025,
                "type": "Episode",
                "seriesName": "The Agency (2024)",
                "season": 2,
                "episode": 1,
                "path": "D:/NimiboVoD/The Agency Central Intelligence/Season 02/S02E01.strm",
            },
            {"name": "The Agency: Unfiltered", "type": "Series"},
        ],
        {"layout": "library", "strip_quality": True, "use_years": True, "match_filenames": True},
    )
    show_by_title = {row["title"]: row for row in show_plan}
    assert show_by_title["The Office S01E01"]["disposition"] == "drop"
    assert show_by_title["The Office S01E03"]["disposition"] == "drop"
    assert "Season 1" in show_by_title["The Office S01E03"]["reason"]
    assert show_by_title["The Office S02E01"]["disposition"] == "keep"
    assert show_by_title["The Agency: Central Intelligence S01E05"]["disposition"] == "drop"
    assert show_by_title["The Agency: Central Intelligence S02E01"]["disposition"] == "keep"
    assert show_by_title["The Agency: Unfiltered S02E01"]["disposition"] == "keep"
    assert show_by_title["Brand New Show S01E01"]["disposition"] == "keep"
    assert strip_quality("Movie [WEB] [x265] [x264] [TS] Megusta [ ]") == "Movie"
    assert strip_quality("Movie [1080p]") == "Movie"
    assert normalize_title("Movie Megusta")[0] == "movie"
    copies = parse_m3u(
        "#EXTM3U\n"
        '#EXTINF:-1 group-title="Movies",Movie [WEB] Megusta\nhttps://example.com/one\n'
        '#EXTINF:-1 group-title="Movies",Movie [x265]\nhttps://example.com/two\n'
    )
    duplicate_plan = plan_entries(
        copies,
        None,
        {"layout": "flat", "strip_quality": True, "drop_duplicate_strm": True},
    )
    assert duplicate_plan[0]["path"] == "Movie.strm" and duplicate_plan[0]["disposition"] == "keep"
    assert duplicate_plan[1]["disposition"] == "drop" and duplicate_plan[1]["reason"] == "Duplicate .strm file"
    kept_plan = plan_entries(
        copies,
        None,
        {"layout": "flat", "strip_quality": True, "drop_duplicate_strm": False},
    )
    assert kept_plan[1]["path"] == "Movie (2).strm" and kept_plan[1]["disposition"] == "keep"
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder / "Movie (2).strm").write_text("old\n", encoding="utf-8")
        with_disk = plan_entries(
            copies[:1],
            None,
            {"layout": "flat", "strip_quality": True, "drop_duplicate_strm": True, "out": directory},
        )
        assert any(row["path"] == "Movie (2).strm" and row["disposition"] == "drop" for row in with_disk)
        assert directory_reachable(folder)
        assert not directory_reachable(folder / "missing")
        assert name_resolves("localhost", 2)
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        matching = b"https://example.com/a\r\n"
        old_b = b"https://example.com/b-old\n"
        other = b"leave me\n"
        (folder / "A.strm").write_bytes(matching)
        (folder / "B.strm").write_bytes(old_b)
        (folder / "C.strm").write_bytes(b"https://example.com/c\n")
        (folder / "Other.strm").write_bytes(other)
        rows = [
            {"path": "A.strm", "url": "https://example.com/a", "disposition": "keep", "reason": ""},
            {"path": "B.strm", "url": "https://example.com/b-new", "disposition": "keep", "reason": ""},
            {"path": "New.strm", "url": "https://example.com/new", "disposition": "keep", "reason": ""},
            {"path": "Shows/Pilot.strm", "url": "https://example.com/pilot", "disposition": "keep", "reason": ""},
            {"path": "C.strm", "url": "https://example.com/c", "disposition": "drop", "reason": "Already in Emby"},
            {"path": "A.strm", "url": "https://example.com/a-old", "disposition": "drop", "reason": "Duplicate .strm file"},
        ]
        before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        seen: list[tuple[int, int]] = []
        preview = write_plan(folder, rows, False, lambda done, total: seen.append((done, total)))
        assert preview == {"added": 2, "updated": 1, "unchanged": 1, "removed": 1}, preview
        assert seen[-1] == (len(rows), len(rows))
        after_preview = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        assert after_preview == before
        assert not (folder / "Shows").exists()
        applied = write_plan(folder, rows, True)
        assert applied == {"added": 2, "updated": 1, "unchanged": 1, "removed": 1}, applied
        assert (folder / "A.strm").read_bytes() == matching
        assert (folder / "B.strm").read_text(encoding="utf-8").strip() == "https://example.com/b-new"
        assert (folder / "New.strm").read_text(encoding="utf-8").strip() == "https://example.com/new"
        assert (folder / "Shows" / "Pilot.strm").read_text(encoding="utf-8").strip() == "https://example.com/pilot"
        assert not (folder / "C.strm").exists()
        assert (folder / "Other.strm").read_bytes() == other
        again = write_plan(folder, rows, True)
        assert again == {"added": 0, "updated": 0, "unchanged": 4, "removed": 0}, again
        assert (folder / "A.strm").read_bytes() == matching
        assert (folder / "Other.strm").read_bytes() == other
    listed = library_rows(
        {
            "Items": [
                {"Name": "TV shows", "Id": "1", "CollectionType": "tvshows"},
                {"Name": "TV shows", "Id": "1", "CollectionType": "tvshows"},
                {"Name": "", "Id": "2"},
                {"Name": "Movies", "ItemId": "3", "CollectionType": "movies"},
            ]
        }
    )
    assert listed == [
        {"id": "3", "name": "Movies", "type": "movies"},
        {"id": "1", "name": "TV shows", "type": "tvshows"},
    ], listed
    print("self-test ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build .strm files from an M3U and remove ones Emby already has.")
    parser.add_argument("--config", help="JSON config downloaded from NimboVoD")
    parser.add_argument(
        "--m3u",
        action="append",
        dest="m3us",
        help="Playlist URL or local .m3u path. Pass once per playlist.",
    )
    parser.add_argument("--out", help="Folder where .strm files are written")
    parser.add_argument("--emby", help="Emby server address, for example http://192.168.1.20:8096")
    parser.add_argument("--api-key", help="Emby API key")
    parser.add_argument("--user-id", default="", help="Optional Emby user id")
    parser.add_argument("--layout", choices=("group", "flat", "library"), help="Folder layout")
    parser.add_argument("--export", help="Write the Emby library to this JSON file and stop")
    parser.add_argument("--apply", action="store_true", help="Write files and delete duplicates. Omit for a dry run.")
    parser.add_argument("--ignore-years", action="store_true", help="Match titles even when years differ")
    parser.add_argument("--keep-quality", action="store_true", help="Keep quality tags in file names")
    parser.add_argument(
        "--keep-duplicate-strm",
        action="store_true",
        help="Keep a second .strm when two streams clean up to the same file name.",
    )
    parser.add_argument(
        "--strip-tag",
        action="append",
        dest="strip_tags",
        help="Extra tag to remove from names, along with the quality tags. Repeat for each tag.",
    )
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    config_path = args.config
    if not config_path and not args.m3us and not args.export:
        config_path = implicit_config()
        if config_path:
            print(f"Using {config_path}")
    config = load_config(config_path)
    sources = list(args.m3us or []) or source_list(config.get("m3u"))
    out = args.out or config.get("out") or ""
    emby = args.emby or config.get("emby") or ""
    api_key = args.api_key or config.get("apiKey") or ""
    user_id = args.user_id or config.get("userId") or ""
    layout = args.layout or config.get("layout") or "group"
    strip_quality = False if args.keep_quality else bool(config.get("stripQuality", True))
    use_years = False if args.ignore_years else bool(config.get("useYears", True))
    match_filenames = bool(config.get("matchFilenames", True))
    extra_tags: list[str] = list(args.strip_tags or [])
    configured_tags = config.get("extraTags")
    if isinstance(configured_tags, str):
        extra_tags.append(configured_tags)
    elif isinstance(configured_tags, list):
        extra_tags.extend(item for item in configured_tags if isinstance(item, str))
    drop_duplicate_strm = not args.keep_duplicate_strm and bool(config.get("dropDuplicateStrm", True))

    if args.export:
        if not emby or not api_key:
            raise SystemExit("--export needs --emby and --api-key.")
        items = fetch_emby(emby, api_key, user_id)
        Path(args.export).write_text(json.dumps({"source": "emby", "items": items}, indent=2), encoding="utf-8")
        print(f"Exported {len(items)} items to {args.export}")
        return

    if not sources or not str(out).strip():
        raise SystemExit(missing_config_message())
    entries = load_playlist_entries(sources)
    if not entries:
        raise SystemExit("No streams were found in those playlists.")
    library = None
    if emby or api_key:
        if not emby or not api_key:
            raise SystemExit("Emby comparison needs both the server address and an API key. Turn on the API key option on the NimboVoD page and download the config again.")
        print(f"Reading Emby at {emby}")
        library = fetch_emby(emby, api_key, user_id)
        print(f"Emby items: {len(library)}")
    else:
        print("No Emby server given. Nothing will be treated as a duplicate.")
    planned = plan_entries(
        entries,
        library,
        {
            "layout": layout,
            "strip_quality": strip_quality,
            "use_years": use_years,
            "match_filenames": match_filenames,
            "extra_tags": extra_tags,
            "drop_duplicate_strm": drop_duplicate_strm,
            "out": str(out).strip(),
        },
    )
    write_plan(Path(out), planned, args.apply)


if __name__ == "__main__":
    code = 0
    try:
        main()
    except BrokenPipeError:
        code = 0
    except SystemExit as exc:
        status = exc.code
        if status is None or status == 0 or status == "":
            code = 0
        elif isinstance(status, int):
            code = status
        else:
            print(status, file=sys.stderr)
            code = 1
    except Exception as exc:
        print(exc, file=sys.stderr)
        code = 1
    pause_if_needed()
    raise SystemExit(code)
