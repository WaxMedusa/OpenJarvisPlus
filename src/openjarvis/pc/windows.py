"""Windows desktop control: apps, windows, monitors, screenshots.

Everything here returns a ``(ok, message)`` tuple or a plain value; nothing
raises on a non-Windows host. The ctypes helpers are adapted from the original
standalone ``jarvis.py`` clap script so window focusing and fullscreen behave
exactly as they did there.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# Known install locations for the apps this project actually drives.
_APP_CANDIDATES = {
    "steam": [
        r"%ProgramFiles(x86)%\Steam\steam.exe",
        r"%ProgramFiles%\Steam\steam.exe",
        r"%LOCALAPPDATA%\Steam\steam.exe",
    ],
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
    "cursor": [
        r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe",
        r"%LOCALAPPDATA%\Programs\Cursor\Cursor.exe",
        r"%ProgramFiles%\Cursor\Cursor.exe",
    ],
    "spotify": [
        r"%APPDATA%\Spotify\Spotify.exe",
        r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
    ],
    "vscode": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
    ],
    "discord": [r"%LOCALAPPDATA%\Discord\Update.exe"],
    "notepad": [r"%WINDIR%\notepad.exe"],
    "explorer": [r"%WINDIR%\explorer.exe"],
    "calculator": [r"%WINDIR%\System32\calc.exe"],
    "paint": [r"%WINDIR%\System32\mspaint.exe"],
    "task manager": [r"%WINDIR%\System32\Taskmgr.exe"],
    "terminal": [r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"],
}

_URL_SCHEMES = ("http://", "https://", "steam://", "mailto:")


def hidden_popen_kwargs() -> dict:
    """Keep helper processes from flashing a console window on Windows."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(path)))


def resolve_app(name: str) -> Optional[str]:
    """Resolve an app name, path or URL to something launchable."""
    value = (name or "").strip().strip('"')
    if not value:
        return None
    if value.lower().startswith(_URL_SCHEMES):
        return value
    expanded = expand(value)
    if Path(expanded).exists():
        return expanded
    known = _APP_CANDIDATES.get(value.lower())
    if known:
        for candidate in known:
            resolved = expand(candidate)
            if Path(resolved).exists():
                return resolved
    for probe in (value, f"{value}.exe"):
        found = shutil.which(probe)
        if found:
            return found
    return None


def chrome_executable() -> Optional[str]:
    return (
        resolve_app("chrome") or shutil.which("google-chrome") or shutil.which("chrome")
    )


