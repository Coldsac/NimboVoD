#!/usr/bin/env python3
"""NimboVoD local cleaner.

Build a .strm folder from an M3U playlist, compare it with an Emby library,
and delete only the .strm files from this playlist that Emby already has.

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
import sys
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
ITEM_LIMIT = 30000


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


def index_library(
    items: list[dict],
    match_filenames: bool,
    extra: re.Pattern[str] | None = None,
) -> tuple[dict, dict, dict, dict]:
    movies: dict[str, list[dict]] = {}
    series: dict[str, list[dict]] = {}
    episodes: dict[str, list[dict]] = {}
    filenames: dict[str, list[dict]] = {}
    for item in items:
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
        path = item.get("path") or item.get("Path")
        if match_filenames and isinstance(path, str) and path.strip():
            base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", path.replace("\\", "/").split("/")[-1])
            file_key, file_year = normalize_title(base, extra)
            remember(filenames, file_key, label, year if isinstance(year, int) else file_year)
    return movies, series, episodes, filenames


def disk_duplicate_rows(folder: Path, planned: list[dict]) -> list[dict]:
    if not folder.is_dir():
        return []
    known = {row["path"].replace("\\", "/").lower() for row in planned}
    found: list[dict] = []
    for row in planned:
        if row["disposition"] != "keep":
            continue
        relative = Path(row["path"].replace("\\", "/"))
        parent = folder / relative.parent
        if not parent.is_dir():
            continue
        pattern = re.compile(rf"^{re.escape(relative.stem)} \((\d+)\){re.escape(relative.suffix)}$", re.I)
        for child in parent.iterdir():
            match = pattern.match(child.name)
            if not child.is_file() or match is None or int(match.group(1)) < 2:
                continue
            sibling = (relative.parent / child.name).as_posix()
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
    movies, series, episodes, filenames = (
        index_library(library, options["match_filenames"], extra) if library is not None else ({}, {}, {}, {})
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
            show_key, _year = normalize_title(show, extra)
            hit = (episodes.get(f"{show_key}|{season}|{number}") or [None])[0]
            if hit:
                reason = f"Already in Emby · {hit['label']}"
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


def request_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"X-Emby-Token": api_key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Emby did not return a library list.")
    return payload


def fetch_emby(base_url: str, api_key: str, user_id: str) -> list[dict]:
    origin = base_url.rstrip("/")
    origin = re.sub(r"/web(/index\.html?)?$", "", origin, flags=re.I)
    origin = re.sub(r"/emby$", "", origin, flags=re.I)
    user_path = f"/Users/{urllib.parse.quote(user_id)}/Items" if user_id else "/Items"
    last_error = "Emby could not be reached."
    for prefix in ("/emby", ""):
        try:
            start = 0
            items: list[dict] = []
            total = None
            while len(items) < ITEM_LIMIT:
                query = urllib.parse.urlencode(
                    {
                        "Recursive": "true",
                        "IncludeItemTypes": "Movie,Series,Episode",
                        "Fields": "Path,ProductionYear",
                        "StartIndex": start,
                        "Limit": PAGE_SIZE,
                        "EnableImages": "false",
                        "EnableUserData": "false",
                    }
                )
                payload = request_json(f"{origin}{prefix}{user_path}?{query}", api_key)
                batch = payload.get("Items")
                if not isinstance(batch, list):
                    raise RuntimeError("Emby did not return a library list.")
                for raw in batch:
                    if not isinstance(raw, dict) or not raw.get("Name"):
                        continue
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
                start += len(batch)
                if isinstance(payload.get("TotalRecordCount"), int):
                    total = payload["TotalRecordCount"]
                if len(batch) < PAGE_SIZE or (total is not None and start >= total):
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


def fetch_text(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("The playlist URL must start with http or https.")
    request = urllib.request.Request(url, headers={"User-Agent": "NimboVoD/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def inside(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise SystemExit(f"Refusing an unsafe path: {relative}")
    root = root.resolve()
    target = (root / relative).resolve()
    target.relative_to(root)
    return target


def write_plan(out: Path, planned: list[dict], apply: bool) -> None:
    removed = 0
    written = 0
    kept_paths = {row["path"].replace("\\", "/").lower() for row in planned if row["disposition"] == "keep"}
    for row in planned:
        if row["disposition"] != "keep" and row["path"].replace("\\", "/").lower() in kept_paths:
            continue
        target = inside(out, row["path"])
        if row["disposition"] == "keep":
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(row["url"].strip() + "\n", encoding="utf-8")
            written += 1
            continue
        if target.exists() and target.is_file():
            if apply:
                target.unlink()
            removed += 1
            print(f"{'deleted' if apply else 'would delete'}  {row['path']}  ({row['reason']})")
    kept = sum(1 for row in planned if row["disposition"] == "keep")
    dropped = len(planned) - kept
    print("")
    print(f"Playlist entries: {len(planned)}")
    print(f"Already in Emby or duplicate: {dropped}")
    print(f"New .strm files: {kept}")
    print(f"Existing duplicate files {'removed' if apply else 'that would be removed'}: {removed}")
    if not apply:
        if os.environ.get("NIMBOVOD_HOME"):
            print("Dry run only. Nothing was written. When the plan looks right, run: nimbovod-cleaner.exe --apply")
        else:
            print("Dry run only. Pass --apply to write the new files and remove duplicates already in the folder.")
    else:
        print(f"Wrote {written} files under {out}")


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
