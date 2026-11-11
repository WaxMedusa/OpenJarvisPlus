"""The resident JARVIS session.

One process owns:

* a single microphone stream (double-clap detector + utterance recorder),
* a worker thread that talks to the operator agent and the TTS speaker,
* a task board for long jobs (Steam/winget), and
* a pluggable sink so either the terminal or the desktop panel can render
  events without the session knowing which one is attached.

The loop never exits on a failed action: a missing engine, a dead microphone or
a denied tool is reported and the session stays up. That is the difference
between "a script that runs once" and "an assistant that is just there".
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from openjarvis.core.config import load_config
from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.pc.audit import AuditLog
from openjarvis.pc.brief import collect_brief
from openjarvis.pc.policy import AutonomyPolicy
from openjarvis.pc.tasks import BackgroundTasks

_SYSTEM_HELP = (
    "Commands: /brief  /status  /audit  /listen  /ritual  /help  /quit\n"
    "Double clap: first one runs the arrival ritual and the checkup, later ones "
    "open the microphone for a request."
)


@dataclass(frozen=True)
class SessionEvent:
    kind: str  # status | user | assistant | brief | task | error | system
    text: str = ""
    detail: str = ""


class WakeSession:
    """Long-running voice operator session."""

    def __init__(
        self,
        config: Any = None,
        *,
        dry_run: bool = False,
        trusted: bool = False,
        no_ritual: bool = False,
        no_voice: bool = False,
        model_name: str = "",
        silent: bool = False,
    ) -> None:
        self.config = config or load_config()
        self.dry_run = dry_run
        self.no_ritual = no_ritual
        self.no_voice = no_voice
        self.silent = silent

        self._sink: Optional[Callable[[SessionEvent], None]] = None
        self._confirm: Callable[[str], bool] = self._terminal_confirm
        self._stop = threading.Event()
        self._queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._listen_flag = threading.Event()
        self._use_stdin = False
        self._pending_approval: Optional[tuple[threading.Event, dict]] = None
        self._approval_lock = threading.Lock()
        self._history: List[Message] = []
        self._threads: List[threading.Thread] = []
        self._started = False
        self._ritual_done = False
        self._voice_error = ""
        self._mic_ready = threading.Event()

        self.audit = AuditLog()
        self.board = BackgroundTasks()

        # Engine + model -----------------------------------------------------
        from openjarvis.engine import get_engine
        from openjarvis.intelligence import register_builtin_models

        register_builtin_models()
        self._engine_error = ""
        try:
            resolved = get_engine(self.config)
        except Exception as exc:
            resolved = None
            self._engine_error = f"{type(exc).__name__}: {exc}"
        intelligence = getattr(self.config, "intelligence", None)
        self.model = (
            model_name
            or getattr(intelligence, "model_chat", "")
            or getattr(intelligence, "default_model", "")
            or "(no model configured)"
        )
        self.engine = None
        self.engine_name = "unavailable"
        if resolved is None:
            if not self._engine_error:
                self._engine_error = (
                    "No inference engine is available. Start Ollama (or configure a "
                    "cloud model); the ritual, microphone and checkup still work."
                )
        else:
            self.engine_name, self.engine = resolved

        self._bus = EventBus(record_history=False)

        # Policy + tools -----------------------------------------------------
        pc_config = getattr(self.config, "pc", None)
        configured_mode = getattr(pc_config, "autonomy", "ask") or "ask"
        mode = "trusted" if trusted else configured_mode
        if mode not in ("ask", "trusted", "read_only"):
            mode = "ask"
        self.policy = AutonomyPolicy(
            mode,
            dry_run=dry_run,
            deny_patterns=getattr(pc_config, "deny_patterns", None) or None,
            allow_shell=bool(getattr(pc_config, "allow_shell", True)),
        )

        from openjarvis.tools.pc import pc_tools

        self.tools = pc_tools(config=self.config, board=self.board, audit=self.audit)
        self.agent: Any = None
        if self.engine is not None:
            from openjarvis.agents.pc_operator import PCOperatorAgent

            self.agent = PCOperatorAgent(
                self.engine,
                self.model,
                tools=self.tools,
                bus=self._bus,
                policy=self.policy,
                confirm=lambda prompt: self._confirm(prompt),
                audit=self.audit,
            )

        # Voice --------------------------------------------------------------
        from openjarvis.speech.announce import Speaker

        self.speaker = Speaker(self.config, on_status=self._speaker_status)
        self._stt: Any = None
        self._stt_language: Optional[str] = None
        if not no_voice:
            self._setup_speech()
        else:
            self._voice_error = "Voice input disabled with --no-voice."

        # Memory -------------------------------------------------------------
        self._memory: Any = None
        if self.engine is not None:
            try:
                from openjarvis.memory import build_memory_service

                self._memory = build_memory_service(
                    self.config, self.engine, self.model, event_bus=self._bus
                )
            except Exception:
                self._memory = None

    # ------------------------------------------------------------------
    # Public surface used by the CLI / UI
    # ------------------------------------------------------------------
    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def voice_ready(self) -> bool:
        return self._stt is not None and not self._voice_error

    @property
    def voice_error(self) -> str:
        return self._voice_error

    def set_sink(self, sink: Callable[[SessionEvent], None]) -> None:
        self._sink = sink

    def set_confirm(self, confirm: Callable[[str], bool]) -> None:
        self._confirm = confirm

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._memory is not None and hasattr(self._memory, "start"):
            try:
                self._memory.start()
            except Exception:
                pass
        worker = threading.Thread(
            target=self._worker, name="jarvis-wake-loop", daemon=True
        )
        worker.start()
        self._threads.append(worker)
        if not self.no_voice:
            mic = threading.Thread(
                target=self._mic_loop, name="jarvis-wake-mic", daemon=True
            )
            mic.start()
            self._threads.append(mic)
        if self._use_stdin:
            stdin_thread = threading.Thread(
                target=self._stdin_loop, name="jarvis-wake-stdin", daemon=True
            )
            stdin_thread.start()
            self._threads.append(stdin_thread)
        self._emit("status", "Listening for a double clap")
        wake_config = getattr(self.config, "wake", None)
        if self.no_ritual and getattr(wake_config, "brief_on_wake", True):
            self._queue.put(("checkup", None))

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.board.shutdown()
        self.speaker.stop()
        with self._approval_lock:
            pending = self._pending_approval
        if pending is not None:
            done, box = pending
            box["ok"] = False
            done.set()
        try:
            self._queue.put_nowait(("quit", None))
        except queue.Full:
            pass
        if self._memory is not None and hasattr(self._memory, "stop"):
            try:
                self._memory.stop()
            except Exception:
                pass
        current = threading.current_thread()
        for thread in self._threads:
            if thread is current:
                continue
            thread.join(timeout=1.5)
        self._emit("status", "Stopped")

    def submit_text(self, text: str) -> None:
        self._queue.put(("text", (text or "").strip()))

    def request_listen(self) -> None:
        if not self.voice_ready:
            self._emit(
                "error",
                "Microphone input is unavailable: "
                + (self._voice_error or "no speech-to-text backend."),
            )
            return
        self._listen_flag.set()
        self._emit("status", "Listening…")

    def run_checkup(self) -> None:
        self._queue.put(("checkup", None))

    def stop_speaking(self) -> None:
        self.speaker.stop()
        self._emit("status", "Stopped speaking")

    def run_text(self, text: str) -> None:
        """Process one request synchronously (used by ``--once``)."""
        self._handle_text(text)

    # ------------------------------------------------------------------
    # Terminal frontend
    # ------------------------------------------------------------------
    def run_terminal(self) -> None:
        self._print_banner()
        self._use_stdin = True
        self.start()
        try:
            while not self._stop.is_set():
                try:
                    self._stop.wait(0.2)
                except KeyboardInterrupt:
                    if self.speaker.speaking.is_set():
                        self.stop_speaking()
                        continue
                    self._emit("system", "Interrupted.")
                    break
        finally:
            self.stop()

    def _stdin_loop(self) -> None:
        """Sole reader of stdin in terminal mode.

        Approval replies are consumed here while a confirmation is pending;
        everything else becomes a chat request. Keeping one reader avoids two
        threads fighting over the console.
        """
        try:
            while not self._stop.is_set():
                line = sys.stdin.readline()
                if line == "":
                    self._emit("system", "Input closed; shutting down.")
                    self._stop.set()
                    self._queue.put(("quit", None))
                    return
                text = line.strip()
                with self._approval_lock:
                    pending = self._pending_approval
                if pending is not None:
                    done, box = pending
                    if not done.is_set():
                        box["ok"] = text.lower() in (
                            "y",
                            "yes",
                            "yeah",
                            "ok",
                            "okay",
                            "do it",
                            "go ahead",
                        )
                        done.set()
                    continue
                self._queue.put(("text", text))
        except KeyboardInterrupt:
            self._stop.set()
            self._queue.put(("quit", None))

    def _print_banner(self) -> None:
        voice = "ready" if self.voice_ready else f"unavailable ({self._voice_error})"
        print("=" * 66)
        print("  J.A.R.V.I.S.  —  resident operator")
        print(f"  engine: {self.engine_name}   model: {self.model}")
        print(f"  {self.policy.describe()}   voice: {voice}")
        print("-" * 66)
        print("  Clap twice to wake me. Type anytime; /help for commands.")
        print("=" * 66)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                kind, payload = self._queue.get(timeout=0.2)
            except queue.Empty:
                self._drain_tasks()
                continue
            if kind == "quit":
                break
            self._drain_tasks()
            try:
                if kind == "text":
                    self._handle_text(str(payload or ""))
                elif kind == "clap":
                    self._handle_clap()
                elif kind == "checkup":
                    self._brief(speak=True)
            except Exception as exc:  # the session must survive any one request
                self._emit("error", f"{type(exc).__name__}: {exc}")
        self._drain_tasks()

    def _drain_tasks(self) -> None:
        for event in self.board.drain():
            if event.kind == "progress":
                self._emit("task", f"{event.name}: {event.message}")
            elif event.kind == "started":
                self._emit("task", f"Started: {event.name}")
            elif event.kind == "done":
                self._emit("assistant", event.message)
                self._speak(event.message)
            elif event.kind == "error":
                self._emit("error", f"{event.name}: {event.message}")
                self._speak(f"{event.name} failed.")
            elif event.kind == "cancelled":
                self._emit("system", f"{event.name}: {event.message}")

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    def _handle_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if text.startswith("/"):
            self._slash(text)
            return

        self._emit("user", text)
        if self.agent is None:
            self._emit(
                "error",
                self._engine_error or "No inference engine is available.",
            )
            self._speak("I have no model available right now.")
            return
        from openjarvis.agents._stubs import AgentContext

        context = AgentContext()
        for message in self._history[-12:]:
            context.conversation.add(message)
        try:
            result = self.agent.run(text, context=context)
        except Exception as exc:
            self._emit("error", f"The model call failed: {type(exc).__name__}: {exc}")
            self._speak("I could not reach the model.")
            return

        content = str(getattr(result, "content", result) or "").strip()
        if not content:
            content = "I finished, but I have nothing to report."
        self._history.append(Message(role=Role.USER, content=text))
        self._history.append(Message(role=Role.ASSISTANT, content=content))
        del self._history[:-16]
        self._emit("assistant", content)
        self._speak(content)
        if self._memory is not None and hasattr(self._memory, "submit"):
            try:
                self._memory.submit(text, content)
            except Exception:
                pass

    def _handle_clap(self) -> None:
        if not self._ritual_done and not self.no_ritual:
            self._ritual_done = True
            self._emit("status", "Coming home…")
            from openjarvis.speech.ritual import run_welcome_ritual

            run_welcome_ritual(
                self.config,
                self.speaker,
                lambda message: self._emit("system", message),
                dry_run=self.dry_run,
            )
            wake_config = getattr(self.config, "wake", None)
            if getattr(wake_config, "brief_on_wake", True):
                self._brief(speak=True)
            return
        self._ritual_done = True
        self._emit("assistant", "Yes, sir?")
        self._speak("Yes, sir?")
        self.request_listen()

    def _brief(self, *, speak: bool) -> None:
        brief = collect_brief(self.config)
        self._emit("brief", brief.speech, "\n".join(brief.lines))
        if speak:
            self._speak(brief.speech)

    def _slash(self, text: str) -> None:
        command = text.strip().lower()
        if command in ("/quit", "/exit", "/q"):
            self._emit("system", "Goodbye.")
            self._stop.set()
            return
        if command == "/help":
            self._emit("system", _SYSTEM_HELP)
            return
        if command == "/brief":
            self._brief(speak=True)
            return
        if command == "/listen":
            self.request_listen()
            return
        if command == "/ritual":
            from openjarvis.speech.ritual import run_welcome_ritual

            run_welcome_ritual(
                self.config,
                self.speaker,
                lambda message: self._emit("system", message),
                dry_run=self.dry_run,
            )
            return
        if command == "/audit":
            self._emit("system", self.audit.describe_last(10))
            return
        if command == "/status":
            voice = "ready" if self.voice_ready else self._voice_error
            self._emit(
                "system",
                f"engine={self.engine_name} model={self.model}\n"
                f"{self.policy.describe()} voice={voice}\n"
                f"audit={self.audit.path}",
            )
            return
        self._emit("system", f"Unknown command {command!r}. " + _SYSTEM_HELP)

    # ------------------------------------------------------------------
    # Microphone
    # ------------------------------------------------------------------
    def _setup_speech(self) -> None:
        try:
            import sounddevice  # noqa: F401
        except Exception as exc:
            self._voice_error = (
                "sounddevice is not installed. Install the speech extra: "
                f'pip install -e ".[speech]" ({type(exc).__name__})'
            )
            return
        try:
            from openjarvis.cli._voice_chat import VoiceSession

            session = VoiceSession(self.config)
            self._stt = session.get_stt_backend()
            self._stt_language = session.get_stt_language()
            if self._stt is None:
                self._voice_error = (
                    "No speech-to-text backend. Install faster-whisper with the "
                    "speech extra, or configure OpenAI/Deepgram STT."
                )
        except Exception as exc:
            self._voice_error = (
                f"Speech input failed to initialise: {type(exc).__name__}: {exc}"
            )

    def _mic_loop(self) -> None:
        try:
            import sounddevice as sd

            from openjarvis.speech.clap import (
                ClapConfig,
                ClapDetector,
                choose_input_device,
                pcm_rms_int16,
            )
            from openjarvis.speech.voice_io import record_stream
        except Exception as exc:
            self._voice_error = f"Microphone unavailable: {type(exc).__name__}: {exc}"
            self._emit("error", self._voice_error)
            return

        config = ClapConfig()
        detector = ClapDetector(config)
        device = choose_input_device(sd, config)
        if device is None:
            self._voice_error = "No working microphone was found."
            self._emit("error", self._voice_error)
            return
        try:
            with sd.InputStream(
                device=device,
                samplerate=config.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=config.block_samples,
            ) as stream:
                self._mic_ready.set()
                while not self._stop.is_set():
                    if self.speaker.speaking.is_set():
                        stream.read(config.block_samples)
                        continue
                    if self._listen_flag.is_set():
                        self._listen_flag.clear()
                        self._capture(stream, config, record_stream)
                        continue
                    data, _overflow = stream.read(config.block_samples)
                    if detector.feed(pcm_rms_int16(data)):
                        self._emit("status", "Double clap detected")
                        self._queue.put(("clap", None))
        except Exception as exc:
            self._voice_error = f"Microphone stream failed: {type(exc).__name__}: {exc}"
            self._emit("error", self._voice_error)

    def _capture(self, stream: Any, config: Any, record_stream: Any) -> None:
        self._emit("status", "Listening… (stop talking to finish)")
        try:
            audio = record_stream(
                stream,
                sample_rate=config.sample_rate,
                block_samples=config.block_samples,
                should_stop=lambda: self._stop.is_set(),
            )
        except Exception as exc:
            self._emit("error", f"Recording failed: {type(exc).__name__}: {exc}")
            return
        if len(audio) < 2_000:
            self._emit("system", "I did not hear anything.")
            return
        if self._stt is None:
            self._emit(
                "error", "No speech-to-text backend, so I cannot transcribe that."
            )
            return
        try:
            result = self._stt.transcribe(
                audio, format="wav", language=self._stt_language
            )
            text = str(getattr(result, "text", "") or "").strip()
        except Exception as exc:
            self._emit("error", f"Transcription failed: {type(exc).__name__}: {exc}")
            return
        if not text:
            self._emit("system", "I did not catch that.")
            return
        self._queue.put(("text", text))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _terminal_confirm(self, prompt: str) -> bool:
        # ``--once`` has no stdin thread, so ask inline. The resident terminal
        # mode routes the reply through ``_stdin_loop`` to avoid two readers.
        if not self._use_stdin:
            self._emit("system", prompt)
            try:
                answer = input("  Approve? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            return answer in ("y", "yes", "yeah", "ok", "okay")
        done = threading.Event()
        box: dict = {"ok": False}
        with self._approval_lock:
            self._pending_approval = (done, box)
        self._emit("system", prompt + "\nType yes or no and press Enter.")
        try:
            done.wait(timeout=120)
        finally:
            with self._approval_lock:
                self._pending_approval = None
        self._emit("system", "Approved." if box.get("ok") else "Denied.")
        return bool(box.get("ok", False))

    def _speaker_status(self, status: str) -> None:
        if status == "speaking":
            self._emit("status", "Speaking…")
        elif status == "idle" and self._mic_ready.is_set():
            self._emit("status", "Listening for a double clap")

    def _speak(self, text: str) -> None:
        if self.dry_run or self.silent or not text:
            return
        from openjarvis.speech.announce import speakable

        summary = speakable(text)
        if summary:
            self.speaker.say(summary)

    def _emit(self, kind: str, text: str = "", detail: str = "") -> None:
        event = SessionEvent(kind=kind, text=text, detail=detail)
        if self._sink is not None:
            try:
                self._sink(event)
            except Exception:
                pass
        if self._sink is None:
            prefix = {
                "user": "You",
                "assistant": "JARVIS",
                "system": "·",
                "task": "·",
                "error": "!",
                "status": "",
                "brief": "JARVIS",
            }.get(kind, kind)
            if kind == "status":
                print(f"\r[{time.strftime('%H:%M:%S')}] {text}")
            elif prefix:
                print(f"\n{prefix}: {text}")
            if detail:
                print(detail)


__all__ = ["SessionEvent", "WakeSession"]
