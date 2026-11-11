# Jarvis Desktop

Double clap → your machine wakes up. Jarvis listens on the microphone, detects
two sharp claps, then runs a sequence you configure: open a song, open Chrome
windows on specific monitors, speak a line through ElevenLabs, and bring Cursor
to the front. Everything runs locally; the UI is served from `127.0.0.1` only.

This is the productised version of `jarvis-main/jarvis-main/jarvis.py`: same
detection idea and Windows window handling, but with a backend, a settings UI,
an event stream, tests, and a Windows installer.

## What is included

| Piece | Where |
| --- | --- |
| FastAPI backend (REST + WebSocket + static UI) | `jarvis_desktop/` |
| Microphone capture + clap detector | `jarvis_desktop/listener.py`, `detector.py` |
| Launch sequence, Chrome/Cursor placement, ElevenLabs voice | `jarvis_desktop/actions.py`, `win32.py` |
| Minimalist UI (no build step, plain HTML/CSS/JS) | `frontend/` |
| PyInstaller app build | `packaging/jarvis-desktop.spec` |
| Minimal per-user Inno Setup installer | `packaging/installer.iss` |
| One-command Windows build | `packaging/build.ps1` |
| Windows CI build | `.github/workflows/jarvis-desktop.yml` |

## Run from source

```bash
cd jarvis-desktop
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
uv run --python .venv/bin/python jarvis-desktop
```

On Windows:

```powershell
cd jarvis-desktop
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
jarvis-desktop
```

The server binds `127.0.0.1:8765` (first free port in the next 20 if taken) and
opens `http://127.0.0.1:8765/` in your default browser. `--no-browser`,
`--host` and `--port` are available.

### Data locations

| Platform | Directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Jarvis` |
| macOS | `~/Library/Application Support/Jarvis` |
| Linux | `~/.local/share/jarvis` |

`settings.json` holds all non-secret settings, `secrets.json` holds the
ElevenLabs key (written `0600` where supported), `voice-cache/` keeps generated
WAVs and `jarvis.log` is the runtime log. Override with `JARVIS_DATA_DIR`.

## Build the Windows `.exe` and installer

PyInstaller cannot cross-compile, so the `.exe` must be built on Windows (or by
the included CI workflow).

```powershell
cd jarvis-desktop
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The script installs `.[dev,build]`, runs the tests, builds
`dist\Jarvis\Jarvis.exe` with PyInstaller, then compiles
`dist\JarvisSetup-<version>.exe` with Inno Setup 6. Requirements: Python
3.10–3.13 and [Inno Setup 6](https://jrsoftware.org/isinfo.php).

The installer is per-user (`%LOCALAPPDATA%\Programs\Jarvis`, no admin prompt),
adds Start Menu/optional desktop shortcuts, offers an optional sign-in autostart
entry that runs with `--no-browser`, and can launch the app at the end. It is
unsigned: Windows SmartScreen will warn on first run until you sign the binary.

To use a custom icon, drop `packaging/jarvis.ico` before building; the spec picks
it up automatically.

CI: `.github/workflows/jarvis-desktop.yml` runs the same build on
`windows-latest` (manual dispatch or changes under `jarvis-desktop/`) and
uploads the installer as an artifact.

## Use

1. Click **Start listening** (or leave auto-start on).
2. Watch the meter: the yellow marker is the clap threshold, derived from the
   room noise floor. Clap twice inside the gap window.
3. Every run is shown in **Activity**; the event feed is also on `/api/events`.

Settings save automatically as you edit. Changing audio settings restarts the
listener if it is running.

### Tuning

| Setting | Effect |
| --- | --- |
| Spike ratio | Higher = fewer false triggers; lower = catches softer claps. |
| Min RMS | Absolute loudness floor; raise it in very quiet rooms. |
| Max gap | Longest allowed spacing between the two claps. |
| Cooldown | Debounce between accepted double claps. |

A lone clap expires after `max_gap_s`; it is not held indefinitely.

## API

All endpoints are local and unauthenticated by design; mutations reject
cross-origin requests. The UI is same-origin.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Version, settings (key masked), listener state, paths |
| PUT | `/api/settings` | Validate + save settings; restarts capture if audio changed |
| GET | `/api/devices` | Input devices, or an error when audio is unavailable |
| POST | `/api/listener/start` / `/stop` | Control capture |
| POST | `/api/trigger` | Run the sequence now |
| POST | `/api/voice/preview` | Speak the configured phrase |
| PUT/DELETE | `/api/voice/key` | Store/clear the ElevenLabs key |
| GET | `/api/events` | Recent event history |
| WS | `/ws` | Snapshot + live `level`, `clap`, `double-clap`, `action`, `sequence`, `listener` events |

## Tests

```bash
cd jarvis-desktop
pytest tests -q
```

The detector tests are pure math; the API tests use FastAPI's `TestClient` and
run without a microphone. `sounddevice` is imported lazily, so a machine with no
PortAudio still starts and reports the problem in the UI instead of crashing.

## Notes and limits

- Monitor numbers are 1-based, sorted left-to-right then top-to-bottom (the same
  convention as the original script). Window snapping and F11 fullscreen are
  Windows-only; other platforms fall back to normal browser launching.
- Chrome "new window" placement waits for a new top-level Chrome window
  (20s timeout). Chrome's own `--window-position` is also passed, so the window
  lands on the right monitor even if detection times out.
- The voice step needs an ElevenLabs API key and voice ID. Audio is cached by
  text + voice + model + format, so replays cost no API calls.
- The app binds loopback only. Do not expose `--host 0.0.0.0` on an untrusted
  network: there is no login.
