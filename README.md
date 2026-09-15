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
| `sample_rate` | 48000 | the working rate: requested from the API, used for local generation, join, mix and delivery |
| `channels` | 1 | layout of generated material (mono speech); join and mix widen to stereo when an input is stereo |
| `default_lufs` | null | per-clip loudness target in `join` (null = none) |
| `confirm_above_chars` | 2000 | generations above this need `--yes`; 0 = always ask; 100000 = never ask |
| `trim_threshold_db` | -40 | silence threshold for trim and verify |
| `trim_margin_ms` | 50 | quiet audio kept on each side of the voiced content |
| `verify_tolerance_ms` | 10 | allowed gap deviation |
| `truncation_db` | -30 | a render whose last 30 ms peak above this is truncated |
| `short_line_chars` / `short_line_model` | 60 / `eleven_multilingual_v2` | short lines render on a model that does not truncate |
| `turn_gap_min` / `turn_gap_max` | 0.15 / 0.45 | default range of dialogue turn gaps, seconds |
| `reference_partner` | null | voice id of the fixed partner in contrast auditions |
| `voices` | `{}` | `{"en": {"narrator": "<voice id>"}}` aliases per language |

## Commands

| command | what it does | credits |
|---|---|---|
| `account` | tier, credits used and limit, reset date, voice slots | free |
| `models [--diff]` | live model list with character limits; `--diff` against the CLI's table | free |
| `voices list [--search]` | voices in your account | free |
| `voices search --language en --search meditation ...` | the shared library (`--preview-dir` downloads previews) | free |
| `voices sample <ids> --out-dir DIR` | a 20-second sample per voice | text length per voice |
| `voices add <id> --owner <public_owner_id> --name N` | add a library voice | free |
| `voices get <voice>` / `voices delete <id>` | details / remove | free |
| `tts --text ... --voice V --out f.wav` | speech, lossless at the working rate, chunked under the model limit | characters |
| `clips --file lines.txt --voice V --out-dir DIR` | one trimmed clip per line plus `manifest.json` | characters |
| `dialogue --file script.txt --speaker "Ann=V1" --speaker "Bob=V2,model=eleven_multilingual_v2,speed=0.9" --out d.wav` | multi-speaker with natural turn-taking (per turn, segments, or single request) | characters |
| `voices audition --use-case UC --protocol isolation --voices V... --grid default` | render candidates on a fixed protocol into the voice lab; `voices rate`, `voices shortlist`, `voices index` | characters per trial |
| `sfx --text "rain on a tent" --duration 10 --out rain.wav` | sound effect | per generation |
| `music --prompt "soft piano" --seconds 60 --out bed.wav` | music, stereo | per generation |
| `noise --color brown --seconds 30 --out bed.wav` | white, pink, brown (red), blue or violet noise, local | none |
| `join a.wav silence:3 b.wav overlap:0.3 c.wav --out out.m4a` | a timeline of trimmed clips with exact silences and overlaps, edge fades, headroom, timing sidecar, verified | none |
| `verify out.m4a out.timing.json` | measure every gap against the sidecar | none |
| `mix piece.m4a --bed rain.wav:-18 --duck 6 --out export.m4a` | beds under a joined piece: loop, fades, gain, ducking | none |
| `measure file.m4a [--timing sidecar]` | duration, rate, layout, LUFS, true peak, range, gap table | none |
| `stt file.mp3 [--diarize] [--language fr]` | transcription | per audio hour |
| `isolate noisy.wav --out clean.mp3` | remove background noise (input at least 4.6 s) | per audio minute |
| `sts take.wav --voice V --out take2.wav` | same performance, another voice | per audio second (high minimum) |
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

## One rate, lossless until delivery

Everything works at `sample_rate` (48 kHz by default). Speech and dialogue are requested from the API as `wav_48000`, sound effects and music as `pcm_48000` wrapped into a WAV, local noise and silence are generated at it, and `join` and `mix` convert any foreign input (a 44.1 kHz recording, a 192 kHz bed) exactly once. Intermediates are 24-bit WAV. The container of the final file follows the `--out` extension: `.wav` or `.flac` for a master, `.m4a` (AAC, 128 kbps mono, 192 kbps stereo) for phones and podcasts, `.mp3` or `.opus` when a platform asks for them. Speech is mono; the output turns stereo as soon as a bed or music is stereo.

