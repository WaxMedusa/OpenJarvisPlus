# Resident JARVIS (`jarvis wake`)

`jarvis wake` turns the double-clap script into a long-running operator: it
listens on one microphone, runs the arrival ritual, reads you a checkup, and
then acts on this PC when you ask.

It is still terminal-first — the desktop panel is optional — and it keeps the
ElevenLabs voice and welcome line from the original script.

## What a clap does

| Trigger | Behaviour |
| --- | --- |
| First double clap | Plays your track, opens any configured pages, focuses Cursor and sends F11, speaks the welcome line, then reads the machine checkup. |
| Later double claps | Opens the microphone for one request. No window focus changes. |
| Typing | Works at any time, even while a task runs. |
| `/brief` | Re-reads the checkup. |
| `/status` | Engine, model, autonomy and voice state. |
| `/audit` | Last ten actions with their approval result. |
| `/quit` | Ends the session. |

Claps are ignored while Jarvis is speaking, so your own speakers cannot trigger
it. `Ctrl+C` stops the current speech; press it again (or use `/quit`) to exit.

## Setup

```bash
pip install -e ".[speech]"        # sounddevice, soundfile, numpy, faster-whisper
```

The voice comes from the same environment variables as before:

```bash
export ELEVENLABS_API_KEY=...     # read from the environment at startup
export ELEVENLABS_VOICE_ID=...    # Windows: use setx so the Startup shortcut sees it
```

Then point OpenJarvis at ElevenLabs:

```toml
# ~/.openjarvis/config.toml
[speech]
tts_backend = "elevenlabs"
voice_id = "<your ELEVENLABS_VOICE_ID>"
model = "small"            # whisper model for voice input

[wake]
song_uri = "https://open.spotify.com/track/..."
open_urls = ["https://calendar.google.com"]
brief_on_wake = true

[pc]
autonomy = "ask"           # ask | trusted | read_only
```

`ELEVENLABS_MODEL_ID` from the old script is honoured as a fallback for the
model, and `JARVIS_INPUT_DEVICE` still selects a microphone by index or name.

## Talking to it

```bash
jarvis wake
```

Then, by voice or by typing:

- `check my Steam games and update anything that needs it`
- `what needs updating on this PC?`
- `open Steam and bring it to my second monitor`
- `find my latest invoice PDF`
- `what did you do while I was away?` (`/audit` shows the same thing)

The operator reads Steam's own manifest files to detect updates, starts them
through Steam's downloads page, and then **verifies** by polling those manifests
until the update bits clear. It does not claim success because it clicked
something.

## Autonomy

| Mode | Reads | Launch/focus | Shell, installs, downloads |
| --- | --- | --- | --- |
| `read_only` | yes | no | no |
| `ask` (default) | yes | yes | asks first |
| `trusted` | yes | yes | yes |

Destructive shell patterns (disk format, shutdown, recursive deletes, account
changes, boot configuration) are refused in every mode. Every call — allowed,
approved, denied or failed — is appended to `~/.openjarvis/wake/audit.jsonl`
with credential-shaped fields redacted.

Use `--dry-run` to see the plan without touching anything:

```bash
jarvis wake --dry-run
```

## Desktop panel and startup

```bash
jarvis wake --ui                  # opens the panel instead of the terminal
jarvis wake --install-startup     # shortcut in the Windows Startup folder
jarvis wake --uninstall-startup
```

The panel runs the same session: status line, transcript, **Talk**,
**Check my PC**, **Stop talking**, **Audit log**, and a **Start with Windows**
toggle. Launching at startup uses `pythonw`, so no console window appears.

## Other useful flags

```bash
jarvis wake --checkup             # print the checkup and exit
jarvis wake --once < request.txt  # one request, then exit
jarvis wake --no-ritual           # clap goes straight to listening
jarvis wake --no-voice            # typed input only
jarvis wake --trusted             # no prompts for this run
jarvis wake --list-tools          # show the PC toolset
```

## Troubleshooting

- **No clap reaction:** set `JARVIS_INPUT_DEVICE` to the right microphone first,
  then clap closer to it.
- **"Voice input unavailable":** install the speech extra; `sounddevice` and
  `faster-whisper` are required for the microphone path.
- **Silent welcome:** check `ELEVENLABS_API_KEY` and the voice ID. Cached audio
  lives in `~/.openjarvis/wake/tts_cache/`.
- **Steam not found:** set `[pc] steam_root`.
- **A tool was blocked:** that is the policy, not a crash. Run with
  `--trusted` only for actions you actually want.
