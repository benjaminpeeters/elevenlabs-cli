---
name: generate
description: Generate audio with ElevenLabs through the `elevenlabs-cli` command: text-to-speech in any language, voiceovers, podcasts and multi-speaker dialogue, guided pieces with exact pauses, sound effects, noise beds, transcription (speech-to-text), voice search, design or cloning, audio isolation, and exact-silence assembly (piece, join, verify, clips). Use whenever the user asks to generate, synthesise or assemble spoken audio, a voiceover, a narration, a dialogue, a sound effect or a noise bed. Never build an ElevenLabs pipeline by hand and never look for an API key: the CLI owns both. If the command is missing, say so and ask the user to run `/elevenlabs:setup`.
allowed-tools: Bash(elevenlabs-cli *), Bash(ffprobe *), Read, Write, Edit, AskUserQuestion
---

# ElevenLabs via `elevenlabs-cli`

Every generation spends credits (real money). The CLI carries the guardrails; obey them instead of working around them.

## Before anything

1. Run `elevenlabs-cli config show` once per session. It prints `confirm_above_chars` (the spend threshold), `default_model`, the voice aliases per language and the trim/verify settings. Those values are the user's current wishes. If the command is not found, stop and ask the user to run `/elevenlabs:setup`.
2. Estimate first: `tts`, `piece`, `clips` and `dialogue` accept `--dry-run` (characters, chunks, credits, nothing spent). Sound effects, transcription, isolation, speech-to-speech and voice design are billed per call or per second; the CLI says so on stderr.
3. Confirm with the user before any run whose characters exceed `confirm_above_chars` (or any per-call billing when the threshold is below 100000). Only after they agree pass `--yes`. Without a terminal the CLI refuses instead of asking; that refusal is the signal to ask the user, never a reason to raise the threshold yourself.

## Rules

- Never invent a voice id. `--voice` takes an alias from the config, an exact voice name of the account, or a voice id; find one with `voices list` (account) or `voices search --language xx --search "calm meditation"` (library, free) and let the user choose. Library voices need `voices add` before they can render; `voices search --preview-dir DIR` downloads their previews for free.
- Always pass an absolute `--out` (or `--out-dir`). Where the audio goes is the user's call: when the request or the project does not make it obvious (a `build/` or `audio/` folder of the current project, a folder the user named), ask with AskUserQuestion before generating, offering the current project folder as the recommended option.
- Report the saved path the CLI prints, verbatim.
- Nothing outside the working directories is writable from a session. If the CLI answers `cannot write to <folder>` (an output folder, or the config directory on `config set`), ask the user to run `/add-dir <folder>` for this session and retry the same command; never change the sandbox settings or move the output elsewhere on your own.
- Fail loudly: on a non-zero exit, show the exact error line and stop. Do not retry with another voice, model or shorter text on your own.
- The key is never printed, echoed or copied; the CLI reads it from the environment or the configured key file.
- Model: use `default_model` (v3 today). Override with `--model` only for a stated reason: `eleven_multilingual_v2` for a pronunciation dictionary, SSML `<break>` compatibility, a seed, a speed or continuity context; `eleven_flash_v2_5` for a cheap draft.
- Pauses are never produced by the model. Exact timing comes from `piece` (one take, cut at its pauses), from `join` (trim, digital silence, verify) or from `clips` plus the player. Details in `references/prompting.md`.
- Languages: pass `--language <iso>` whenever the text is not English or mixes scripts; it also selects the alias table (`voices.fr.narrator` versus `voices.en.narrator`).
- Rate and formats: everything works at the config `sample_rate` (48 kHz) and the CLI requests lossless clips by default; do not pass `--format` unless the user wants an MP3 to listen to. Write `.wav` for anything that feeds `join` or `mix`, deliver `.m4a`. Speech is mono; a piece turns stereo when a bed or music is stereo.
- Loudness: normalise a take, not its lines. A single voice reading one script is normalised once (`piece --lufs`); `join --lufs` normalises per clip, which is for assembling different voices and which raises the room tone of any quiet render until it is audible.
- Beds: for content an app will play, keep speech and beds as separate files and let the app mix them. Print a bed into the file with `mix` only for a podcast, video or one-off piece, and only after `join` and `verify`.
- Before choosing a voice for a role, run `voices shortlist --use-case <use-case>` and read `references/pipeline-findings.md`. No rated voice for the role means a casting round (`/elevenlabs:casting`), not a guess from a library description.
- Truncation: the CLI checks the tail of every render and fails loudly when the API cut it (v3 does this to the last utterance of a request, whatever its length). Short lines render on v2 automatically; v3 lines in `dialogue --mode per-turn` and every line of `piece` get a sacrificial tail. Never pass `--allow-truncated` to make an error go away; report it and use the printed remedy.
- When the user reports a problem with a mechanism (timing, loudness, cuts, a CLI error), point them to `/elevenlabs:improve`; when the problem is a voice, to `/elevenlabs:casting`.

