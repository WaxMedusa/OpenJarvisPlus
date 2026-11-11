"""Launching apps and positioning windows.

Chrome monitor placement, Cursor focusing and F11 fullscreen are Windows-only.
Everywhere else the same calls degrade to a plain browser/process launch, which
keeps the whole app testable on Linux and macOS.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
DEFAULT_WINDOW = (1400, 900)
NEW_WINDOW_TIMEOUT_S = 20.0

_POPEN_KW = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
}
if IS_WINDOWS:
    _POPEN_KW["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def open_uri(uri: str) -> str:
    """Open a URL / spotify: / file: target with the OS default handler."""
    target = uri.strip()
    if not target:
        return "empty target"
    try:
        if IS_WINDOWS and hasattr(os, "startfile"):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            webbrowser.open(target)
    except OSError as exc:
        return f"could not open: {exc}"
    return "opened"


def chrome_executable() -> str | None:
    if IS_WINDOWS:
        bases = (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        )
        for base in bases:
            if not base:
                continue
            candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    return (
        shutil.which("google-chrome")
        or shutil.which("chrome")
        or shutil.which("chromium")
    )


def cursor_executable() -> str | None:
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for rel in ("Programs\\cursor\\Cursor.exe", "Programs\\Cursor\\Cursor.exe"):
                candidate = Path(local) / Path(*rel.split("\\"))
                if candidate.is_file():
                    return str(candidate)
    return shutil.which("cursor")


# --- Windows helpers -------------------------------------------------------


def _sorted_monitors() -> list[tuple[int, int, int, int]]:
    """Monitor rects sorted left-to-right, then top-to-bottom."""
    if not IS_WINDOWS:
        return []
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    found: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )
    def _collect(_monitor, _hdc, rect_ptr, _lparam):
        rect = rect_ptr.contents
        found.append((int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, _collect, 0)
    found.sort(key=lambda r: (r[0], r[1]))
    return found


def _monitor_bounds(one_based: int) -> tuple[int, int, int, int]:
    monitors = _sorted_monitors()
    if not monitors:
        return (0, 0, 1920, 1080)
    index = min(max(one_based - 1, 0), len(monitors) - 1)
    return monitors[index]


def _chrome_hwnds() -> set[int]:
    if not IS_WINDOWS:
        return set()
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x80
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return True
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return True
            exe = buf.value
        finally:
            kernel32.CloseHandle(handle)
        if os.path.basename(exe).lower() != "chrome.exe":
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left < 80 or rect.bottom - rect.top < 80:
            return True
        found.add(int(hwnd))
        return True

    user32.EnumWindows(_visit, 0)
    return found


def _wait_new_hwnd(before: set[int], timeout: float) -> int | None:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.12)
        new = _chrome_hwnds() - before
        best, best_area = None, 0
        for hwnd in new:
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                if area > best_area:
                    best, best_area = hwnd, area
        if best is not None:
            return best
    return None


def _snap(hwnd: int, monitor: int, *, fullscreen: bool) -> None:
    user32 = ctypes.windll.user32
    left, top, right, bottom = _monitor_bounds(monitor)
    SW_RESTORE = 9
    SW_SHOWMAXIMIZED = 3
    HWND_TOP = 0
    SWP_SHOWWINDOW = 0x0040
    SWP_FRAMECHANGED = 0x0020

    user32.ShowWindow(hwnd, SW_RESTORE)
    if fullscreen:
        x, y = left, top
        width, height = right - left, bottom - top
    else:
        width, height = DEFAULT_WINDOW
        width = min(width, max(480, right - left))
        height = min(height, max(360, bottom - top))
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
    user32.SetWindowPos(
        hwnd, HWND_TOP, x, y, width, height, SWP_SHOWWINDOW | SWP_FRAMECHANGED
    )
    if fullscreen:
        user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
        VK_F11 = 0x7A
        KEYEVENTF_KEYUP = 0x0002
        foreground = user32.GetForegroundWindow()
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        foreground_tid = (
            user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        )
        if foreground_tid and target_tid:
            user32.AttachThreadInput(foreground_tid, target_tid, True)
        user32.SetForegroundWindow(hwnd)
        if foreground_tid and target_tid:
            user32.AttachThreadInput(foreground_tid, target_tid, False)
        user32.keybd_event(VK_F11, 0, 0, 0)
        user32.keybd_event(VK_F11, 0, KEYEVENTF_KEYUP, 0)


def open_url_in_chrome(url: str, *, monitor: int = 1, fullscreen: bool = False) -> str:
    url = url.strip()
    if not url:
        return "empty url"
    chrome = chrome_executable()
    if not chrome:
        webbrowser.open(url)
        return "Chrome not found; opened in the default browser"

    args = [chrome, "--new-window"]
    if IS_WINDOWS:
        left, top, right, bottom = _monitor_bounds(monitor)
        size = (right - left, bottom - top) if fullscreen else DEFAULT_WINDOW
        args.append(f"--window-position={left},{top}")
        args.append(f"--window-size={size[0]},{size[1]}")
    if fullscreen and not IS_WINDOWS:
        args.append("--start-fullscreen")
    args.append(url)

    try:
        before = _chrome_hwnds() if IS_WINDOWS else set()
        subprocess.Popen(args, **_POPEN_KW)
        if IS_WINDOWS:
            hwnd = _wait_new_hwnd(before, NEW_WINDOW_TIMEOUT_S)
            if hwnd is None:
                return "Opened, but no new Chrome window was found to position"
            _snap(hwnd, monitor, fullscreen=fullscreen)
    except OSError as exc:
        return f"Could not open Chrome: {exc}"
    return f"Opened on monitor {monitor}{' (fullscreen)' if fullscreen else ''}"


def _foreground(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    foreground = user32.GetForegroundWindow()
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_tid = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    if foreground_tid and target_tid:
        user32.AttachThreadInput(foreground_tid, target_tid, True)
    user32.SetForegroundWindow(hwnd)
    if foreground_tid and target_tid:
        user32.AttachThreadInput(foreground_tid, target_tid, False)


def _largest_cursor_window() -> int | None:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    candidates: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):
        if user32.GetWindow(hwnd, 4) or user32.GetWindowLongW(hwnd, -20) & 0x80:
            return True
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return True
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return True
            exe = buf.value
        finally:
            kernel32.CloseHandle(handle)
        if os.path.basename(exe).lower() != "cursor.exe":
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 200 or height < 200:
            return True
        candidates.append((width * height, int(hwnd)))
        return True

    user32.EnumWindows(_visit, 0)
    return max(candidates)[1] if candidates else None


def _send_f11(hwnd: int) -> None:
    _foreground(hwnd)
    user32 = ctypes.windll.user32
    VK_F11 = 0x7A
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_F11, 0, 0, 0)
    user32.keybd_event(VK_F11, 0, KEYEVENTF_KEYUP, 0)


def focus_cursor(*, focus: bool, new_window: bool, fullscreen: bool) -> str:
    if not focus and not new_window:
        return "disabled"
    exe = cursor_executable()
    if not exe:
        return "Cursor not found (install it or add `cursor` to PATH)"

    focused = False
    try:
        if focus:
            if IS_WINDOWS:
                hwnd = _largest_cursor_window()
                if hwnd is not None:
                    _foreground(hwnd)
                    focused = True
            if not focused:
                subprocess.Popen([exe], **_POPEN_KW)
        if new_window:
            subprocess.Popen([exe, "-n"], **_POPEN_KW)
    except OSError as exc:
        return f"Could not start Cursor: {exc}"

    if fullscreen and IS_WINDOWS:
        time.sleep(0.5)
        hwnd = _largest_cursor_window()
        if hwnd is None:
            return "Opened Cursor, but no window was found for fullscreen"
        _send_f11(hwnd)
    return "Focused existing window" if focused else "Opened Cursor"