def open_target(target: str) -> Tuple[bool, str]:
    """Open a file, folder, URL or URI with the OS default handler."""
    value = (target or "").strip()
    if not value:
        return False, "Nothing to open."
    try:
        if IS_WINDOWS:
            os.startfile(value)  # type: ignore[attr-defined]
            return True, f"Opened {value}"
        if value.lower().startswith(("http://", "https://")):
            import webbrowser

            webbrowser.open(value)
            return True, f"Opened {value} in the browser."
        subprocess.Popen(
            ["xdg-open", value],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, f"Opened {value}"
    except OSError as exc:
        return False, f"Could not open {value}: {exc}"


def launch_app(name: str, args: str = "") -> Tuple[bool, str]:
    """Launch an application, passing extra *args* when it is an executable."""
    resolved = resolve_app(name)
    if not resolved:
        return False, (
            f"I could not find an app called {name!r}. "
            "Pass a full path or install it first."
        )
    if resolved.lower().startswith(_URL_SCHEMES):
        return open_target(resolved)
    extra = shlex.split(args, posix=False) if args else []
    if extra or not IS_WINDOWS or resolved.lower().endswith((".exe", ".bat", ".cmd")):
        try:
            subprocess.Popen([resolved, *extra], **hidden_popen_kwargs())
            return True, f"Launched {name}."
        except OSError as exc:
            return False, f"Could not launch {name}: {exc}"
    return open_target(resolved)


def open_url_in_chrome(
    url: str,
    *,
    monitor: int = 1,
    fullscreen: bool = False,
    width: int = 1400,
    height: int = 900,
) -> Tuple[bool, str]:
    """Open *url* in a new Chrome window on a specific monitor."""
    chrome = chrome_executable()
    if not chrome:
        return open_target(url)
    args = [chrome, "--new-window"]
    rect = monitor_rect(monitor)
    if rect:
        left, top, right, bottom = rect
        if fullscreen:
            args.append(f"--window-position={left},{top}")
            args.append(
                f"--window-size={max(320, right - left)},{max(240, bottom - top)}"
            )
            args.append("--start-fullscreen")
        else:
            x = left + max(0, (right - left - width) // 2)
            y = top + max(0, (bottom - top - height) // 2)
            args.append(f"--window-position={x},{y}")
            args.append(f"--window-size={width},{height}")
    elif fullscreen:
        args.append("--start-fullscreen")
    args.append(url)
    try:
        subprocess.Popen(args, **hidden_popen_kwargs())
        return True, f"Opened {url} in Chrome."
    except OSError as exc:
        return False, f"Could not open Chrome: {exc}"


# ---------------------------------------------------------------------------
# Window enumeration / focus (Windows only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)

    def as_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "exe": self.exe,
            "rect": [self.left, self.top, self.right, self.bottom],
        }


def _exe_for_pid(pid: int) -> str:
    if not IS_WINDOWS:
        return ""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def iter_windows() -> List[WindowInfo]:
    """List visible, titled top-level windows."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    found: List[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam) -> bool:
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title_buf.value,
                exe=_exe_for_pid(pid.value) if pid.value else "",
                left=rect.left,
                top=rect.top,
                right=rect.right,
                bottom=rect.bottom,
            )
        )
        return True

    user32.EnumWindows(_enum, 0)
    return found


def find_window(*, exe: str = "", title: str = "") -> Optional[WindowInfo]:
    exe_l = exe.lower()
    title_l = title.lower()
    matches = [
        window
        for window in iter_windows()
        if (not exe_l or exe_l in window.exe.lower())
        and (not title_l or title_l in window.title.lower())
    ]
    if not matches:
        return None
    return max(matches, key=lambda window: window.area)


def focus_window(hwnd: int) -> bool:
    if not IS_WINDOWS:
        return False
    import ctypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    tid_target = user32.GetWindowThreadProcessId(hwnd, None)
    tid_foreground = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    attached = False
    if tid_foreground and tid_target and tid_foreground != tid_target:
        attached = bool(user32.AttachThreadInput(tid_foreground, tid_target, True))
    try:
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(tid_foreground, tid_target, False)


def focus_app(name: str) -> Tuple[bool, str]:
    """Focus an existing window for *name*; launch it when none is open."""
    exe = resolve_app(name) or ""
    basename = os.path.basename(exe) if exe else f"{name.lower()}.exe"
    window = find_window(exe=basename)
    if window is None and not basename.endswith(".exe"):
        window = find_window(exe=basename + ".exe")
    if window is not None and focus_window(window.hwnd):
        return True, f"Focused {window.title!r}."
    if window is not None:
        return False, f"Found {window.title!r} but Windows refused to focus it."
    return launch_app(name)


def send_f11(hwnd: int) -> None:
    if not IS_WINDOWS:
        return
    import ctypes

    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    VK_F11 = 0x7A
    focus_window(hwnd)
    user32.keybd_event(VK_F11, 0, 0, 0)
    user32.keybd_event(VK_F11, 0, KEYEVENTF_KEYUP, 0)


# ---------------------------------------------------------------------------
# Monitors / window placement
# ---------------------------------------------------------------------------


def monitor_rects() -> List[Tuple[int, int, int, int]]:
    """Each monitor as (left, top, right, bottom), sorted left-to-right."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    collected: List[Tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )
    def _callback(_hm, _hdc, rect_ptr, _lp) -> bool:
        rect = rect_ptr.contents
        collected.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    ctypes.windll.user32.EnumDisplayMonitors(None, None, _callback, 0)
    collected.sort(key=lambda item: (item[0], item[1]))
    return collected


def monitor_rect(index: int) -> Optional[Tuple[int, int, int, int]]:
    rects = monitor_rects()
    if not rects:
        return None
    idx = max(0, min(index - 1, len(rects) - 1))
    return rects[idx]


def move_window(
    hwnd: int,
    *,
    monitor: int = 1,
    fullscreen: bool = False,
    width: int = 1400,
    height: int = 900,
) -> Tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Window placement is Windows-only."
    import ctypes

    rect = monitor_rect(monitor)
    if rect is None:
        return False, "No monitors detected."
    left, top, right, bottom = rect
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    SW_SHOWMAXIMIZED = 3
    SWP_SHOWWINDOW = 0x0040
    SWP_FRAMECHANGED = 0x0020
    HWND_TOP = 0
    user32.ShowWindow(hwnd, SW_RESTORE)
    if fullscreen:
        x, y = left, top
        w, h = right - left, bottom - top
    else:
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        w, h = width, height
    ok = user32.SetWindowPos(
        hwnd, HWND_TOP, x, y, w, h, SWP_SHOWWINDOW | SWP_FRAMECHANGED
    )
    if fullscreen:
        user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    focus_window(hwnd)
    return bool(ok), f"Moved window to monitor {monitor}" + (
        " (fullscreen)." if fullscreen else "."
    )


# ---------------------------------------------------------------------------
# Screenshot / disk / processes
# ---------------------------------------------------------------------------


def screenshot_path() -> Path:
    from openjarvis.core.paths import get_config_dir

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return get_config_dir() / "wake" / "screenshots" / f"screen-{stamp}.png"


def screen_capture(path: Path) -> Tuple[bool, str]:
    """Capture the primary screen to a PNG (Windows, PowerShell/.NET)."""
    if not IS_WINDOWS:
        return False, "Screen capture is Windows-only right now."
    path.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(path).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{escaped}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=45,
            **{"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            if IS_WINDOWS
            else {},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Screen capture failed: {exc}"
    if result.returncode != 0 or not path.exists():
        return False, (result.stderr or "Screen capture produced no file.").strip()[
            :400
        ]
    return True, f"Saved screenshot to {path}."


def disk_usage(path: Optional[Path] = None) -> Tuple[float, float]:
    """Return (free_gb, total_gb) for the drive holding *path*."""
    target = path or Path.home()
    try:
        usage = shutil.disk_usage(str(target))
    except OSError:
        return 0.0, 0.0
    return usage.free / (1024**3), usage.total / (1024**3)


def process_running(exe: str) -> bool:
    """Best-effort process check by executable name."""
    try:
        import psutil
    except ImportError:
        return bool(find_window(exe=exe))
    needle = os.path.basename(exe).lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == needle:
                return True
        except Exception:
            continue
    return False


def run_powershell(script: str, *, timeout: int = 120) -> Tuple[bool, str]:
    """Run a PowerShell command and return combined output."""
    if not IS_WINDOWS:
        return False, "PowerShell is only available on Windows."
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, f"PowerShell timed out after {timeout}s."
    except OSError as exc:
        return False, f"PowerShell failed to start: {exc}"
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        return False, (error or output or f"exit code {result.returncode}")[:2000]
    return True, (output or "(no output)")[:4000]


def sleep(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


__all__ = [
    "IS_WINDOWS",
    "WindowInfo",
    "chrome_executable",
    "disk_usage",
    "find_window",
    "focus_app",
    "focus_window",
    "hidden_popen_kwargs",
    "iter_windows",
    "launch_app",
    "monitor_rect",
    "monitor_rects",
    "move_window",
    "open_target",
    "open_url_in_chrome",
    "process_running",
    "resolve_app",
    "run_powershell",
    "screen_capture",
    "screenshot_path",
    "send_f11",
    "sleep",
]