Why 48 kHz: ElevenLabs resamples server-side, so any rate is free of charge; lossless WAV is served at 48 kHz on every paid tier while 44.1 kHz WAV and raw PCM are Pro-only; phones mix at 48 kHz; video expects it. `--rate` on `join`, `mix` and `noise` overrides it for a one-off export.

## Truncation guard

v3 decides itself when speech ends and drops the tail of the last utterance of a request, whatever its length. Every render is measured: the last 30 ms must sit below `truncation_db` (-30 dB) and 20 dB under the clip's own peak, or the command fails and names the file (`--allow-truncated` downgrades it to a warning). Lines shorter than `short_line_chars` render on `short_line_model` (v2, which does not truncate); `dialogue` renders v3 lines with a sacrificial closing sentence and cuts at the silence between the two.

## Dialogue with natural turn-taking

A script is one line per turn, `Name: text`, optionally starting with `[quick]`, `[gap 1.2]` or `[overlap 0.4]`. Unmarked turns get a seeded gap chosen by rule (after a question 0.10..0.30 s, a reply of three words or fewer 0.10..0.25, after a long statement 0.35..0.70, the same speaker continuing 0.40..0.80, otherwise `turn_gap_min..turn_gap_max`), so no two turns are alike and the same script re-renders identically. Three mechanisms: `--mode per-turn` (default; one request per line with the speaker's own model, settings and seed), `--mode segments` (the v3 dialogue endpoint's single stream cut at its voice segments and re-timed), `--mode single` (the endpoint's output as is). `--dry-run` prints the turn plan with the reason for every gap and the credits.

## The voice lab

Casting is measured, not guessed. The config directory doubles as the lab: it holds fixed audition protocols per use case (`isolation`, `longform`, `contrast`), every rendered trial as audio plus a JSON with voice card, model, SDK version, settings, seed, date and measurements, the user's verdicts, dated findings, and a derived index. `voices audition` renders, `voices rate` records a verdict, `voices shortlist` ranks, `voices index` rebuilds. A trial rated years ago stays comparable with one rendered today on the same protocol.

## Exact pauses

A TTS clip carries its own leading and trailing silence, different at every request, so a pause built from tags or from raw clips is never exact. `join` therefore:

1. **trims** every clip to its voiced content (ffmpeg `silencedetect` at `trim_threshold_db`, keeping up to `trim_margin_ms` of quiet audio on each side so soft onsets survive),
2. **inserts** digital silence, shortened by the margins its neighbours keep, so the gap between voiced content equals the spec,
3. **verifies** the result by measuring, at the position the sidecar records for each gap, the silence with the same threshold, and fails loudly when one is off by more than `verify_tolerance_ms`.

`verify` against a plain spec file (no sidecar) matches silences in order and can be confused by a long pause inside speech; the sidecar is the reliable reference. Beds go in **after** `join` and `verify`, with `mix`: once rain sits under the voice there are no measurable gaps left, which is why the speech-only file stays the timing reference.

```sh
elevenlabs-cli tts --text "Let the body settle." --voice narrator --seed 7 --out a.wav
elevenlabs-cli tts --text "Notice the breath."   --voice narrator --seed 7 --out b.wav
elevenlabs-cli join a.wav silence:3 b.wav --out settle.m4a
# saved: /home/me/audio/settle.m4a
# timing: /home/me/audio/settle.timing.json (2 clips, 1 gaps, 8.412 s, 48000 Hz, mono)
# gap  expected s  measured s  delta    status
# 1    3.000       3.001       +1.2 ms  ok
```

A spec file works too (`--spec`): lines `file <path>` and `silence <seconds>`, paths relative to the file.

Two ways to deliver timing, on the same trimmed clips:

- **baked**: `join` writes the silences into the file (a podcast, a guided meditation), and `mix` prints a bed into an export;
- **app-timed**: `clips` ships atomic trimmed clips with a manifest (id, text, duration, sha256, kept margins) and the player inserts the silence at run time (a breathing exercise whose exhale the user sets, a review prompt with a user-set answer time). Beds for an app stay separate loops the app mixes under the piece, so the user can pick and level them.

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

Tests mock the SDK at the `client.py` boundary (the only module importing it) and run real ffmpeg round trips on generated tones (trim, join, verify, noise, mix, loudness); those are skipped when ffmpeg is absent.

## Licence

MIT.
