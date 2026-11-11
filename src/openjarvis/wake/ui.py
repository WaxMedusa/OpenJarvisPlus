"""Small Tkinter control panel so Jarvis can run without a terminal.

The window is intentionally modest: status, transcript, a Talk button, a text
box, the checkup button, and a "start with Windows" toggle. It runs the same
:class:`~openjarvis.wake.session.WakeSession` as the terminal, so behaviour
cannot drift between the two frontends.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from openjarvis.wake import startup
from openjarvis.wake.session import SessionEvent, WakeSession

_BG = "#0b1015"
_PANEL = "#121a22"
_FG = "#d8e6ee"
_DIM = "#7f95a6"
_ACCENT = "#4fd1e5"


def _make_window(root: Any) -> dict:
    import tkinter as tk
    from tkinter import scrolledtext

    root.title("J.A.R.V.I.S.")
    root.configure(bg=_BG)
    root.geometry("820x600")
    root.minsize(640, 460)

    header = tk.Frame(root, bg=_BG)
    header.pack(fill="x", padx=18, pady=(16, 6))
    tk.Label(
        header,
        text="J.A.R.V.I.S.",
        bg=_BG,
        fg=_FG,
        font=("Segoe UI Semibold", 20),
    ).pack(side="left")
    status = tk.Label(
        header,
        text="starting…",
        bg=_BG,
        fg=_ACCENT,
        font=("Segoe UI", 10),
    )
    status.pack(side="right", pady=(8, 0))

    subtitle = tk.Label(
        root,
        text="Clap twice to wake. Ask for something, or type below.",
        bg=_BG,
        fg=_DIM,
        font=("Segoe UI", 9),
    )
    subtitle.pack(anchor="w", padx=18)

    transcript = scrolledtext.ScrolledText(
        root,
        bg=_PANEL,
        fg=_FG,
        insertbackground=_FG,
        relief="flat",
        wrap="word",
        font=("Segoe UI", 10),
        padx=12,
        pady=10,
        height=18,
    )
    transcript.pack(fill="both", expand=True, padx=18, pady=10)
    transcript.tag_configure("user", foreground="#8bd5ff", spacing1=6)
    transcript.tag_configure("assistant", foreground=_FG, spacing1=6)
    transcript.tag_configure("system", foreground=_DIM, spacing1=4)
    transcript.tag_configure("error", foreground="#ff8b7a", spacing1=6)
    transcript.tag_configure("task", foreground="#c8b273", spacing1=4)
    transcript.configure(state="disabled")

    entry_row = tk.Frame(root, bg=_BG)
    entry_row.pack(fill="x", padx=18, pady=(0, 8))
    entry = tk.Entry(
        entry_row,
        bg=_PANEL,
        fg=_FG,
        insertbackground=_FG,
        relief="flat",
        font=("Segoe UI", 11),
    )
    entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))

    def _button(parent: Any, text: str, command: Any, accent: bool = False) -> Any:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=_ACCENT if accent else _PANEL,
            fg="#04222a" if accent else _FG,
            activebackground=_ACCENT if accent else "#1b2833",
            activeforeground="#04222a" if accent else _FG,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )

    send_button = _button(entry_row, "Send", lambda: None, accent=True)
    send_button.pack(side="right")

    actions = tk.Frame(root, bg=_BG)
    actions.pack(fill="x", padx=18, pady=(0, 10))

    footer = tk.Frame(root, bg=_BG)
    footer.pack(fill="x", padx=18, pady=(0, 14))

    startup_var = tk.BooleanVar(value=startup.is_startup_installed())
    startup_toggle = tk.Checkbutton(
        footer,
        text="Start with Windows",
        variable=startup_var,
        bg=_BG,
        fg=_DIM,
        selectcolor=_PANEL,
        activebackground=_BG,
        activeforeground=_FG,
        font=("Segoe UI", 9),
    )
    startup_toggle.pack(side="left")

    hint = tk.Label(
        footer,
        text="autonomy + voice details are in the first lines above",
        bg=_BG,
        fg=_DIM,
        font=("Segoe UI", 8),
    )
    hint.pack(side="right")

    return {
        "status": status,
        "transcript": transcript,
        "entry": entry,
        "send": send_button,
        "actions": actions,
        "startup_var": startup_var,
        "startup_toggle": startup_toggle,
    }


def run_ui(session: WakeSession) -> None:
    """Open the panel and run until the window is closed."""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "Tkinter is not available in this Python build: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        root = tk.Tk()
    except Exception as exc:
        raise RuntimeError(
            "Could not open a window (no desktop session?): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    widgets = _make_window(root)
    transcript = widgets["transcript"]
    status = widgets["status"]
    entry = widgets["entry"]
    actions = widgets["actions"]

    events: "queue.Queue[SessionEvent]" = queue.Queue()
    calls: "queue.Queue[Any]" = queue.Queue()
    closing = threading.Event()

    def append(text: str, tag: str = "assistant") -> None:
        transcript.configure(state="normal")
        transcript.insert("end", text.rstrip() + "\n", tag)
        transcript.see("end")
        transcript.configure(state="disabled")

    def render(event: SessionEvent) -> None:
        if event.kind == "status":
            status.configure(text=event.text or "idle")
            return
        if event.kind == "user":
            append(f"You: {event.text}", "user")
        elif event.kind == "assistant":
            append(f"JARVIS: {event.text}", "assistant")
        elif event.kind == "brief":
            append(f"JARVIS: {event.text}", "assistant")
            if event.detail:
                append(event.detail, "system")
        elif event.kind == "task":
            append(event.text, "task")
        elif event.kind == "error":
            append(f"Error: {event.text}", "error")
        else:
            append(event.text, "system")

    session.set_sink(lambda event: events.put(event))

    def confirm(prompt: str) -> bool:
        answer: dict[str, bool] = {}
        done = threading.Event()

        def ask() -> None:
            try:
                answer["ok"] = messagebox.askyesno(
                    "Approve this action?", prompt, parent=root
                )
            finally:
                done.set()

        calls.put(ask)
        done.wait(timeout=120)
        return bool(answer.get("ok", False))

    session.set_confirm(confirm)

    def poll() -> None:
        if closing.is_set():
            return
        while True:
            try:
                call = calls.get_nowait()
            except queue.Empty:
                break
            try:
                call()
            except Exception:
                pass
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            render(event)
        root.after(80, poll)

    def send() -> None:
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, "end")
        append(f"You: {text}", "user")
        session.submit_text(text)

    widgets["send"].configure(command=send)
    entry.bind("<Return>", lambda _event: send())

    def talk() -> None:
        session.request_listen()

    def checkup() -> None:
        session.run_checkup()

    def stop_talking() -> None:
        session.stop_speaking()

    def open_audit() -> None:
        path = session.audit.path
        if not path.exists():
            messagebox.showinfo("Audit log", "No actions have been recorded yet.")
            return
        try:
            from openjarvis.pc.windows import open_target

            open_target(str(path))
        except Exception as exc:
            messagebox.showerror("Audit log", str(exc))

    def toggle_startup() -> None:
        if widgets["startup_var"].get():
            ok, message = startup.install_startup()
        else:
            ok, message = startup.uninstall_startup()
        if not ok:
            widgets["startup_var"].set(not widgets["startup_var"].get())
            messagebox.showerror("Startup", message)
            return
        append(message, "system")

    widgets["startup_toggle"].configure(command=toggle_startup)

    for label, command, accent in (
        ("Talk", talk, True),
        ("Check my PC", checkup, False),
        ("Stop talking", stop_talking, False),
        ("Audit log", open_audit, False),
    ):
        _button_widget = tk.Button(
            actions,
            text=label,
            command=command,
            bg=_ACCENT if accent else _PANEL,
            fg="#04222a" if accent else _FG,
            activebackground=_ACCENT if accent else "#1b2833",
            activeforeground="#04222a" if accent else _FG,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        _button_widget.pack(side="left", padx=(0, 8))

    def quit_app() -> None:
        closing.set()
        session.stop()
        root.destroy()

    tk.Button(
        actions,
        text="Quit",
        command=quit_app,
        bg=_PANEL,
        fg=_DIM,
        activebackground="#1b2833",
        activeforeground=_FG,
        relief="flat",
        bd=0,
        padx=14,
        pady=7,
        font=("Segoe UI", 9),
        cursor="hand2",
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", quit_app)
    append(
        f"J.A.R.V.I.S. online — engine {session.engine_name}, model {session.model}, "
        f"{session.policy.describe()}.",
        "system",
    )
    if not session.voice_ready:
        append(f"Voice input unavailable: {session.voice_error}", "error")

    session.start()
    root.after(80, poll)
    try:
        root.mainloop()
    finally:
        closing.set()
        session.stop()


__all__ = ["run_ui"]
