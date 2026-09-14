# elevenlabs-cli

A command-line interface over the official [ElevenLabs Python SDK](https://github.com/elevenlabs/elevenlabs-python), for people (and coding agents) who generate audio from a terminal: speech, multi-speaker dialogue, sound effects, transcription, voice search and design, plus **exact silence assembly** with ffmpeg.

Why a CLI and not the MCP server: the hosted MCP keeps only text-to-speech and the local server is archived. A CLI over the maintained SDK is easy to keep current, scriptable from any tool, and carries its own guardrails (spend confirmation, explicit output paths, no guessed voice ids, the key never printed).

## Install

Requires Python 3.10+ and, for `trim`/`join`/`verify`/`clips` and any non-MP3 output, [ffmpeg](https://ffmpeg.org/).

```sh
uv tool install git+https://github.com/benjaminpeeters/elevenlabs-cli   # or: pipx install ...
elevenlabs-cli config init
```

`config init` writes `~/.config/elevenlabs-cli/config.json` (override with `$ELEVENLABS_CLI_CONFIG` or `--config`). The API key is **never** stored there: `api_key_env` names an environment variable (default `ELEVENLABS_API_KEY`) and `api_key_file` optionally names a file holding a bare key or `KEY=value` lines.

```sh
elevenlabs-cli config init --api-key-file ~/.secrets/elevenlabs.env
elevenlabs-cli config show                      # everything except the key
elevenlabs-cli config set confirm_above_chars 5000
elevenlabs-cli config set voices.en.narrator 21m00Tcm4TlvDq8ikWAM
```

| key | default | meaning |
|---|---|---|
| `api_key_env` | `ELEVENLABS_API_KEY` | environment variable holding the key |
| `api_key_file` | null | file read when the variable is unset |
| `output_dir` | null | base for relative `--out` paths; null = the current directory |
| `default_model` | `eleven_v3` | model when `--model` is absent |
| `default_format` | `mp3_44100_128` | API output format |
| `default_lufs` | null | loudness target for `join` (null = none) |
| `confirm_above_chars` | 2000 | generations above this need `--yes`; 0 = always ask; 100000 = never ask |
| `trim_threshold_db` | -40 | silence threshold for trim and verify |
| `trim_margin_ms` | 50 | quiet audio kept on each side of the voiced content |
| `verify_tolerance_ms` | 10 | allowed gap deviation |
| `voices` | `{}` | `{"en": {"narrator": "<voice id>"}}` aliases per language |

## Commands

| command | what it does | credits |
|---|---|---|
| `account` | tier, credits used and limit, reset date, voice slots | free |
| `models` | live model list with character limits | free |
| `voices list [--search]` | voices in your account | free |
| `voices search --language en --use-case meditation ...` | the shared library | free |
| `voices sample <ids> --out-dir DIR [--preview]` | one sample per voice (`--preview` downloads the library preview for free) | text length per voice |
| `voices add <id> --owner <public_owner_id> --name N` | add a library voice | free |
| `voices get <voice>` / `voices delete <id>` | details / remove | free |
| `tts --text ... --voice V --out f.mp3` | speech into one file, chunked under the model limit | characters |
| `clips --file lines.txt --voice V --out-dir DIR` | one trimmed clip per line plus `manifest.json` | characters |
| `join a.mp3 silence:3 b.mp3 --out out.m4a` | trimmed clips and exact silences, timing sidecar, verified | none |
| `verify out.m4a out.timing.json` | measure every gap against the spec | none |
| `dialogue --file script.txt --speaker Ann=V1 --speaker Bob=V2 --out d.mp3` | multi-speaker (v3) | characters |
| `sfx --text "rain on a tent" --duration 10 --out rain.mp3` | sound effect | per generation |
| `stt file.mp3 [--diarize] [--language fr]` | transcription | per audio hour |
| `isolate noisy.wav --out clean.mp3` | remove background noise | per audio minute |
| `sts take.wav --voice V --out take2.mp3` | same performance, another voice | per audio second |
| `voice design --description "..." --out-dir DIR` | previews from a description | per call |
| `voice create --name N --description D --generated-id ID` | save a preview as a voice | free |
| `voice clone --name N --sample a.wav --sample b.wav` | instant clone | a voice slot |

Global flags: `--json` (machine-readable output where supported), `--yes` (skip the spend confirmation), `--config PATH`.

Output paths are always explicit: `--out` or `--out-dir` is required, a relative path lands under `output_dir` (or the current directory when it is unset), and the absolute path written is printed.

Every generating command prints the character count and the credit estimate first. Above `confirm_above_chars` it asks on a terminal and **refuses without `--yes`** when there is none, so a script or an agent cannot spend silently. `--dry-run` on `tts`, `clips` and `dialogue` prints the estimate and stops.

A `--voice` is an alias from the config, the exact name of one of your voices, or a voice id. Anything else is an error: the tool never guesses a voice.

Input paths (`join` items, `stt`, `isolate`, `sts`) are relative to the current directory like any command-line tool.

## Models

| model | credits/char | max chars/request | audio tags | request stitching |
|---|---|---|---|---|
| `eleven_v3` | 1 | 5000 | yes (`[whispers]`, `[pause]`...) | no |
| `eleven_multilingual_v2` | 1 | 10000 | no (SSML `<break>`) | yes |
| `eleven_flash_v2_5` | 0.5 | 40000 | no | yes |
| `eleven_turbo_v2_5` | 0.5 | 40000 | no | yes |

Long text is split under the limit: by paragraph for v3 (each request stands on its own; use a fixed `--seed` and fixed settings so the delivery stays consistent), by sentence with stitching for the others. A single paragraph or sentence over the limit is an error rather than a mid-sentence cut.

## Exact pauses

A TTS clip carries its own leading and trailing silence, different at every request, so a pause built from tags or from raw clips is never exact. `join` therefore:

1. **trims** every clip to its voiced content (ffmpeg `silencedetect` at `trim_threshold_db`, keeping up to `trim_margin_ms` of quiet audio on each side so soft onsets survive),
2. **inserts** digital silence, shortened by the margins its neighbours keep, so the gap between voiced content equals the spec,
3. **verifies** the result by measuring every gap with the same threshold and fails loudly when one is off by more than `verify_tolerance_ms`.

```sh
elevenlabs-cli tts --text "Let the body settle." --voice narrator --seed 7 --out a.mp3
elevenlabs-cli tts --text "Notice the breath."   --voice narrator --seed 7 --out b.mp3
elevenlabs-cli join a.mp3 silence:3 b.mp3 --out settle.m4a
# saved: /home/me/audio/settle.m4a
# timing: /home/me/audio/settle.timing.json (2 clips, 1 gaps, 8.412 s)
# gap  expected s  measured s  delta    status
# 1    3.000       3.001       +1.2 ms  ok
```

A spec file works too (`--spec`): lines `file <path>` and `silence <seconds>`, paths relative to the file.

Two ways to deliver timing, on the same trimmed clips:

- **baked**: `join` writes the silences into the file (a podcast, a guided meditation);
- **app-timed**: `clips` ships atomic trimmed clips with a manifest (id, text, duration, sha256, kept margins) and the player inserts the silence at run time (a breathing exercise whose exhale the user sets, a review prompt with a user-set answer time).

## Languages

All commands are language-agnostic. `--language` takes an ISO 639-1 code on `tts`, `clips`, `dialogue`, `stt` and `voices search`; on `tts` it also selects which alias table of the config applies (`voices.fr.narrator` versus `voices.en.narrator`). Pass it explicitly for Chinese, Japanese and mixed-script text so numbers and names are normalised in the right language.

```sh
elevenlabs-cli voices search --language fr --gender female --use-case narration
elevenlabs-cli tts --language fr --voice narrateuse --file chapitre.txt --out chapitre.mp3
elevenlabs-cli tts --language zh --voice 老师 --text "今天我们学习三个新词。" --out lesson.mp3
```

## Using it from a coding agent

The CLI is designed to be driven by scripts and agent skills: every failure is a clear message and a non-zero exit, estimates go to stderr, `--json` gives structured output, `config show` exposes the limits the agent must respect, and `confirm_above_chars` is the one knob that decides how much it may spend without asking. A skill only needs to read the config, obey the threshold, never invent voice ids, and report the saved path.

## Development

```sh
git clone https://github.com/benjaminpeeters/elevenlabs-cli && cd elevenlabs-cli
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

Tests mock the SDK at the `client.py` boundary (the only module importing it) and run one real ffmpeg round trip on a generated tone; the ffmpeg test is skipped when ffmpeg is absent.

## Licence

MIT.