## Command map

| need | command |
|---|---|
| credits, tier | `elevenlabs-cli account` (free) |
| my voices / library / previews | `voices list`, `voices search --language en --search calm --preview-dir dir`, `voices sample <voice> --out-dir dir` |
| one narration | `tts --file text.txt --voice narrator --seed 7 --out name.mp3` |
| a guided piece that must sound like one take | `piece --file script.txt --voice narrator --model eleven_multilingual_v2 --seed 7 --lufs -25 --out piece.m4a` (script = text lines and `[pause 2.5]` lines; one request, cut at the model's pauses, re-timed exactly; `--dry-run` first) |
| exact pauses from separate renders | `tts` per utterance, then `join a.wav silence:3 b.wav --out piece.m4a` (writes `piece.timing.json`, verifies) |
| re-check a file | `verify piece.m4a piece.timing.json` |
| atomic clips for an app | `clips --file lines.txt --voice narrator --out-dir clips/` (manifest.json) |
| podcast, two speakers | `dialogue --file script.txt --speaker "Ann=alias" --speaker "Bob=alias,model=eleven_multilingual_v2,speed=0.9" --out ep.wav` (per turn by default: varied gaps, `[quick]`, `[gap 1.2]`, `[overlap 0.4]` markers in the script; `--dry-run` shows the turn plan) |
| pick voices for a role | `/elevenlabs:casting` (the lab funnel); `voices shortlist --use-case <x>` shows what the lab already knows |
| sound effect | `sfx --text "..." --duration 8 --out fx.wav` |
| music | `music --prompt "..." --seconds 60 --instrumental --out bed.wav` (stereo) |
| white, pink or brown (red) noise | `noise --color brown --seconds 5 --out bed.wav` (local ffmpeg, free; never spend sfx credits on plain noise) |
| a bed under a finished piece (export only) | `mix piece.m4a --bed rain.wav:-18 --duck 6 --out export.m4a`, after `join` and `verify` |
| why does it sound wrong | `measure file.m4a --timing file.timing.json` (loudness, rate, layout, gaps) before changing anything |
| transcript | `stt file.mp3 --language fr [--diarize] [--json]` |
| clean a recording | `isolate in.wav --out clean.mp3` |
| same take, other voice | `sts take.wav --voice alias --out take2.mp3` |
| new voice | `voice design --description "..." --out-dir previews/` then `voice create ...`; `voice clone --name N --sample a.wav` |
| settings | `config get|set <key> <value>`, aliases `config set voices.<lang>.<alias> <id>` |

Flags, defaults and output formats: `references/cli.md` (or `elevenlabs-cli <command> --help`). Delivery, tags, chunking, per-language notes and the one-take versus per-line decision: `references/prompting.md`. Measured facts about the models and the pipeline: `references/pipeline-findings.md`.

## Model table

| model | credits/char | max chars/request | audio tags | seed, speed, continuity |
|---|---|---|---|---|
| `eleven_v3` (default) | 1 | 5000 | yes | no: one paragraph per request, fixed settings |
| `eleven_multilingual_v2` | 1 | 10000 | no, SSML `<break>` up to 3 s | yes, plus request stitching |
| `eleven_flash_v2_5` | 0.5 | 40000 | no | yes |

## Reporting

End with: what was generated, the model and voice used, characters and credits spent, and every saved path. If `join`, `piece` or `verify` reports a gap mismatch, say which gap and by how much; do not ship the file.
