#!/usr/bin/env python3
"""NimboVoD desktop window.

Load M3U playlists, compare them with an Emby library, and write the .strm
folder. Nothing is written or deleted until Write .strm files is confirmed,
unless automatic updates are turned on in Settings.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
# The cleaner is loaded from a separate file, so PyInstaller only sees imports made here.
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

CANVAS = "#090b10"
SURFACE = "#12151c"
RAISED = "#1b2130"
FIELD = "#0c0f14"
FG = "#eef1f6"
MUTED = "#8e97a8"
LINE = "#2a3142"
ACCENT = "#6e8cff"
ACCENT_ACTIVE = "#8aa4ff"
SELECT = "#243454"
ERROR_BG = "#1a1418"
ERROR_FG = "#f0c8d0"
ERROR_LINE = "#c45d6e"
MAX_PLAYLIST_BYTES = 80 * 1024 * 1024
TREE_LIMIT = 2500

LAYOUT_HINTS = {
    "group": "One folder per group-title. The same stream URL in two playlists is kept once.",
    "flat": "Every .strm file goes in a single folder.",
    "library": "Movies go in Movies/. Episodes go under TV/Show/Season 01/.",
}

SAMPLE_PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="matrix" group-title="Movies",The Matrix (1999)
https://example.com/streams/the-matrix-1999
#EXTINF:-1 group-title="Movies",Inception (2010) 1080p
https://example.com/streams/inception-2010
#EXTINF:-1 group-title="Movies",Inception.2010.UHD.BluRay
https://example.com/streams/inception-uhd
#EXTINF:-1 group-title="Movies",Dune (2021)
https://example.com/streams/dune-2021
#EXTINF:-1 group-title="Classic Movies",Dune (1984)
https://example.com/streams/dune-1984
#EXTINF:-1 group-title="Movies",Arrival (2016)
https://example.com/streams/arrival
#EXTINF:-1 group-title="Movies",Blade Runner 2049
https://example.com/streams/blade-runner-2049
#EXTINF:-1 group-title="Movies",Heat (1995)
https://example.com/streams/heat
#EXTINF:-1 group-title="Movies",The Matrix (1999)
https://example.com/streams/the-matrix-1999
#EXTINF:-1 group-title="TV",The Office (2005)
https://example.com/streams/the-office
#EXTINF:-1 group-title="TV",The Office S01E01
https://example.com/streams/the-office-s01e01
#EXTINF:-1 group-title="TV",The Office S01E03
https://example.com/streams/the-office-s01e03
#EXTINF:-1 group-title="TV",Severance S01E01
https://example.com/streams/severance-s01e01
#EXTINF:-1 group-title="TV",Severance S01E02
https://example.com/streams/severance-s01e02
#EXTINF:-1 group-title="Live TV",City News
https://example.com/streams/city-news
"""

SAMPLE_LIBRARY = [
    {"name": "The Matrix", "year": 1999, "type": "Movie", "path": "/media/movies/The Matrix (1999).mkv"},
    {"name": "Inception", "year": 2010, "type": "Movie", "path": "/media/movies/Inception (2010).mkv"},
    {"name": "Dune", "year": 2021, "type": "Movie", "path": "/media/movies/Dune (2021).mkv"},
    {"name": "Arrival", "year": 2016, "type": "Movie"},
    {"name": "Heat", "year": 1995, "type": "Movie"},
    {"name": "The Office", "year": 2005, "type": "Series"},
    {"name": "Pilot", "year": 2005, "type": "Episode", "seriesName": "The Office", "season": 1, "episode": 1},
    {"name": "Diversity Day", "year": 2005, "type": "Episode", "seriesName": "The Office", "season": 1, "episode": 2},
    {"name": "Severance", "year": 2022, "type": "Series"},
    {
        "name": "Good News About Hell",
        "year": 2022,
        "type": "Episode",
        "seriesName": "Severance",
        "season": 1,
        "episode": 1,
    },
]


def ensure_stdio() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        return


def use_dark_titlebar(widget: tk.Misc) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
        child = int(widget.winfo_id())
        parent = int(user32.GetParent(child) or 0)
        hwnd = parent if window_class(parent) == "TkTopLevel" else int(user32.GetAncestor(child, 2) or 0)
        if not hwnd:
            return
        value = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        applied = False
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                applied = True
                break
        if applied:
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
    except Exception:
        return


WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_SYSCOMMAND = 0x0112
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_TRAY = 0x8001
SC_MINIMIZE = 0xF020
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
TRAY_MENU_OPEN = 1
TRAY_MENU_EXIT = 2


def is_minimize_command(message: int, wparam: int) -> bool:
    return message == WM_SYSCOMMAND and (int(wparam) & 0xFFF0) == SC_MINIMIZE


def tray_click(mouse: int) -> str:
    if mouse in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
        return "open"
    if mouse in (WM_RBUTTONUP, WM_CONTEXTMENU):
        return "menu"
    return ""


_TRAY_APIS = None


def _prepare_tray_apis():
    global _TRAY_APIS
    if _TRAY_APIS is not None:
        return _TRAY_APIS
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32
    lresult = ctypes.c_ssize_t
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = lresult
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    ]
    user32.TrackPopupMenu.restype = ctypes.c_uint
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = wintypes.HICON
    user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _TRAY_APIS = (user32, shell32, gdi32, lresult)
    return _TRAY_APIS


def window_class(hwnd: int) -> str:
    if sys.platform != "win32" or not hwnd:
        return ""
    import ctypes

    user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def create_tray_icon(size: int = 32):
    """Draw a blue notification icon. Returns (handle, owned). A shared fallback icon is not owned."""
    import ctypes
    from ctypes import wintypes

    user32, _shell32, gdi32, _lresult = _prepare_tray_apis()

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 1)]

    class IconInfo(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    def fallback():
        handle = user32.LoadIconW(None, 32512)
        return (handle or 0), False

    pixels = bytearray(size * size * 4)
    radius = 7
    for y in range(size):
        for x in range(size):
            outside = False
            corners = (
                (radius, radius),
                (size - 1 - radius, radius),
                (radius, size - 1 - radius),
                (size - 1 - radius, size - 1 - radius),
            )
            if x < radius or x > size - 1 - radius:
                if y < radius or y > size - 1 - radius:
                    corner_x, corner_y = min(corners, key=lambda point: (point[0] - x) ** 2 + (point[1] - y) ** 2)
                    outside = (x - corner_x) ** 2 + (y - corner_y) ** 2 > radius * radius
            if x < 1 or y < 1 or x > size - 2 or y > size - 2:
                outside = True
            left = size * 0.34
            right = size * 0.72
            top = size * 0.28
            bottom = size * 0.72
            play = False
            if left <= x <= right:
                span = (bottom - top) / 2 * (1 - (x - left) / (right - left))
                play = abs(y - (top + bottom) / 2) <= span
            if outside:
                blue = green = red = alpha = 0
            elif play:
                blue, green, red, alpha = 0xFF, 0xF8, 0xF7, 255
            else:
                blue, green, red, alpha = 0xFF, 0x8C, 0x6E, 255
            offset = ((size - 1 - y) * size + x) * 4
            pixels[offset] = blue * alpha // 255
            pixels[offset + 1] = green * alpha // 255
            pixels[offset + 2] = red * alpha // 255
            pixels[offset + 3] = alpha
    color = mask = 0
    try:
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = size
        info.bmiHeader.biHeight = size
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        screen = user32.GetDC(None)
        color = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        user32.ReleaseDC(None, screen)
        if not color or not bits:
            return fallback()
        raw = (ctypes.c_ubyte * len(pixels)).from_buffer_copy(pixels)
        ctypes.memmove(bits, raw, len(pixels))
        mask_bits = (ctypes.c_ubyte * (size * size // 8))()
        mask = gdi32.CreateBitmap(size, size, 1, 1, ctypes.cast(mask_bits, ctypes.c_void_p))
        icon = IconInfo()
        icon.fIcon = True
        icon.hbmMask = mask
        icon.hbmColor = color
        handle = user32.CreateIconIndirect(ctypes.byref(icon))
        if not handle:
            return fallback()
        return handle, True
    except Exception:
        return fallback()
    finally:
        if color:
            gdi32.DeleteObject(color)
        if mask:
            gdi32.DeleteObject(mask)


def _tray_icon_type():
    import ctypes
    from ctypes import wintypes

    class NotifyIconData(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
        ]

    return NotifyIconData


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def cleaner_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "nimbovod-cleaner.py"
    return Path(__file__).resolve().with_name("nimbovod-cleaner.py")


def settings_path() -> Path:
    override = os.environ.get("NIMBOVOD_SETTINGS", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    folder = root / "NimboVoD"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


STARTUP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_RUN_NAME = "NimboVoD"


def startup_registration_allowed() -> bool:
    """Change the real sign-in startup entry only for the packaged exe."""
    if os.environ.get("NIMBOVOD_SETTINGS", "").strip():
        return False
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def startup_command() -> str:
    return f'"{Path(sys.executable).resolve()}"'


def read_startup_command() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _kind = winreg.QueryValueEx(key, STARTUP_RUN_NAME)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def apply_startup_registration(enabled: bool) -> str | None:
    if not startup_registration_allowed():
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, STARTUP_RUN_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        return f"Could not update Windows startup: {exc}"
    return None


def auto_steps(pull_playlists: bool, pull_emby: bool, apply_strm: bool) -> list[str]:
    steps: list[str] = []
    if pull_playlists:
        steps.append("playlists")
    if pull_emby:
        steps.append("emby")
    if apply_strm:
        steps.append("apply")
    return steps


def host_resolves(host: str, timeout: float = 3.0) -> bool:
    """Name lookup with a limit. The normal lookup can stall right after a restart."""
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
        done.set()

    threading.Thread(target=work, daemon=True).start()
    if not done.wait(timeout):
        return False
    return bool(outcome.get("ok"))


def refresh_minutes(value: str) -> int:
    """Minutes between automatic refreshes. Blank or invalid means off."""
    text = value.strip()
    if not text:
        return 0
    try:
        minutes = int(text)
    except ValueError:
        return 0
    if minutes < 1:
        return 0
    return min(minutes, 10080)


def load_cleaner():
    path = cleaner_path()
    if not path.is_file():
        raise FileNotFoundError(f"Missing cleaner logic: {path}")
    spec = importlib.util.spec_from_file_location("nimbovod_cleaner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load cleaner logic from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def explain(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        if isinstance(exc.code, str) and exc.code.strip():
            return exc.code.strip()
        if exc.code in (None, 0, ""):
            return "Stopped."
        return str(exc.code)
    message = str(exc).strip()
    return message or exc.__class__.__name__


def playlist_label(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        parsed = urlparse(source)
        last = [part for part in parsed.path.split("/") if part]
        return last[-1] if last else (parsed.hostname or source)
    return Path(source).name or source


def looks_like_playlist(text: str) -> bool:
    head = text[:4000]
    return "#EXTM3U" in head.upper() or "#EXTINF:" in head.upper()


def optional_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def as_kind(value: object) -> str:
    if value in {"Movie", "Series", "Episode"}:
        return str(value)
    return "Other"


def map_record(raw: object, cleaner) -> dict | None:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        _key, year = cleaner.normalize_title(name)
        return {"name": name, "year": year, "type": "Movie"}
    if not isinstance(raw, dict):
        return None
    name = raw.get("Name") if isinstance(raw.get("Name"), str) else raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    year = optional_number(raw.get("ProductionYear", raw.get("year")))
    if year is None:
        _key, year = cleaner.normalize_title(name)
    path = raw.get("Path", raw.get("path"))
    series = raw.get("SeriesName", raw.get("seriesName"))
    item = {
        "name": name.strip(),
        "year": year,
        "type": as_kind(raw.get("Type", raw.get("type"))),
    }
    if isinstance(path, str) and path.strip():
        item["path"] = path
    if isinstance(series, str) and series.strip():
        item["seriesName"] = series.strip()
    season = optional_number(raw.get("ParentIndexNumber", raw.get("season")))
    episode = optional_number(raw.get("IndexNumber", raw.get("episode")))
    if season is not None:
        item["season"] = season
    if episode is not None:
        item["episode"] = episode
    return item


def extract_records(parsed: object) -> list:
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    nested = parsed.get("Items", parsed.get("items", parsed.get("results")))
    if isinstance(nested, list):
        return nested
    if isinstance(parsed.get("name"), str) or isinstance(parsed.get("Name"), str):
        return [parsed]
    return []


def parse_library_text(text: str, cleaner) -> list[dict]:
    trimmed = text.replace("\ufeff", "").strip()
    if not trimmed:
        return []
    if trimmed.startswith("{") or trimmed.startswith("["):
        try:
            parsed = json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise ValueError("That file is not valid JSON.") from exc
        items = [item for item in (map_record(record, cleaner) for record in extract_records(parsed)) if item]
        if not items:
            raise ValueError("No library titles were found in that JSON.")
        return items
    items: list[dict] = []
    for line in trimmed.splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        episode = cleaner.parse_episode(name)
        if episode:
            show, season, number, _rest = episode
            _key, year = cleaner.normalize_title(show)
            items.append(
                {
                    "name": name,
                    "year": year,
                    "type": "Episode",
                    "seriesName": show,
                    "season": season,
                    "episode": number,
                }
            )
            continue
        _key, year = cleaner.normalize_title(name)
        items.append({"name": name, "year": year, "type": "Movie"})
    if not items:
        raise ValueError("No titles were found. Put one title on each line.")
    return items


def report_text(planned: list[dict], source_label: str, compared: bool) -> str:
    kept = [row for row in planned if row["disposition"] == "keep"]
    dropped = [row for row in planned if row["disposition"] != "keep"]
    lines = [
        "NimboVoD report",
        f"Source: {source_label or 'playlist'}",
        f"Compared with Emby: {'yes' if compared else 'no'}",
        f"New .strm files: {len(kept)}",
        f"Left out: {len(dropped)}",
        "",
        "Left out",
    ]
    lines.extend(
        f"{row['path']}\t{row.get('reason', '')}\t{row['title']}" for row in dropped
    )
    if not dropped:
        lines.append("(none)")
    lines.extend(["", "Kept"])
    lines.extend(f"{row['path']}\t{row['title']}" for row in kept)
    if not kept:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cleaner = load_cleaner()
        self._tag_job: str | None = None
        self._wheel_job: str | None = None
        self._wheel_pending = 0.0
        self._refresh_job: str | None = None
        self._startup_job: str | None = None
        self._ui_thread = threading.current_thread()
        self._ui_calls: queue.Queue = queue.Queue()
        self._auto_running = False
        self.links: list[str] = []
        self.playlist_files: list[str] = []
        self.sources: list[dict] = []
        self.library: list[dict] | None = None
        self.library_label = ""
        self.emby_libraries: list[dict] = []
        self._library_vars: list[tk.IntVar] = []
        self.planned: list[dict] = []
        self._buttons: list[ttk.Button] = []
        self._busy = False
        self._ready = False
        self._auto_queue: list[str] = []
        self._auto_note = ""
        self._in_tray = False
        self._tray_added = False
        self._tray_hwnd = 0
        self._tray_proc = None
        self._old_wndproc = None
        self._tray_icon = None
        self._tray_icon_owned = False
        self._plan_hidden_for_tray = False
        self._tray_hook_tries = 0
        self._tray_minimizing = False
        self._tray_requests: list[str] = []
        self.title("NimboVoD")
        self.geometry("640x720")
        self.minsize(560, 640)
        self.plan_window: tk.Toplevel | None = None
        self.configure(bg=CANVAS)
        self._init_vars()
        self._apply_style()
        self._build()
        self._load_settings()
        self._bind_options()
        self._ready = True
        self._update_layout_hint()
        self._sync_startup_registration()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(30, self._poll_ui_calls)
        self.after(400, self._startup_auto)

    def _init_vars(self) -> None:
        self.link_entry = tk.StringVar()
        self.out_dir = tk.StringVar()
        self.emby = tk.StringVar()
        self.api_key = tk.StringVar()
        self.user_id = tk.StringVar()
        self.layout = tk.StringVar(value="group")
        self.strip_quality = tk.IntVar(value=1)
        self.drop_duplicate_strm = tk.IntVar(value=1)
        self.use_years = tk.IntVar(value=1)
        self.match_filenames = tk.IntVar(value=1)
        self.run_at_startup = tk.IntVar(value=0)
        self.auto_pull_playlists = tk.IntVar(value=0)
        self.auto_pull_emby = tk.IntVar(value=0)
        self.auto_apply_strm = tk.IntVar(value=0)
        self.refresh_minutes = tk.StringVar(value="")
        self.show_key = tk.IntVar(value=0)
        self.filter_mode = tk.StringVar(value="All")
        self.query = tk.StringVar()
        self.summary = tk.StringVar(value="")
        self.status = tk.StringVar(value="Ready")
        self.library_status = tk.StringVar(value="No library loaded. Every stream will be kept.")
        self.layout_hint = tk.StringVar(value=LAYOUT_HINTS["group"])

    def _apply_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=CANVAS, foreground=FG, font=("Segoe UI", 10))
        style.configure("TFrame", background=CANVAS)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=CANVAS, foreground=FG)
        style.configure("Muted.TLabel", background=CANVAS, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=SURFACE, foreground=FG)
        style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=FG, font=("Segoe UI", 12))
        style.configure(
            "TEntry",
            fieldbackground=FIELD,
            foreground=FG,
            bordercolor=LINE,
            lightcolor=FIELD,
            darkcolor=FIELD,
            insertcolor=FG,
            padding=6,
        )
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)], darkcolor=[("focus", FIELD)])
        style.configure(
            "TButton",
            background=RAISED,
            foreground=FG,
            bordercolor=LINE,
            lightcolor=RAISED,
            darkcolor=RAISED,
            focuscolor=RAISED,
            padding=(12, 7),
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("active", "#242b3a"), ("pressed", "#2c3548"), ("disabled", SURFACE)],
            bordercolor=[("active", "#3c465c"), ("focus", ACCENT), ("disabled", LINE)],
            foreground=[("disabled", MUTED)],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#f7f8ff",
            bordercolor=ACCENT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            focuscolor=ACCENT,
            font=("Segoe UI", 10, "bold"),
            padding=(14, 7),
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_ACTIVE), ("pressed", "#5876ea"), ("disabled", "#2a3348")],
            bordercolor=[("active", ACCENT_ACTIVE), ("disabled", "#2a3348")],
            foreground=[("disabled", MUTED)],
        )
        style.configure(
            "Card.TCheckbutton",
            background=SURFACE,
            foreground=FG,
            indicatorcolor=FIELD,
            focuscolor=SURFACE,
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", SURFACE)],
            indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
        )
        style.configure(
            "Card.TRadiobutton",
            background=SURFACE,
            foreground=FG,
            indicatorcolor=FIELD,
            focuscolor=SURFACE,
        )
        style.map(
            "Card.TRadiobutton",
            background=[("active", SURFACE)],
            indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
        )
        style.configure(
            "Treeview",
            background=FIELD,
            fieldbackground=FIELD,
            foreground=FG,
            bordercolor=FIELD,
            lightcolor=FIELD,
            darkcolor=FIELD,
            rowheight=30,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=SURFACE,
            foreground=MUTED,
            bordercolor=LINE,
            lightcolor=SURFACE,
            darkcolor=SURFACE,
            relief="flat",
            font=("Segoe UI", 9),
        )
        style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", FG)])
        style.map("Treeview.Heading", background=[("active", RAISED)])
        style.configure(
            "App.TNotebook",
            background=CANVAS,
            borderwidth=0,
            relief="flat",
            tabmargins=(2, 6, 2, 0),
        )
        style.configure(
            "App.TNotebook.Tab",
            background=SURFACE,
            foreground=MUTED,
            borderwidth=0,
            padding=(14, 8),
            font=("Segoe UI", 10),
            focuscolor=CANVAS,
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", RAISED), ("active", "#242b3a")],
            foreground=[("selected", FG), ("active", FG)],
            padding=[("selected", (14, 8))],
        )
        style.layout(
            "App.TNotebook.Tab",
            [
                (
                    "Notebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [("Notebook.label", {"side": "top", "sticky": ""})],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        style.configure(
            "TCombobox",
            fieldbackground=FIELD,
            background=RAISED,
            foreground=FG,
            arrowcolor=MUTED,
            bordercolor=LINE,
            lightcolor=FIELD,
            darkcolor=FIELD,
            padding=4,
            arrowsize=14,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD), ("disabled", SURFACE)],
            foreground=[("readonly", FG), ("disabled", MUTED)],
            bordercolor=[("focus", ACCENT)],
        )
        for orient in ("Vertical", "Horizontal"):
            style.configure(
                f"{orient}.TScrollbar",
                background=LINE,
                troughcolor=CANVAS,
                bordercolor=CANVAS,
                arrowcolor=MUTED,
                lightcolor=CANVAS,
                darkcolor=CANVAS,
                arrowsize=12,
            )
            style.map(f"{orient}.TScrollbar", background=[("active", "#3c465c"), ("!active", LINE)])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", SELECT)
        self.option_add("*TCombobox*Listbox.selectForeground", FG)
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        self.option_add("*TCombobox*Listbox.relief", "flat")

    def _build(self) -> None:
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        header = tk.Frame(self, bg=CANVAS)
        header.pack(fill="x", padx=18, pady=(16, 10))
        text = tk.Frame(header, bg=CANVAS)
        text.pack(side="left", fill="both", expand=True)
        tk.Label(text, text="PLAYLIST TO STRM", bg=CANVAS, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(text, text="NimboVoD", bg=CANVAS, fg=FG, font=("Segoe UI", 22)).pack(anchor="w", pady=(2, 0))
        self.header_blurb = tk.Label(
            text,
            text="Playlists in, .strm files out, with anything already in Emby left out.",
            bg=CANVAS,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=340,
        )
        self.header_blurb.pack(anchor="w", pady=(6, 0))
        sample = ttk.Button(header, text="Preview sample", command=self.load_sample)
        sample.pack(side="right", anchor="n", padx=(16, 0))
        self._buttons.append(sample)

        self.error_host = tk.Frame(self, bg=CANVAS)
        tk.Frame(self.error_host, bg=ERROR_LINE, width=3).pack(side="left", fill="y")
        self.error_label = tk.Label(
            self.error_host,
            text="",
            bg=ERROR_BG,
            fg=ERROR_FG,
            anchor="w",
            justify="left",
            wraplength=1040,
            padx=12,
            pady=8,
            font=("Segoe UI", 9),
        )
        self.error_label.pack(side="left", fill="both", expand=True)

        status = tk.Label(
            self,
            textvariable=self.status,
            bg=CANVAS,
            fg=MUTED,
            anchor="w",
            padx=18,
            pady=8,
            font=("Segoe UI", 9),
        )
        status.pack(side="bottom", fill="x")
        tk.Frame(self, bg=LINE, height=1).pack(side="bottom", fill="x")

        self.form_host = ttk.Frame(self)
        self.form_host.pack(fill="both", expand=True, padx=18, pady=(2, 12))
        self._build_form()
        self.bind("<Configure>", self._on_resize)
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<Unmap>", self._on_unmap, add="+")
        self.after_idle(lambda: use_dark_titlebar(self))
        self.after_idle(self._install_tray_hook)

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self.error_label.configure(wraplength=max(240, event.width - 90))
        self.header_blurb.configure(wraplength=max(200, event.width - 230))
        hint_width = max(220, event.width - 72)
        alive = []
        for label in self._wrapping:
            try:
                if label.winfo_exists():
                    label.configure(wraplength=hint_width)
                    alive.append(label)
            except tk.TclError:
                continue
        self._wrapping = alive

    def _tab(self, notebook: ttk.Notebook, title: str) -> tk.Frame:
        page = tk.Frame(notebook, bg=CANVAS)
        notebook.add(page, text=title)
        return page

    def _panel(self, parent: tk.Misc, *, expand: bool) -> ttk.Frame:
        shell = tk.Frame(parent, bg=LINE, padx=1, pady=1)
        shell.pack(fill="both" if expand else "x", expand=expand)
        frame = ttk.Frame(shell, style="Card.TFrame", padding=(14, 12))
        frame.pack(fill="both", expand=True)
        return frame

    def _hint(self, parent: tk.Misc, text: str, **pack) -> ttk.Label:
        label = ttk.Label(parent, text=text, style="CardMuted.TLabel", wraplength=480)
        label.pack(anchor="w", **pack)
        self._wrapping.append(label)
        return label

    def _button(self, parent: tk.Misc, text: str, command, accent: bool = False) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "TButton")
        self._buttons.append(button)
        return button

    def _listbox(self, parent: tk.Misc, height: int) -> tk.Listbox:
        box = tk.Listbox(
            parent,
            height=height,
            bg=FIELD,
            fg=FG,
            selectbackground=SELECT,
            selectforeground=FG,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            relief="flat",
            activestyle="none",
            font=("Segoe UI", 10),
            borderwidth=0,
        )
        box.bind("<MouseWheel>", self._wheel_widget)
        return box

    def _build_form(self) -> None:
        self._wrapping = []
        self.form_canvas = None
        notebook = ttk.Notebook(self.form_host, style="App.TNotebook")
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        self._tab_canvases: list[tk.Canvas] = []
        self._wheel_target = None
        playlists = self._scroll_tab(self._tab(notebook, "Playlists"))
        self._build_playlist_tab(playlists)
        self._bind_tab_wheel(playlists)
        self._build_names_tab(self._panel(self._tab(notebook, "File names"), expand=True))
        emby = self._scroll_tab(self._tab(notebook, "Emby"))
        self._build_emby_tab(emby)
        self._bind_tab_wheel(emby)
        self._build_output_tab(self._panel(self._tab(notebook, "Output"), expand=False))
        self._build_settings_tab(self._panel(self._tab(notebook, "Settings"), expand=False))

    def _scroll_tab(self, page: tk.Frame) -> ttk.Frame:
        """Scroll this tab only when its own contents are taller than the page."""
        canvas = tk.Canvas(page, bg=CANVAS, highlightthickness=0, borderwidth=0, yscrollincrement=1)
        bar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=CANVAS)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        pending = {"job": None, "busy": False}

        def schedule(_event: tk.Event | None = None) -> None:
            if pending["job"] is None:
                pending["job"] = canvas.after_idle(apply)

        def apply() -> None:
            pending["job"] = None
            if pending["busy"] or not canvas.winfo_exists():
                return
            pending["busy"] = True
            try:
                width = canvas.winfo_width()
                if width > 1:
                    canvas.itemconfigure(window, width=width)
                bbox = canvas.bbox("all")
                if not bbox:
                    return
                canvas.configure(scrollregion=bbox)
                overflow = (bbox[3] - bbox[1]) - canvas.winfo_height()
                if overflow > 2 and not bar.winfo_ismapped():
                    bar.pack(side="right", fill="y")
                elif overflow <= 2 and bar.winfo_ismapped():
                    bar.pack_forget()
                    canvas.yview_moveto(0)
            finally:
                pending["busy"] = False

        inner.bind("<Configure>", schedule)
        canvas.bind("<Configure>", schedule)
        canvas.bind("<MouseWheel>", self._wheel_form)
        self._tab_canvases.append(canvas)
        return self._panel(inner, expand=False)

    def _bind_tab_wheel(self, widget: tk.Misc) -> None:
        if widget not in (getattr(self, "link_list", None), getattr(self, "source_list", None), getattr(self, "paste", None), getattr(self, "extra_tags", None)):
            widget.bind("<MouseWheel>", self._wheel_form)
        for child in widget.winfo_children():
            self._bind_tab_wheel(child)

    def _canvas_for_widget(self, widget: tk.Misc | None) -> tk.Canvas | None:
        while widget is not None:
            if widget in self._tab_canvases:
                return widget
            widget = getattr(widget, "master", None)
        return None

    def _build_settings_tab(self, page: ttk.Frame) -> None:
        ttk.Checkbutton(
            page,
            text="Run NimboVoD when Windows starts",
            variable=self.run_at_startup,
            style="Card.TCheckbutton",
        ).pack(anchor="w")
        self._hint(
            page,
            "Opens this program when you sign in. It uses your account, so administrator rights are not required.",
            pady=(2, 8),
        )
        ttk.Checkbutton(
            page,
            text="Automatically pull the M3U playlists",
            variable=self.auto_pull_playlists,
            style="Card.TCheckbutton",
        ).pack(anchor="w")
        ttk.Checkbutton(
            page,
            text="Automatically pull the Emby library",
            variable=self.auto_pull_emby,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(
            page,
            text="Automatically update the .strm folder",
            variable=self.auto_apply_strm,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(4, 0))
        timer = ttk.Frame(page, style="Card.TFrame")
        timer.pack(fill="x", pady=(12, 0))
        ttk.Label(timer, text="Refresh every", style="Card.TLabel").pack(side="left")
        ttk.Entry(timer, textvariable=self.refresh_minutes, width=6).pack(side="left", padx=(8, 6))
        ttk.Label(timer, text="minutes", style="Card.TLabel").pack(side="left")
        self._hint(
            page,
            "Checked steps run when NimboVoD opens, one at a time: playlists, then Emby, then the .strm folder. "
            "A number of minutes repeats that sequence. Leave it blank to run only when the program opens. "
            "Updating the folder does not ask again.",
            pady=(8, 0),
        )

    def _build_playlist_tab(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="M3U links", style="Card.TLabel").pack(anchor="w")
        row = ttk.Frame(page, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 6))
        entry = ttk.Entry(row, textvariable=self.link_entry)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.add_link())
        self._button(row, "Add", self.add_link).pack(side="left", padx=(8, 0))
        self.link_list = self._listbox(page, 4)
        self.link_list.pack(fill="both", expand=True, pady=(0, 6))
        actions = ttk.Frame(page, style="Card.TFrame")
        actions.pack(fill="x")
        self._button(actions, "Remove link", self.remove_link).pack(side="left")
        self._button(actions, "Add playlist files", self.add_files).pack(side="left", padx=(8, 0))
        self._button(actions, "Load playlists", self.load_links, accent=True).pack(side="right")
        ttk.Label(page, text="Loaded", style="Card.TLabel").pack(anchor="w", pady=(12, 4))
        self.source_list = self._listbox(page, 3)
        self.source_list.pack(fill="both", expand=True)
        self._button(page, "Remove loaded playlist", self.remove_source).pack(anchor="w", pady=(6, 10))
        ttk.Label(page, text="Folder layout", style="Card.TLabel").pack(anchor="w")
        radios = ttk.Frame(page, style="Card.TFrame")
        radios.pack(fill="x", pady=(4, 4))
        for value, label in (("group", "By group"), ("flat", "Flat"), ("library", "Movies & TV")):
            ttk.Radiobutton(
                radios,
                text=label,
                value=value,
                variable=self.layout,
                style="Card.TRadiobutton",
            ).pack(side="left", padx=(0, 12))
        hint = ttk.Label(page, textvariable=self.layout_hint, style="CardMuted.TLabel", wraplength=480)
        hint.pack(anchor="w")
        self._wrapping.append(hint)

    def _build_names_tab(self, page: ttk.Frame) -> None:
        ttk.Checkbutton(
            page,
            text="Strip quality tags from file names",
            variable=self.strip_quality,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(page, text="Quality tags already removed", style="Card.TLabel").pack(anchor="w")
        self._hint(page, ", ".join(self.cleaner.QUALITY_TAGS), pady=(2, 10))
        ttk.Label(page, text="Also strip these tags", style="Card.TLabel").pack(anchor="w")
        self._hint(
            page,
            "One per line or separated by commas. They are removed from file names and ignored when matching Emby.",
            pady=(2, 4),
        )
        self.extra_tags = self._text(page, 6)
        self.extra_tags.pack(fill="both", expand=True)
        ttk.Checkbutton(
            page,
            text="Remove duplicate .strm files",
            variable=self.drop_duplicate_strm,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(10, 0))
        self._hint(
            page,
            "When two streams clean up to the same file, the first is kept. Numbered copies already in the folder, such as Name (2).strm, are removed too.",
            pady=(2, 0),
        )

    def _build_emby_tab(self, page: ttk.Frame) -> None:
        self._labeled_entry(page, "Server address", self.emby, "http://192.168.1.20:8096")
        self._hint(page, "A 192.168 address works here. This program runs on your computer.", pady=(0, 6))
        key_row = ttk.Frame(page, style="Card.TFrame")
        key_row.pack(fill="x")
        ttk.Label(key_row, text="API key", style="Card.TLabel").pack(side="left")
        ttk.Checkbutton(
            key_row,
            text="Show",
            variable=self.show_key,
            command=self._toggle_key,
            style="Card.TCheckbutton",
        ).pack(side="right")
        self.api_entry = ttk.Entry(page, textvariable=self.api_key, show="•")
        self.api_entry.pack(fill="x", pady=(4, 6))
        self._labeled_entry(page, "User id", self.user_id, "Optional. Leave blank to read the whole server.")
        library_head = ttk.Frame(page, style="Card.TFrame")
        library_head.pack(fill="x", pady=(4, 4))
        ttk.Label(library_head, text="Libraries", style="Card.TLabel").pack(side="left")
        self._button(library_head, "Load libraries", self.load_emby_libraries).pack(side="right")
        self.library_choice = ttk.Frame(page, style="Card.TFrame")
        self.library_choice.pack(fill="x")
        self._hint(
            page,
            "Load libraries, then uncheck any that should be left out of the comparison. Until you load them, the pull reads every library.",
            pady=(2, 6),
        )
        self._render_library_choices()
        emby_actions = ttk.Frame(page, style="Card.TFrame")
        emby_actions.pack(fill="x", pady=(2, 6))
        self._button(emby_actions, "Pull library", self.pull_emby, accent=True).pack(side="left")
        self._button(emby_actions, "Upload export", self.upload_library).pack(side="left", padx=(8, 0))
        self._button(emby_actions, "Clear library", self.clear_library).pack(side="left", padx=(8, 0))
        status = ttk.Label(page, textvariable=self.library_status, style="CardMuted.TLabel", wraplength=480)
        status.pack(anchor="w", pady=(0, 6))
        self._wrapping.append(status)
        ttk.Label(page, text="Or paste titles", style="Card.TLabel").pack(anchor="w")
        self._hint(
            page,
            "One per line. A season already in Emby is left out. A later season that Emby does not have is added.",
            pady=(0, 4),
        )
        self._button(page, "Match pasted titles", self.match_pasted).pack(anchor="w", pady=(0, 6))
        self.paste = self._text(page, 3)
        self.paste.pack(fill="both", expand=True)

    def _build_output_tab(self, page: ttk.Frame) -> None:
        ttk.Label(page, text="Save folder", style="Card.TLabel").pack(anchor="w", pady=(0, 4))
        folder = ttk.Frame(page, style="Card.TFrame")
        folder.pack(fill="x")
        ttk.Entry(folder, textvariable=self.out_dir).pack(side="left", fill="x", expand=True)
        self._button(folder, "Browse", self.browse_out).pack(side="left", padx=(8, 0))
        write_row = ttk.Frame(page, style="Card.TFrame")
        write_row.pack(fill="x", pady=(12, 0))
        self._button(write_row, "Write .strm files", self.write_files, accent=True).pack(side="left")
        self._button(write_row, "View plan", self.show_plan).pack(side="left", padx=(8, 0))
        self._button(write_row, "Save report", self.save_report).pack(side="left", padx=(8, 0))
        self._hint(
            page,
            "Write .strm files asks before it changes the folder. Matching files stay, new streams are added, and changed addresses are replaced. Other files in the folder are left alone. View plan shows what will be kept and left out.",
            pady=(10, 0),
        )

    def _text(self, parent: tk.Misc, height: int) -> tk.Text:
        box = tk.Text(
            parent,
            height=height,
            bg=FIELD,
            fg=FG,
            insertbackground=FG,
            selectbackground=SELECT,
            selectforeground=FG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            font=("Segoe UI", 10),
            wrap="word",
            padx=8,
            pady=6,
            borderwidth=0,
        )
        box.bind("<MouseWheel>", self._wheel_widget)
        return box

    def _labeled_entry(self, parent: ttk.Frame, label: str, variable: tk.StringVar, placeholder: str) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").pack(anchor="w")
        ttk.Entry(parent, textvariable=variable).pack(fill="x", pady=(4, 2))
        self._hint(parent, placeholder, pady=(0, 6))

    def show_plan(self) -> None:
        if not self.planned:
            self._set_error("Load a playlist to see the plan.")
            return
        self._set_error("")
        if self.plan_window is not None and self.plan_window.winfo_exists():
            self.fill_tree()
            self.plan_window.deiconify()
            self.plan_window.lift()
            self.plan_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.plan_window = window
        window.title("NimboVoD plan")
        window.configure(bg=CANVAS)
        window.geometry("900x640")
        window.minsize(640, 420)
        window.transient(self)
        window.after_idle(lambda window=window: use_dark_titlebar(window))
        tk.Frame(window, bg=ACCENT, height=2).pack(fill="x")
        body = tk.Frame(window, bg=CANVAS)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        tk.Label(body, text="Plan", bg=CANVAS, fg=FG, font=("Segoe UI", 16)).pack(anchor="w")
        summary = tk.Label(
            body,
            textvariable=self.summary,
            bg=CANVAS,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=820,
        )
        summary.pack(anchor="w", pady=(4, 0))

        def _fit_summary(event: tk.Event) -> None:
            if event.widget is window:
                summary.configure(wraplength=max(280, event.width - 48))

        window.bind("<Configure>", _fit_summary)
        filters = tk.Frame(body, bg=CANVAS)
        filters.pack(fill="x", pady=(12, 10))
        ttk.Combobox(
            filters,
            textvariable=self.filter_mode,
            values=("All", "New files", "Left out"),
            state="readonly",
            width=12,
        ).pack(side="left")
        ttk.Entry(filters, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=(8, 0))
        table = tk.Frame(body, bg=LINE, padx=1, pady=1)
        table.pack(fill="both", expand=True)
        frame = tk.Frame(table, bg=FIELD)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            frame,
            columns=("title", "path", "plan", "reason"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("title", text="Title")
        self.tree.heading("path", text="File")
        self.tree.heading("plan", text="Plan")
        self.tree.heading("reason", text="Reason")
        self.tree.column("title", width=200, anchor="w")
        self.tree.column("path", width=280, anchor="w")
        self.tree.column("plan", width=90, anchor="w")
        self.tree.column("reason", width=260, anchor="w")
        self.tree.tag_configure("keep", foreground=FG)
        self.tree.tag_configure("drop", foreground=MUTED)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.fill_tree()
        window.protocol("WM_DELETE_WINDOW", self._close_plan)

    def _close_plan(self) -> None:
        if self.plan_window is not None and self.plan_window.winfo_exists():
            self.plan_window.destroy()
        self.plan_window = None
        if hasattr(self, "tree"):
            del self.tree

    def _wheel_form(self, event: tk.Event) -> str:
        # One wheel flick can deliver dozens of events. Apply them together so
        # the form is redrawn once per frame instead of once per event.
        target = self._canvas_for_widget(getattr(event, "widget", None))
        if target is not None:
            self._wheel_target = target
        self._wheel_pending += -event.delta / 120.0 * 72.0
        if self._wheel_job is None:
            self._wheel_job = self.after(16, self._apply_form_wheel)
        return "break"

    def _apply_form_wheel(self) -> None:
        self._wheel_job = None
        if not self.winfo_exists():
            return
        pixels = int(self._wheel_pending)
        if pixels == 0:
            return
        self._wheel_pending -= pixels
        canvas = self._wheel_target if self._wheel_target is not None else self.form_canvas
        if canvas is not None:
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(pixels, "units")
            except tk.TclError:
                pass
        if abs(self._wheel_pending) >= 1:
            self._wheel_job = self.after(16, self._apply_form_wheel)

    def _wheel_widget(self, event: tk.Event) -> str:
        widget = event.widget
        try:
            top, bottom = widget.yview()
        except tk.TclError:
            return self._wheel_form(event)
        # A playlist box only uses the wheel when it has lines outside the view.
        # Otherwise the page scrolls, including when the pointer is over that box.
        if event.delta > 0:
            can_scroll = top > 0.001
        elif event.delta < 0:
            can_scroll = bottom < 0.999
        else:
            can_scroll = False
        if not can_scroll:
            return self._wheel_form(event)
        steps = int(-event.delta / 120)
        if steps == 0:
            steps = -1 if event.delta > 0 else 1
        widget.yview_scroll(steps, "units")
        return "break"

    def _bind_options(self) -> None:
        for variable in (self.layout, self.strip_quality, self.drop_duplicate_strm, self.use_years, self.match_filenames):
            variable.trace_add("write", self._on_option)
        self.run_at_startup.trace_add("write", self._on_startup_toggle)
        for variable in (self.auto_pull_playlists, self.auto_pull_emby, self.auto_apply_strm):
            variable.trace_add("write", self._on_auto_option)
        self.refresh_minutes.trace_add("write", self._on_refresh_minutes)
        self.out_dir.trace_add("write", self._schedule_tags)
        self.extra_tags.bind("<KeyRelease>", self._schedule_tags)
        self.extra_tags.bind("<<Paste>>", self._schedule_tags)
        self.filter_mode.trace_add("write", lambda *_args: self.fill_tree())
        self.query.trace_add("write", lambda *_args: self.fill_tree())

    def _schedule_tags(self, *_args: object) -> None:
        if not self._ready:
            return
        if self._tag_job is not None:
            self.after_cancel(self._tag_job)
        self._tag_job = self.after(200, self._apply_tag_edit)

    def _apply_tag_edit(self) -> None:
        self._tag_job = None
        if not self._ready:
            return
        self.build_plan()
        self._save_settings()

    def _on_option(self, *_args) -> None:
        if not self._ready:
            return
        self._update_layout_hint()
        self.build_plan()
        self._save_settings()

    def _on_auto_option(self, *_args: object) -> None:
        if not self._ready:
            return
        self._save_settings()
        self._schedule_refresh()

    def _on_refresh_minutes(self, *_args: object) -> None:
        if not self._ready:
            return
        self._save_settings()
        self._schedule_refresh()

    def _on_startup_toggle(self, *_args: object) -> None:
        if not self._ready:
            return
        message = apply_startup_registration(bool(self.run_at_startup.get()))
        if message:
            self._set_error(message)
        self._save_settings()

    def _sync_startup_registration(self) -> None:
        if not self.run_at_startup.get():
            return
        message = apply_startup_registration(True)
        if message:
            self._set_error(message)

    def _startup_auto(self) -> None:
        if os.environ.get("NIMBOVOD_SETTINGS", "").strip():
            return
        # Paint the window before refreshing. A restart can launch NimboVoD
        # before the network is up, and a pull at that moment looks frozen.
        if self._startup_job is not None:
            self.after_cancel(self._startup_job)
        self._startup_job = self.after(1200, self._begin_startup_auto)

    def _begin_startup_auto(self) -> None:
        self._startup_job = None
        if os.environ.get("NIMBOVOD_SETTINGS", "").strip():
            return
        if not self.winfo_exists() or self._busy or self._auto_running or self._auto_queue:
            return
        steps = auto_steps(
            bool(self.auto_pull_playlists.get()),
            bool(self.auto_pull_emby.get()),
            bool(self.auto_apply_strm.get()),
        )
        if not steps:
            self._schedule_refresh()
            return
        snapshot = self._auto_snapshot(steps)
        self._auto_running = True
        self._busy = True
        self._set_busy(True)
        self._auto_queue = list(steps)
        self._auto_note = ""
        self._set_error("")
        if snapshot["hosts"]:
            self.status.set("Waiting for the network…")
        elif steps[0] == "playlists":
            self.status.set("Updating playlists…")
        elif steps[0] == "emby":
            self.status.set("Updating Emby library…")
        else:
            self.status.set("Updating .strm files…")
        threading.Thread(target=self._auto_pipeline, args=(snapshot,), daemon=True).start()

    def _auto_snapshot(self, steps: list[str]) -> dict:
        """Copy the inputs a refresh needs. The worker must not read the widgets."""
        return {
            "steps": list(steps),
            "urls": [link for link in self.links if link.strip()] if "playlists" in steps else [],
            "files": list(self.playlist_files) if "playlists" in steps else [],
            "emby": self.emby.get().strip(),
            "api_key": self.api_key.get().strip(),
            "user_id": self.user_id.get().strip(),
            "libraries": self._libraries_for_fetch(),
            "out": self.out_dir.get().strip(),
            "hosts": self._refresh_hosts(steps),
            "options": self._plan_options(),
        }

    def _poll_ui_calls(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_calls.get_nowait()
                fn()
        except queue.Empty:
            pass
        try:
            self._apply_tray_requests()
        except Exception:
            pass
        if self.winfo_exists():
            self.after(30, self._poll_ui_calls)

    def _apply_tray_requests(self) -> None:
        if not self._tray_requests:
            return
        pending = self._tray_requests
        self._tray_requests = []
        for action in pending:
            if action == "minimize":
                self._minimize_to_tray()
            elif action == "open":
                self._restore_from_tray()
            elif action == "menu":
                self._show_tray_menu()

    def _call_on_ui(self, fn):
        """Run fn on the window thread and wait until it has finished."""
        if threading.current_thread() is self._ui_thread:
            return fn()
        done = threading.Event()
        box: dict = {}

        def wrapper() -> None:
            try:
                if not self.winfo_exists():
                    box["closed"] = True
                else:
                    box["result"] = fn()
            except BaseException as exc:
                box["error"] = exc
            finally:
                done.set()

        self._ui_calls.put(wrapper)
        if not done.wait(120):
            raise TimeoutError("The window did not finish the refresh step.")
        if box.get("closed"):
            return None
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _hosts_ready(self, hosts: list[str]) -> bool:
        for _attempt in range(12):
            open_window = self._call_on_ui(self.winfo_exists)
            if not open_window:
                return False
            if all(host_resolves(host, 3) for host in hosts):
                return True
            time.sleep(5)
        return False

    def _auto_pipeline(self, snapshot: dict) -> None:
        """Run one refresh. Playlists finish, then Emby, then the .strm folder."""
        steps: list[str] = list(snapshot["steps"])
        note = ""
        playlists_ok = "playlists" not in steps
        emby_ok = "emby" not in steps
        try:
            hosts = snapshot["hosts"]
            if hosts and not self._hosts_ready(hosts):
                self._call_on_ui(lambda: self.status.set("Network is not ready. Trying the refresh anyway…"))
            if "playlists" in steps:
                loaded, failures, total = self._fetch_auto_playlists(snapshot["files"], snapshot["urls"])
                if not loaded:
                    playlists_ok = False
                    note = " ".join(failures) or "No playlists could be loaded."
                    self._call_on_ui(lambda note=note: self._set_error(note))
                else:
                    playlists_ok = True
                    if failures:
                        note = f"Loaded {len(loaded)} of {total}. " + " ".join(failures)

                    def apply_sources(loaded=loaded, note=note) -> None:
                        self.sources = loaded
                        self._refresh_sources()
                        self._auto_note = note
                        self._set_error(note)

                    self._call_on_ui(apply_sources)
                    self._call_on_ui(lambda: self.status.set("Playlists updated."))
            if "emby" in steps and self._call_on_ui(self.winfo_exists):
                self._call_on_ui(lambda: self.status.set("Updating Emby library…"))
                if not snapshot["emby"] or not snapshot["api_key"]:
                    emby_ok = False
                    message = "Enter the Emby server address and API key before automatic library updates."
                    self._call_on_ui(lambda message=message: self._set_error(message))
                elif snapshot.get("libraries") == []:
                    emby_ok = False
                    message = "Select at least one Emby library."
                    self._call_on_ui(lambda message=message: self._set_error(message))
                else:
                    try:
                        items = self.cleaner.fetch_emby(
                            snapshot["emby"],
                            snapshot["api_key"],
                            snapshot["user_id"],
                            self._emby_progress,
                            snapshot.get("libraries"),
                        )
                    except BaseException as exc:
                        emby_ok = False
                        message = explain(exc)
                        self._call_on_ui(lambda message=message: self._set_error(message))
                    else:
                        if not items:
                            emby_ok = False
                            message = "Emby returned no movies, shows, or episodes."
                            self._call_on_ui(lambda message=message: self._set_error(message))
                        else:
                            emby_ok = True

                            def apply_emby(items=items, note=note) -> None:
                                self.library = items
                                suffix = self._library_limit_suffix(len(items))
                                self.library_label = f"Emby · {len(items):,} items{suffix}"
                                chosen = snapshot.get("libraries") or []
                                if chosen:
                                    self.library_label += " · " + ", ".join(
                                        item["name"] for item in chosen if item.get("name")
                                    )
                                self._show_library()
                                self._auto_note = note
                                self._set_error(note)
                                self._save_settings()
                                self.status.set("Emby library updated.")

                            self._call_on_ui(apply_emby)
            if not self._call_on_ui(self.winfo_exists):
                return
            if playlists_ok and emby_ok and "apply" in steps:
                self._call_on_ui(lambda: self.status.set("Updating .strm files…"))
            if playlists_ok and emby_ok:
                planned = self._plan_snapshot(snapshot["options"])
                self._call_on_ui(lambda planned=planned: self._show_planned(planned))
            else:
                planned = None
            if "apply" not in steps or planned is None:
                return
            if not planned:
                self._call_on_ui(
                    lambda: self._set_error("No playlist is loaded, so the .strm folder was not changed.")
                )
                return
            folder = self._folder_from_text(snapshot["out"])
            if folder is None:
                self._call_on_ui(
                    lambda: self._set_error("Choose the folder where the .strm files should be written.")
                )
                return
            kept = sum(1 for row in planned if row["disposition"] == "keep")
            folder.mkdir(parents=True, exist_ok=True)
            counts = self.cleaner.write_plan(folder, planned, True, self._strm_progress)

            def finish_write(counts=counts, kept=kept, folder=folder, note=note) -> None:
                self._auto_note = note
                self._set_error(note)
                self.status.set(self._write_status(folder, counts, kept))
                self._save_settings()

            self._call_on_ui(finish_write)
        except BaseException as exc:
            message = explain(exc)
            try:
                self._call_on_ui(lambda message=message: self._set_error(message))
            except Exception:
                pass
        finally:
            def finish() -> None:
                current = self.status.get()
                if current.startswith("Updating") or current.startswith("Waiting") or current.startswith("Network"):
                    self.status.set("Ready")
                self._busy = False
                self._set_busy(False)
                self._auto_queue = []
                self._auto_running = False
                self._schedule_refresh()

            try:
                self._call_on_ui(finish)
            except Exception:
                self._busy = False
                self._auto_queue = []
                self._auto_running = False

    def _fetch_auto_playlists(self, files: list[str], urls: list[str]) -> tuple[list[dict], list[str], int]:
        loaded: list[dict] = []
        failures: list[str] = []
        total = len(files) + len(urls)
        if total == 0:
            return loaded, ["Add a playlist link or playlist file before automatic loading."], 0
        number = 0
        for path in files:
            number += 1
            current = number
            label = Path(path).name
            self._post_status(f"Updating playlist {current} of {total}… {label}")
            try:
                loaded.append(self._read_source(path, "file", on_progress=self._playlist_progress(current, total, label)))
            except BaseException as exc:
                failures.append(f"{label}: {explain(exc)}")
        for url in urls:
            number += 1
            current = number
            label = playlist_label(url)
            self._post_status(f"Updating playlist {current} of {total}… {label}")
            try:
                loaded.append(self._read_source(url, "url", on_progress=self._playlist_progress(current, total, label)))
            except BaseException as exc:
                failures.append(f"{label}: {explain(exc)}")
        return loaded, failures, total

    def _playlist_progress(self, current: int, total: int, label: str):
        last = {"at": 0.0}

        def progress(nbytes: int, current: int = current, total: int = total, label: str = label) -> None:
            now = time.time()
            if now - last["at"] < 0.25:
                return
            last["at"] = now
            self._post_status(f"Updating playlist {current} of {total}… {label} · {nbytes / 1048576:.1f} MB")

        return progress

    def _plan_snapshot(self, options: dict) -> list[dict]:
        copied = self._call_on_ui(
            lambda: (
                [dict(source, entries=list(source["entries"])) for source in self.sources],
                None if self.library is None else list(self.library),
            )
        )
        if not copied:
            return []
        sources, library = copied
        entries = [entry for source in sources for entry in source["entries"]]
        if not entries:
            return []
        return self._entries_plan(entries, library, options)

    def _entries_plan(self, entries: list[dict], library: list[dict] | None, options: dict) -> list[dict]:
        planned = self.cleaner.plan_entries(entries, library, options)
        for entry, row in zip(entries, planned):
            row["group"] = entry.get("group") or ""
        return planned

    def _show_planned(self, planned: list[dict]) -> None:
        self.planned = planned
        self.fill_tree()

    def _folder_from_text(self, raw: str) -> Path | None:
        text = raw.strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            path = app_dir() / path
        return path

    def _refresh_hosts(self, steps: list[str]) -> list[str]:
        hosts: list[str] = []
        if "playlists" in steps:
            for link in self.links:
                host = urlparse(link.strip()).hostname
                if host and host not in hosts:
                    hosts.append(host)
        if "emby" in steps:
            host = urlparse(self.emby.get().strip()).hostname
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    def _schedule_refresh(self) -> None:
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        if os.environ.get("NIMBOVOD_SETTINGS", "").strip():
            return
        minutes = refresh_minutes(self.refresh_minutes.get())
        if minutes < 1:
            return
        if not (
            self.auto_pull_playlists.get()
            or self.auto_pull_emby.get()
            or self.auto_apply_strm.get()
        ):
            return
        self._refresh_job = self.after(minutes * 60 * 1000, self._refresh_tick)

    def _refresh_tick(self) -> None:
        self._refresh_job = None
        if not self.winfo_exists():
            return
        if self._busy or self._auto_running or self._auto_queue:
            self._refresh_job = self.after(60_000, self._refresh_tick)
            return
        self._startup_auto()

    def _update_layout_hint(self) -> None:
        self.layout_hint.set(LAYOUT_HINTS.get(self.layout.get(), LAYOUT_HINTS["group"]))

    def _toggle_key(self) -> None:
        self.api_entry.configure(show="" if self.show_key.get() else "•")

    def _set_error(self, message: str) -> None:
        if message:
            self.error_label.configure(text=message)
            if not self.error_host.winfo_manager():
                self.error_host.pack(fill="x", padx=18, pady=(0, 8), before=self.form_host)
        elif self.error_host.winfo_manager():
            self.error_host.pack_forget()

    def _set_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        for button in self._buttons:
            button.state(state)

    def _run(self, label: str, work, done) -> None:
        if self._busy or self._auto_running:
            return
        self._busy = True
        self._set_busy(True)
        self._set_error("")
        self.status.set(label)

        def target() -> None:
            try:
                result = work()
            except BaseException as exc:
                message = explain(exc)
                self.after(0, lambda message=message: self._finish_error(message))
            else:
                self.after(0, lambda result=result: self._finish_ok(done, result))

        threading.Thread(target=target, daemon=True).start()

    def _finish_error(self, message: str) -> None:
        self._busy = False
        self._set_busy(False)
        self._set_error(message)
        self.status.set("Ready")
        if not self._auto_running:
            self._schedule_refresh()

    def _finish_ok(self, done, result) -> None:
        try:
            done(result)
        except BaseException as exc:
            self._set_error(explain(exc))
            self.status.set("Ready")
        finally:
            self._busy = False
            self._set_busy(False)

    def _refresh_links(self) -> None:
        self.link_list.delete(0, "end")
        for link in self.links:
            self.link_list.insert("end", link)

    def _refresh_sources(self) -> None:
        self.source_list.delete(0, "end")
        for source in self.sources:
            self.source_list.insert("end", f"{source['label']}  ({len(source['entries']):,})")

    def _library_limit_suffix(self, count: int) -> str:
        if count >= self.cleaner.ITEM_LIMIT:
            return f" (stopped at {self.cleaner.ITEM_LIMIT:,})"
        return ""

    def _emby_progress(self, done: int, total: int | None, label: str | None = None) -> None:
        prefix = f"{label} · " if label else ""
        if total:
            self._post_status(f"Reading Emby library… {prefix}{done:,} of {total:,}")
        else:
            self._post_status(f"Reading Emby library… {prefix}{done:,}")

    def _library_choice_text(self, item: dict) -> str:
        name = str(item.get("name") or "")
        kind = str(item.get("type") or "")
        same_name = sum(1 for other in self.emby_libraries if other.get("name") == name)
        if kind and same_name > 1:
            return f"{name} ({kind})"
        return name

    def _render_library_choices(self) -> None:
        frame = self.library_choice
        for child in frame.winfo_children():
            child.destroy()
        self._library_vars = []
        if not self.emby_libraries:
            note = ttk.Label(
                frame,
                text="Every library is used until you load the list.",
                style="CardMuted.TLabel",
                wraplength=480,
            )
            note.pack(anchor="w")
            self._wrapping.append(note)
            return
        for item in self.emby_libraries:
            var = tk.IntVar(value=1 if item.get("selected") else 0)
            self._library_vars.append(var)
            check = ttk.Checkbutton(
                frame,
                text=self._library_choice_text(item),
                variable=var,
                command=self._store_library_checks,
                style="Card.TCheckbutton",
            )
            check.pack(anchor="w")
            check.bind("<MouseWheel>", self._wheel_form)

    def _store_library_checks(self) -> None:
        for item, var in zip(self.emby_libraries, self._library_vars):
            item["selected"] = bool(var.get())
        self._save_settings()

    def _libraries_for_fetch(self) -> list[dict] | None:
        if not self.emby_libraries:
            return None
        chosen = [
            {"id": item["id"], "name": item["name"]}
            for item in self.emby_libraries
            if item.get("selected") and item.get("id")
        ]
        return chosen

    def load_emby_libraries(self) -> None:
        base = self.emby.get().strip()
        key = self.api_key.get().strip()
        if not base or not key:
            self._set_error("Enter the Emby server address and API key.")
            return
        previous = {item["id"]: item for item in self.emby_libraries if item.get("id")}

        def work():
            return self.cleaner.list_emby_libraries(base, key)

        def done(rows) -> None:
            merged = []
            for row in rows:
                old = previous.get(row["id"])
                if not previous:
                    selected = True
                elif old is not None:
                    selected = bool(old.get("selected"))
                else:
                    selected = False
                merged.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "type": row.get("type") or "",
                        "selected": selected,
                    }
                )
            self.emby_libraries = merged
            self._render_library_choices()
            self._set_error("")
            self.status.set(f"Found {len(merged)} Emby libraries.")
            self._save_settings()

        self._run("Loading Emby libraries…", work, done)

    def _show_library(self) -> None:
        if self.library is None:
            self.library_status.set("No library loaded. Every stream will be kept.")
        else:
            self.library_status.set(self.library_label or f"{len(self.library):,} titles")

    def add_link(self) -> None:
        added = False
        for line in self.link_entry.get().splitlines():
            text = line.strip()
            if text and text not in self.links:
                self.links.append(text)
                added = True
        if not added and not self.link_entry.get().strip():
            self._set_error("Enter a playlist link first.")
            return
        self.link_entry.set("")
        self._refresh_links()
        self._set_error("")
        self._save_settings()

    def remove_link(self) -> None:
        selected = list(self.link_list.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.links[index]
        self._refresh_links()
        self._save_settings()

    def _post_status(self, text: str) -> None:
        """Update the status line without waiting. Safe to call from a worker."""
        if threading.current_thread() is self._ui_thread:
            if self.winfo_exists():
                self.status.set(text)
            return
        if self._ui_calls.qsize() > 8:
            return

        def apply(text: str = text) -> None:
            if self.winfo_exists():
                self.status.set(text)

        self._ui_calls.put(apply)

    def _read_local_playlist(self, source: str) -> tuple[str, str, str]:
        outcome: dict = {}

        def work() -> None:
            try:
                path = Path(source)
                if not path.is_file():
                    raise ValueError(f"Cannot find {source}")
                if path.stat().st_size > MAX_PLAYLIST_BYTES:
                    raise ValueError(f"{path.name} is over 80 MB.")
                outcome["text"] = path.read_text(encoding="utf-8", errors="replace")
                outcome["label"] = path.name
                outcome["path"] = str(path.resolve())
            except BaseException as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(30)
        if thread.is_alive():
            raise TimeoutError(f"{Path(source).name} took too long.")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["text"], outcome["label"], outcome["path"]

    def _read_source(self, source: str, kind: str, on_progress=None) -> dict:
        saved_path = ""
        if source.startswith("http://") or source.startswith("https://"):
            text = self.cleaner.fetch_text(source, on_progress=on_progress)
            label = playlist_label(source)
        else:
            text, label, saved_path = self._read_local_playlist(source)
        if not looks_like_playlist(text):
            raise ValueError("not an M3U playlist")
        entries = self.cleaner.parse_m3u(text)
        if not entries:
            raise ValueError("no streams found")
        payload = {"kind": kind, "label": label, "entries": entries}
        if kind == "file":
            payload["path"] = saved_path
        return payload

    def _remember_playlist_file(self, path: str) -> None:
        if path and path not in self.playlist_files:
            self.playlist_files.append(path)

    def add_files(self) -> None:
        selected = filedialog.askopenfilenames(
            parent=self,
            title="Add playlist files",
            filetypes=[("M3U playlists", "*.m3u *.m3u8 *.txt"), ("All files", "*.*")],
        )
        if not selected:
            return
        paths = list(selected)

        def work():
            loaded = []
            failures = []
            for path in paths:
                try:
                    loaded.append(self._read_source(path, "file"))
                except BaseException as exc:
                    failures.append(f"{Path(path).name}: {explain(exc)}")
            return loaded, failures

        def done(result) -> None:
            loaded, failures = result
            if loaded:
                kept = [source for source in self.sources if source["kind"] != "sample"]
                self.sources = kept + loaded
                for source in loaded:
                    path = source.get("path")
                    if isinstance(path, str):
                        self._remember_playlist_file(path)
                self._refresh_sources()
                self.build_plan()
                self._save_settings()
            if failures:
                self._set_error(" ".join(failures))
            else:
                self._set_error("")
            count = len(loaded)
            self.status.set(f"Loaded {count} playlist file{'s' if count != 1 else ''}." if count else "Ready")

        self._run("Reading playlist files…", work, done)

    def load_links(self) -> None:
        self.add_link() if self.link_entry.get().strip() else None
        urls = [link for link in self.links if link.strip()]
        if not urls:
            self._set_error("Add at least one playlist link.")
            return
        options = self._plan_options()
        library = None if self.library is None else list(self.library)
        total = len(urls)

        def work():
            loaded = []
            failures = []
            for index, url in enumerate(urls, start=1):
                label = playlist_label(url)
                self._post_status(f"Loading playlist {index} of {total}… {label}")
                last = {"at": 0.0}

                def progress(nbytes: int, index: int = index, total: int = total, label: str = label) -> None:
                    now = time.time()
                    if now - last["at"] < 0.25:
                        return
                    last["at"] = now
                    self._post_status(f"Loading playlist {index} of {total}… {label} · {nbytes / 1048576:.1f} MB")

                try:
                    loaded.append(self._read_source(url, "url", on_progress=progress))
                except BaseException as exc:
                    failures.append(f"{label}: {explain(exc)}")
            entries = [entry for source in loaded for entry in source["entries"]]
            planned: list[dict] = []
            if entries:
                self._post_status("Checking playlists…")
                planned = self._entries_plan(entries, library, options)
            return loaded, failures, planned

        def done(result) -> None:
            loaded, failures, planned = result
            if not loaded:
                self._set_error(" ".join(failures) or "No playlists could be loaded.")
                self.status.set("Ready")
                return
            files = [source for source in self.sources if source["kind"] == "file"]
            self.sources = files + loaded
            self._refresh_sources()
            self._show_planned(planned)
            if failures:
                self._set_error(f"Loaded {len(loaded)} of {total}. " + " ".join(failures))
            else:
                self._set_error("")
            self.status.set(f"Loaded {len(loaded)} playlist link{'s' if len(loaded) != 1 else ''}.")

        self._run(f"Loading playlist 1 of {total}…", work, done)

    def remove_source(self) -> None:
        selected = list(self.source_list.curselection())
        if not selected:
            return
        for index in reversed(selected):
            source = self.sources[index]
            path = source.get("path")
            if isinstance(path, str) and path in self.playlist_files:
                self.playlist_files.remove(path)
            del self.sources[index]
        self._refresh_sources()
        self.build_plan()
        self._save_settings()

    def pull_emby(self) -> None:
        base = self.emby.get().strip()
        key = self.api_key.get().strip()
        user = self.user_id.get().strip()
        if not base or not key:
            self._set_error("Enter the Emby server address and API key.")
            return
        libraries = self._libraries_for_fetch()
        if libraries == []:
            self._set_error("Select at least one Emby library.")
            return

        def work():
            return self.cleaner.fetch_emby(base, key, user, self._emby_progress, libraries)

        def done(items) -> None:
            if not items:
                self._set_error("Emby returned no movies, shows, or episodes.")
                self.status.set("Ready")
                return
            self.library = items
            suffix = self._library_limit_suffix(len(items))
            self.library_label = f"Emby · {len(items):,} items{suffix}"
            if libraries:
                self.library_label += " · " + ", ".join(item["name"] for item in libraries)
            self._show_library()
            self.build_plan()
            self._set_error("")
            self.status.set(self.library_label)
            self._save_settings()

        self._run("Reading Emby library…", work, done)

    def upload_library(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Upload library export",
            filetypes=[("Library export", "*.json *.txt"), ("All files", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            if path.stat().st_size > MAX_PLAYLIST_BYTES:
                raise ValueError(f"{path.name} is over 80 MB.")
            items = parse_library_text(path.read_text(encoding="utf-8", errors="replace"), self.cleaner)
        except BaseException as exc:
            self._set_error(explain(exc))
            return
        self.library = items
        self.library_label = f"{path.name} · {len(items):,} titles"
        self._show_library()
        self.build_plan()
        self._set_error("")
        self.status.set(self.library_label)

    def match_pasted(self) -> None:
        try:
            items = parse_library_text(self.paste.get("1.0", "end"), self.cleaner)
        except BaseException as exc:
            self._set_error(explain(exc))
            return
        self.library = items
        self.library_label = f"Pasted titles · {len(items):,}"
        self._show_library()
        self.build_plan()
        self._set_error("")
        self.status.set(self.library_label)

    def clear_library(self) -> None:
        self.library = None
        self.library_label = ""
        self._show_library()
        self.build_plan()
        self.status.set("Library cleared.")

    def browse_out(self) -> None:
        current = self.out_dir.get().strip()
        initial = current if current and Path(current).is_dir() else str(app_dir())
        selected = filedialog.askdirectory(parent=self, title="Save .strm files", initialdir=initial, mustexist=False)
        if selected:
            self.out_dir.set(selected)
            self._save_settings()

    def load_sample(self) -> None:
        entries = self.cleaner.parse_m3u(SAMPLE_PLAYLIST)
        self.sources = [{"kind": "sample", "label": "Sample playlist", "entries": entries}]
        self.library = [dict(item) for item in SAMPLE_LIBRARY]
        self.library_label = "Sample Emby library"
        self._refresh_sources()
        self._show_library()
        self.filter_mode.set("All")
        self.query.set("")
        self.build_plan()
        self._set_error("")
        self.status.set("Sample loaded. Dune (1984) stays. The Matrix (1999) and The Office are left out.")

    def _plan_options(self) -> dict:
        layout = self.layout.get()
        if layout not in {"group", "flat", "library"}:
            layout = "group"
        return {
            "layout": layout,
            "strip_quality": bool(self.strip_quality.get()),
            "use_years": bool(self.use_years.get()),
            "match_filenames": bool(self.match_filenames.get()),
            "extra_tags": self.extra_tags.get("1.0", "end"),
            "drop_duplicate_strm": bool(self.drop_duplicate_strm.get()),
            "out": self._plan_out(),
        }

    def _plan_out(self) -> str:
        raw = self.out_dir.get().strip()
        if not raw:
            return ""
        path = Path(raw)
        if not path.is_absolute():
            path = app_dir() / path
        return str(path)

    def build_plan(self) -> None:
        entries = [entry for source in self.sources for entry in source["entries"]]
        if not entries:
            self.planned = []
            self.fill_tree()
            return
        planned = self.cleaner.plan_entries(entries, self.library, self._plan_options())
        for entry, row in zip(entries, planned):
            row["group"] = entry.get("group") or ""
        self.planned = planned
        self.fill_tree()

    def _visible_rows(self) -> list[dict]:
        mode = self.filter_mode.get()
        needle = self.query.get().strip().lower()
        rows = []
        for row in self.planned:
            if mode == "New files" and row["disposition"] != "keep":
                continue
            if mode == "Left out" and row["disposition"] == "keep":
                continue
            if needle:
                haystack = " ".join(
                    [row.get("title", ""), row.get("path", ""), row.get("reason", ""), row.get("group", "")]
                ).lower()
                if needle not in haystack:
                    continue
            rows.append(row)
        return rows

    def fill_tree(self) -> None:
        visible = self._visible_rows()
        self._update_summary(len(visible))
        tree = getattr(self, "tree", None)
        if tree is None or not tree.winfo_exists():
            return
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(visible[:TREE_LIMIT]):
            plan = "New" if row["disposition"] == "keep" else "Leave out"
            tag = "keep" if row["disposition"] == "keep" else "drop"
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(row["title"], row["path"], plan, row.get("reason", "")),
                tags=(tag,),
            )

    def _update_summary(self, shown: int) -> None:
        if not self.planned:
            self.summary.set("Load a playlist to see the plan.")
            return
        kept = sum(1 for row in self.planned if row["disposition"] == "keep")
        dropped = len(self.planned) - kept
        text = f"{len(self.planned):,} playlist entries · {kept:,} new · {dropped:,} left out"
        if self.library is None:
            text += " · no Emby library loaded"
        elif self.library_label:
            text += f" · {self.library_label}"
        if shown != len(self.planned):
            text += f" · {shown:,} shown"
        if shown > TREE_LIMIT:
            text += f" · first {TREE_LIMIT:,} rows"
        self.summary.set(text)

    def _out_path(self) -> Path | None:
        raw = self.out_dir.get().strip()
        if not raw:
            self._set_error("Choose the folder where the .strm files should be written.")
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = app_dir() / path
        return path

    def _source_label(self) -> str:
        if not self.sources:
            return ""
        if len(self.sources) <= 2:
            return " · ".join(source["label"] for source in self.sources)
        return f"{len(self.sources)} playlists"

    def _removal_count(self, folder: Path, planned: list[dict] | None = None) -> int:
        rows = self.planned if planned is None else planned
        if not self.cleaner.directory_reachable(folder):
            return 0
        kept_paths = {
            row["path"].replace("\\", "/").lower() for row in rows if row["disposition"] == "keep"
        }
        removed = 0
        seen: set[str] = set()
        for row in rows:
            if row["disposition"] == "keep":
                continue
            key = row["path"].replace("\\", "/").lower()
            if key in kept_paths or key in seen:
                continue
            seen.add(key)
            target = self.cleaner.inside(folder, row["path"])
            if target.is_file():
                removed += 1
        return removed

    def _write_status(self, folder: Path, counts: object, fallback_kept: int) -> str:
        if not isinstance(counts, dict):
            return f"Wrote {fallback_kept:,} .strm files to {folder}."
        added = int(counts.get("added") or 0)
        updated = int(counts.get("updated") or 0)
        unchanged = int(counts.get("unchanged") or 0)
        removed = int(counts.get("removed") or 0)
        if added == 0 and updated == 0 and removed == 0:
            return f".strm folder already matches. Left {unchanged:,} files unchanged in {folder}."
        parts: list[str] = []
        if added:
            parts.append(f"added {added:,}")
        if updated:
            parts.append(f"updated {updated:,}")
        if unchanged:
            parts.append(f"left {unchanged:,} unchanged")
        if parts:
            summary = ", ".join(parts)
            summary = summary[0].upper() + summary[1:] + f" in {folder}."
        else:
            summary = f"Updated {folder}."
        if removed:
            summary += f" Removed {removed:,} files that were already in the folder."
        return summary

    def _strm_progress(self, done: int, total: int) -> None:
        self._post_status(f"Checking .strm files… {done:,} of {total:,}")

    def write_files(self) -> None:
        if self._busy:
            return
        if not self.planned:
            self._set_error("Load a playlist first.")
            return
        try:
            folder = self._out_path()
            if folder is None:
                return
            removed = self._removal_count(folder)
        except BaseException as exc:
            self._set_error(explain(exc))
            return
        message = (
            f"Update the .strm files in\n{folder}?\n\n"
            "Files that already match the playlist stay as they are. "
            "New streams are added, and files whose address changed are replaced."
        )
        if removed:
            message += (
                f"\n\n{removed:,} existing files will be removed because they are already in Emby "
                "or are duplicate streams."
            )
        else:
            message += "\n\nNo planned files will be removed."
        if self.library is None:
            warning = "No Emby library is loaded, so every stream is treated as new."
            if self.emby.get().strip() or self.api_key.get().strip():
                warning = "The Emby library has not been pulled, so nothing will be left out as a duplicate."
            message += f"\n\n{warning}"
        if not messagebox.askyesno("Write .strm files", message, parent=self):
            return
        self._start_write(folder)

    def _start_write(self, folder: Path) -> None:
        planned = self.planned
        kept = sum(1 for row in self.planned if row["disposition"] == "keep")

        def work():
            folder.mkdir(parents=True, exist_ok=True)
            return self.cleaner.write_plan(folder, planned, True, self._strm_progress)

        def done(result) -> None:
            if self._auto_note:
                self._set_error(self._auto_note)
            else:
                self._set_error("")
            self.status.set(self._write_status(folder, result, kept))
            self._save_settings()

        self._run("Writing .strm files…", work, done)

    def save_report(self) -> None:
        if not self.planned:
            self._set_error("Load a playlist first.")
            return
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Save report",
            defaultextension=".txt",
            initialfile="nimbovod-report.txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not selected:
            return
        text = report_text(self.planned, self._source_label(), self.library is not None)
        Path(selected).write_text(text, encoding="utf-8")
        self._set_error("")
        self.status.set(f"Saved the report to {selected}")

    def _collect_settings(self) -> dict:
        return {
            "links": self.links,
            "out": self.out_dir.get(),
            "emby": self.emby.get(),
            "apiKey": self.api_key.get(),
            "userId": self.user_id.get(),
            "layout": self.layout.get(),
            "stripQuality": bool(self.strip_quality.get()),
            "useYears": bool(self.use_years.get()),
            "matchFilenames": bool(self.match_filenames.get()),
            "dropDuplicateStrm": bool(self.drop_duplicate_strm.get()),
            "extraTags": self.extra_tags.get("1.0", "end").strip(),
            "playlistFiles": list(self.playlist_files),
            "runAtStartup": bool(self.run_at_startup.get()),
            "autoPullPlaylists": bool(self.auto_pull_playlists.get()),
            "autoPullEmby": bool(self.auto_pull_emby.get()),
            "autoApplyStrm": bool(self.auto_apply_strm.get()),
            "refreshMinutes": refresh_minutes(self.refresh_minutes.get()),
            "embyLibraries": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "type": item.get("type") or "",
                    "selected": bool(item.get("selected")),
                }
                for item in self.emby_libraries
                if item.get("id") and item.get("name")
            ],
            "geometry": self.geometry(),
        }

    def _save_settings(self) -> None:
        if not self._ready:
            return
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._collect_settings(), indent=2), encoding="utf-8")

    def _load_settings(self) -> None:
        path = settings_path()
        if not path.is_file():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(saved, dict):
            return
        links = saved.get("links")
        if isinstance(links, list):
            self.links = [item.strip() for item in links if isinstance(item, str) and item.strip()]
            self._refresh_links()
        for key, variable in (
            ("out", self.out_dir),
            ("emby", self.emby),
            ("apiKey", self.api_key),
            ("userId", self.user_id),
        ):
            if isinstance(saved.get(key), str):
                variable.set(saved[key])
        if saved.get("layout") in {"group", "flat", "library"}:
            self.layout.set(saved["layout"])
        for key, variable in (
            ("stripQuality", self.strip_quality),
            ("useYears", self.use_years),
            ("matchFilenames", self.match_filenames),
            ("dropDuplicateStrm", self.drop_duplicate_strm),
            ("runAtStartup", self.run_at_startup),
            ("autoPullPlaylists", self.auto_pull_playlists),
            ("autoPullEmby", self.auto_pull_emby),
            ("autoApplyStrm", self.auto_apply_strm),
        ):
            if isinstance(saved.get(key), bool):
                variable.set(1 if saved[key] else 0)
        minutes = saved.get("refreshMinutes")
        if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
            self.refresh_minutes.set(str(min(minutes, 10080)))
        files = saved.get("playlistFiles")
        if isinstance(files, list):
            self.playlist_files = []
            for item in files:
                if isinstance(item, str) and item.strip():
                    self._remember_playlist_file(item.strip())
        saved_libraries = saved.get("embyLibraries")
        if isinstance(saved_libraries, list):
            parsed_libraries = []
            for item in saved_libraries:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                name = item.get("name")
                if not isinstance(item_id, str) or not item_id.strip():
                    continue
                if not isinstance(name, str) or not name.strip():
                    continue
                kind = item.get("type")
                parsed_libraries.append(
                    {
                        "id": item_id.strip(),
                        "name": name.strip(),
                        "type": kind if isinstance(kind, str) else "",
                        "selected": item.get("selected") is True,
                    }
                )
            self.emby_libraries = parsed_libraries
        self._render_library_choices()
        extra_tags = saved.get("extraTags")
        if isinstance(extra_tags, str) and extra_tags.strip():
            self.extra_tags.insert("1.0", extra_tags.strip())
        elif isinstance(extra_tags, list):
            lines = [item.strip() for item in extra_tags if isinstance(item, str) and item.strip()]
            if lines:
                self.extra_tags.insert("1.0", "\n".join(lines))
        geometry = saved.get("geometry")
        if isinstance(geometry, str) and "x" in geometry:
            self.geometry(geometry)

    def _on_close(self) -> None:
        if self._wheel_job is not None:
            self.after_cancel(self._wheel_job)
            self._wheel_job = None
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self._startup_job is not None:
            self.after_cancel(self._startup_job)
            self._startup_job = None
        pending = self.link_entry.get().strip()
        if pending:
            self.add_link()
        self._ready = True
        self._save_settings()
        self.destroy()

    def destroy(self) -> None:
        self._tray_remove()
        self._unhook_window()
        super().destroy()

    def _frame_hwnd(self) -> int:
        """Return the outer window that owns the minimize button.

        Tk creates that TkTopLevel wrapper when the window is first shown.
        Before then, winfo_id() is only the inner TkChild.
        """
        user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
        child = int(self.winfo_id())
        parent = int(user32.GetParent(child) or 0)
        if window_class(parent) == "TkTopLevel":
            return parent
        return 0

    def _schedule_tray_hook(self) -> None:
        if self._tray_hook_tries >= 40:
            return
        self._tray_hook_tries += 1
        self.after(50, self._install_tray_hook)

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self:
            self._install_tray_hook()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is not self or self._in_tray:
            return
        try:
            iconic = self.state() == "iconic"
        except tk.TclError:
            return
        if iconic:
            self.after(0, self._minimize_to_tray)

    def _install_tray_hook(self) -> None:
        if sys.platform != "win32":
            return
        try:
            frame = self._frame_hwnd()
        except Exception:
            frame = 0
        if not frame:
            self._schedule_tray_hook()
            return
        if self._tray_hwnd == frame and self._tray_proc is not None:
            return
        if self._tray_proc is not None:
            self._unhook_window()
        try:
            import ctypes

            user32, _shell32, _gdi32, lresult = _prepare_tray_apis()
            wndproc = ctypes.WINFUNCTYPE(lresult, ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
            # Windows keeps this pointer. It has to stay referenced for the life of the window.
            self._tray_proc = wndproc(self._wndproc)
            previous = user32.SetWindowLongPtrW(frame, -4, ctypes.cast(self._tray_proc, ctypes.c_void_p))
            if not previous:
                self._tray_proc = None
                self._schedule_tray_hook()
                return
            self._tray_hwnd = frame
            self._old_wndproc = previous
            self._tray_hook_tries = 0
            use_dark_titlebar(self)
        except Exception:
            self._tray_proc = None
            self._old_wndproc = None
            self._schedule_tray_hook()

    def _unhook_window(self) -> None:
        if self._tray_proc is None or not self._tray_hwnd or not self._old_wndproc:
            self._tray_proc = None
            self._old_wndproc = None
            return
        try:
            import ctypes

            user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
            user32.SetWindowLongPtrW(self._tray_hwnd, -4, ctypes.c_void_p(self._old_wndproc))
        except Exception:
            pass
        self._tray_proc = None
        self._old_wndproc = None

    def _wndproc(self, hwnd, message, wparam, lparam):
        import ctypes

        handled = False
        try:
            handled = self._handle_window_message(int(message), int(wparam), int(lparam))
        except Exception:
            handled = False
        if handled:
            return 0
        if not self._old_wndproc:
            return 0
        try:
            user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
            return user32.CallWindowProcW(ctypes.c_void_p(self._old_wndproc), hwnd, message, wparam, lparam)
        except Exception:
            return 0

    def _handle_window_message(self, message: int, wparam: int, lparam: int) -> bool:
        # This runs inside the Windows message call. Touching Tk here aborts the program.
        if is_minimize_command(message, wparam) and not self._tray_minimizing:
            self._tray_requests.append("minimize")
            return True
        if message == WM_TRAY:
            action = tray_click(int(lparam) & 0xFFFF)
            if action:
                self._tray_requests.append(action)
            return True
        return False

    def _minimize_to_tray(self) -> bool:
        if self._in_tray or self._tray_minimizing:
            return self._in_tray
        self._tray_minimizing = True
        try:
            if not self._tray_add():
                try:
                    self.iconify()
                except tk.TclError:
                    pass
                return False
            self._plan_hidden_for_tray = self._withdraw_plan_if_visible()
            try:
                self.withdraw()
            except tk.TclError:
                self._tray_remove()
                self._plan_hidden_for_tray = False
                return False
            self._in_tray = True
            return True
        finally:
            self._tray_minimizing = False

    def _withdraw_plan_if_visible(self) -> bool:
        plan = self.plan_window
        if plan is None:
            return False
        try:
            if not plan.winfo_exists() or not plan.winfo_viewable():
                return False
            plan.withdraw()
        except tk.TclError:
            return False
        return True

    def _restore_from_tray(self) -> None:
        was_hidden = self._in_tray
        show_plan = self._plan_hidden_for_tray
        self._plan_hidden_for_tray = False
        self._in_tray = False
        self._tray_remove()
        if not was_hidden:
            return
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except tk.TclError:
            return
        if self._tray_hwnd:
            try:
                user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
                user32.SetForegroundWindow(self._tray_hwnd)
            except Exception:
                pass
        if not show_plan or self.plan_window is None:
            return
        try:
            if self.plan_window.winfo_exists():
                self.plan_window.deiconify()
                self.plan_window.lift()
        except tk.TclError:
            return

    def _show_tray_menu(self) -> None:
        if not self._in_tray or not self._tray_hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            user32.AppendMenuW(menu, 0, TRAY_MENU_OPEN, "Open NimboVoD")
            user32.AppendMenuW(menu, 0, TRAY_MENU_EXIT, "Exit")
            user32.SetForegroundWindow(self._tray_hwnd)
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            command = user32.TrackPopupMenu(menu, 0x0002 | 0x0100 | 0x0080 | 0x0020, int(point.x), int(point.y), 0, self._tray_hwnd, None)
            user32.PostMessageW(self._tray_hwnd, WM_NULL, 0, 0)
            user32.DestroyMenu(menu)
        except Exception:
            return
        if command == TRAY_MENU_OPEN:
            self._restore_from_tray()
        elif command == TRAY_MENU_EXIT:
            self._on_close()

    def _tray_add(self) -> bool:
        if sys.platform != "win32":
            return False
        if not self._tray_hwnd:
            self._install_tray_hook()
        if not self._tray_hwnd:
            return False
        if self._tray_added:
            return True
        try:
            import ctypes

            _user32, shell32, _gdi32, _lresult = _prepare_tray_apis()
            if not self._tray_icon:
                handle, owned = create_tray_icon()
                if not handle:
                    return False
                self._tray_icon = handle
                self._tray_icon_owned = owned
            data = _tray_icon_type()()
            data.cbSize = ctypes.sizeof(data)
            data.hWnd = self._tray_hwnd
            data.uID = 1
            data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            data.uCallbackMessage = WM_TRAY
            data.hIcon = self._tray_icon
            data.szTip = "NimboVoD"
            pointer = ctypes.byref(data)
            if shell32.Shell_NotifyIconW(NIM_ADD, pointer):
                self._tray_added = True
                return True
            shell32.Shell_NotifyIconW(NIM_DELETE, pointer)
            if shell32.Shell_NotifyIconW(NIM_ADD, pointer):
                self._tray_added = True
                return True
        except Exception:
            return False
        return False

    def _tray_remove(self) -> None:
        if sys.platform != "win32":
            self._tray_added = False
            return
        try:
            import ctypes

            user32, shell32, _gdi32, _lresult = _prepare_tray_apis()
            if self._tray_added and self._tray_hwnd:
                data = _tray_icon_type()()
                data.cbSize = ctypes.sizeof(data)
                data.hWnd = self._tray_hwnd
                data.uID = 1
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            if self._tray_icon and self._tray_icon_owned:
                user32.DestroyIcon(self._tray_icon)
        except Exception:
            pass
        self._tray_added = False
        self._tray_icon = None
        self._tray_icon_owned = False


def self_check() -> None:
    cleaner = load_cleaner()
    pasted = parse_library_text("The Matrix (1999)\nThe Office S01E01\n", cleaner)
    assert pasted[0]["type"] == "Movie"
    assert pasted[1]["type"] == "Episode" and pasted[1]["episode"] == 1
    exported = parse_library_text(json.dumps({"items": SAMPLE_LIBRARY}), cleaner)
    assert len(exported) == len(SAMPLE_LIBRARY)
    entries = cleaner.parse_m3u(SAMPLE_PLAYLIST)
    planned = cleaner.plan_entries(
        entries,
        SAMPLE_LIBRARY,
        {"layout": "group", "strip_quality": True, "use_years": True, "match_filenames": True},
    )
    by_title: dict[str, list[str]] = {}
    for row in planned:
        by_title.setdefault(row["title"], []).append(row["disposition"])
    assert by_title["The Matrix (1999)"] == ["drop", "drop"]
    assert by_title["Dune (1984)"] == ["keep"]
    assert by_title["Dune (2021)"] == ["drop"]
    assert by_title["The Office S01E01"] == ["drop"]
    assert by_title["The Office S01E03"] == ["drop"]
    assert by_title["Severance S01E02"] == ["drop"]
    assert by_title["Blade Runner 2049"] == ["keep"]
    assert by_title["City News"] == ["keep"]
    tagged = cleaner.parse_m3u(
        '#EXTM3U\n#EXTINF:-1 group-title="Movies",The Matrix YTS 1080p\nhttps://example.com/yts\n'
    )
    tagged_plan = cleaner.plan_entries(
        tagged,
        [{"name": "The Matrix", "year": 1999, "type": "Movie"}],
        {
            "layout": "flat",
            "strip_quality": True,
            "use_years": True,
            "match_filenames": False,
            "extra_tags": "YTS",
        },
    )
    assert tagged_plan[0]["path"] == "The Matrix.strm"
    assert tagged_plan[0]["disposition"] == "drop"

    import tempfile

    assert auto_steps(True, True, True) == ["playlists", "emby", "apply"]
    assert auto_steps(False, False, False) == []
    assert auto_steps(False, True, True) == ["emby", "apply"]
    assert refresh_minutes("") == 0
    assert refresh_minutes("0") == 0
    assert refresh_minutes("15") == 15
    assert refresh_minutes("abc") == 0
    assert refresh_minutes("999999") == 10080
    assert host_resolves("localhost", 2)
    handle = tempfile.NamedTemporaryFile(prefix="nimbovod-settings-", suffix=".json", delete=False)
    handle.close()
    os.environ["NIMBOVOD_SETTINGS"] = handle.name
    Path(handle.name).write_text(
        json.dumps(
            {
                "runAtStartup": True,
                "autoPullPlaylists": True,
                "autoPullEmby": True,
                "autoApplyStrm": True,
                "refreshMinutes": 45,
                "playlistFiles": [r"C:\playlists\test.m3u"],
            }
        ),
        encoding="utf-8",
    )
    startup_before = read_startup_command()
    app = None
    try:
        app = App()
        app.withdraw()
        assert not startup_registration_allowed()
        assert read_startup_command() == startup_before
        apply_startup_registration(True)
        apply_startup_registration(False)
        assert read_startup_command() == startup_before
        app._startup_auto()
        assert app._auto_queue == []
        assert not app._busy
        assert app.run_at_startup.get() == 1
        assert app.auto_pull_playlists.get() == 1
        assert app.auto_pull_emby.get() == 1
        assert app.auto_apply_strm.get() == 1
        assert app.playlist_files == [r"C:\playlists\test.m3u"]
        collected = app._collect_settings()
        assert collected["runAtStartup"] is True
        assert collected["autoPullPlaylists"] is True
        assert collected["autoPullEmby"] is True
        assert collected["autoApplyStrm"] is True
        assert collected["refreshMinutes"] == 45
        assert app.refresh_minutes.get() == "45"
        assert app._refresh_job is None
        app.refresh_minutes.set("10")
        assert app._refresh_job is None
        app.refresh_minutes.set("45")
        assert collected["playlistFiles"] == [r"C:\playlists\test.m3u"]
        app.emby_libraries = [
            {"id": "tv", "name": "TV shows", "type": "tvshows", "selected": True},
            {"id": "vod", "name": "TV VoD", "type": "tvshows", "selected": True},
        ]
        app._render_library_choices()
        app._library_vars[1].set(0)
        app._store_library_checks()
        assert app._collect_settings()["embyLibraries"][1]["selected"] is False
        assert app._libraries_for_fetch() == [{"id": "tv", "name": "TV shows"}]
        wheel = type("Wheel", (), {"delta": -120})()
        app._wheel_form(wheel)
        app._wheel_form(wheel)
        assert app._wheel_job is not None
        assert app._wheel_pending == 144
        app.after_cancel(app._wheel_job)
        app._apply_form_wheel()
        assert app._wheel_job is None
        assert app._wheel_pending == 0
        over_list = type("Wheel", (), {"delta": -120, "widget": app.link_list})()
        app._wheel_widget(over_list)
        assert app._wheel_pending == 72
        app.after_cancel(app._wheel_job)
        app._wheel_job = None
        app._wheel_pending = 0.0
        for index in range(20):
            app.link_list.insert("end", f"playlist {index}")
        app.update_idletasks()
        before = app.link_list.yview()[0]
        app._wheel_widget(type("Wheel", (), {"delta": -120, "widget": app.link_list})())
        assert app.link_list.yview()[0] > before
        assert app._wheel_pending == 0
        app.link_list.delete(0, "end")
        app.load_sample()
        app.show_plan()
        app.update()
        app.deiconify()
        app.update()
        app._install_tray_hook()
        assert window_class(app._tray_hwnd) == "TkTopLevel", window_class(app._tray_hwnd)
        assert int(app._tray_hwnd) != int(app.winfo_id())
        assert is_minimize_command(WM_SYSCOMMAND, SC_MINIMIZE)
        assert not is_minimize_command(WM_SYSCOMMAND, 0xF060)
        assert tray_click(WM_LBUTTONUP) == "open"
        assert tray_click(WM_LBUTTONDBLCLK) == "open"
        assert tray_click(WM_RBUTTONUP) == "menu"
        assert tray_click(WM_CONTEXTMENU) == "menu"
        assert tray_click(0x0200) == ""
        plan_visible = bool(app.plan_window is not None and app.plan_window.winfo_viewable())
        user32, _shell32, _gdi32, _lresult = _prepare_tray_apis()
        user32.SendMessageW(app._tray_hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
        app._apply_tray_requests()
        assert app._in_tray and app._tray_added
        assert not app.winfo_viewable()
        if plan_visible:
            assert not app.plan_window.winfo_viewable()
        app._restore_from_tray()
        assert not app._in_tray and not app._tray_added
        assert app.winfo_viewable()
        if plan_visible:
            assert app.plan_window.winfo_viewable()
        app.iconify()
        app.update()
        app._apply_tray_requests()
        assert app._in_tray and not app.winfo_viewable()
        app._restore_from_tray()
        app._restore_from_tray()
        app.withdraw()
        children = app.tree.get_children()
        assert children, "sample plan did not fill the list"
        app.sources = [{"kind": "file", "label": "tagged", "entries": tagged}]
        app.library = [{"name": "The Matrix", "year": 1999, "type": "Movie"}]
        app.extra_tags.delete("1.0", "end")
        app.extra_tags.insert("1.0", "YTS")
        app.layout.set("flat")
        app.build_plan()
        assert app.planned[0]["path"] == "The Matrix.strm", app.planned[0]["path"]
        assert "left out" in app.summary.get()
        assert app.tree.item(children[0], "values")[2] in {"New", "Leave out"}
        order: list[str] = []
        active = {"name": ""}
        problems: list[str] = []

        def fetch_text(url: str, on_progress=None) -> str:
            if active["name"]:
                problems.append(f"playlist overlapped {active['name']}")
            active["name"] = "playlist"
            order.append("playlist")
            time.sleep(0.06)
            active["name"] = ""
            return SAMPLE_PLAYLIST

        def fetch_emby(_base: str, _key: str, _user: str, on_progress=None, libraries=None) -> list[dict]:
            if active["name"]:
                problems.append(f"emby overlapped {active['name']}")
            if order != ["playlist", "playlist"]:
                problems.append(f"emby started early: {order}")
            active["name"] = "emby"
            order.append("emby")
            time.sleep(0.06)
            active["name"] = ""
            return [{"name": "The Matrix", "year": 1999, "type": "Movie"}]

        def write_plan(_out: Path, _planned: list[dict], _apply: bool, on_progress=None) -> dict[str, int]:
            if active["name"]:
                problems.append(f"write overlapped {active['name']}")
            if order != ["playlist", "playlist", "emby"]:
                problems.append(f"write started early: {order}")
            order.append("write")
            return {"added": 1, "updated": 0, "unchanged": 2, "removed": 0}

        app.cleaner.fetch_text = fetch_text
        app.cleaner.fetch_emby = fetch_emby
        app.cleaner.write_plan = write_plan
        sequence_dir = tempfile.mkdtemp(prefix="nimbovod-seq-")
        try:
            app.links = ["http://127.0.0.1/one.m3u", "http://127.0.0.1/two.m3u"]
            app.playlist_files = []
            app.emby.set("http://127.0.0.1:8096")
            app.api_key.set("test-key")
            app.user_id.set("")
            app.out_dir.set(sequence_dir)
            if app._tag_job is not None:
                app.after_cancel(app._tag_job)
                app._tag_job = None
            snapshot = app._auto_snapshot(["playlists", "emby", "apply"])
            app._auto_running = True
            app._busy = True
            worker = threading.Thread(target=lambda: app._auto_pipeline(snapshot), daemon=True)
            worker.start()
            deadline = time.time() + 15
            while time.time() < deadline and worker.is_alive():
                app.update()
                time.sleep(0.02)
            worker.join(2)
            assert order == ["playlist", "playlist", "emby", "write"], (order, problems, app.status.get())
            assert not problems, problems
            assert not app._auto_running
            assert not app._busy
            assert app.status.get().startswith("Added 1"), app.status.get()
            assert "left 2 unchanged" in app.status.get()
        finally:
            import shutil

            shutil.rmtree(sequence_dir, ignore_errors=True)
    finally:
        if app is not None:
            app.destroy()
        Path(handle.name).unlink(missing_ok=True)
        os.environ.pop("NIMBOVOD_SETTINGS", None)
    print("gui self-check ok")


def main() -> None:
    ensure_stdio()
    enable_dpi()
    if "--self-check" in sys.argv:
        try:
            self_check()
        except Exception:
            import traceback

            log = Path(os.environ.get("TEMP", ".")) / "nimbovod-self-check.txt"
            log.write_text(traceback.format_exc(), encoding="utf-8")
            raise
        return
    try:
        app = App()
    except Exception as exc:
        ensure_stdio()
        print(explain(exc), file=sys.stderr)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("NimboVoD", explain(exc))
            root.destroy()
        except Exception:
            pass
        raise SystemExit(1)
    app.mainloop()


if __name__ == "__main__":
    main()
