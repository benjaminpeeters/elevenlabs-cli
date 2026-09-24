# Prompting and assembly notes

## The voice matters most

Delivery quality is decided first by the voice, then by stability, then by the text. Before tuning tags or settings, search the library (`voices search --language xx --search "<theme>" --preview-dir dir`, free) and let the user listen. A calm narration voice with a `narrative_story` or `informative_educational` use case reads guided content far better than a conversational one pushed with tags.

## Pauses: by construction, never by the model

A TTS clip carries its own leading and trailing silence, and it differs at every request. So:

- Do not rely on `[pause]` tags (v3, vague) or SSML `<break time="1.5s"/>` (v2 only, at most 3 s, not exact either) for timing that matters. Use them at most for a beat inside a sentence.
- For a pause of N seconds between two utterances: render each utterance as its own `tts` request, then `join a.mp3 silence:N b.mp3 --out piece.m4a`. `join` trims every clip to its voiced content (threshold `trim_threshold_db`, keeping up to `trim_margin_ms` of quiet audio so soft onsets survive), inserts digital silence shortened by those margins, and measures every gap of the result against the spec. A mismatch beyond `verify_tolerance_ms` is an error: report it, do not ship the file.
- A spec file keeps long pieces readable: lines `file 01-settle.mp3`, `silence 4`, `file 02-breath.mp3`, ... and `join --spec piece.txt --out piece.m4a`. The `<out>.timing.json` sidecar records every clip's start/end and every gap; `verify piece.m4a piece.timing.json` re-checks later.

### Baked versus app-timed

- **One take, re-timed** (the default for a guided piece): the whole script is a single request, so the voice keeps one tone and one room tone from start to finish, and the take is then cut at the pauses the model left and reassembled with the exact silences. Use `piece` with a timed script (text lines and `[pause 2.5]` lines). Rendering each utterance as its own request also gives exact timing, but each request comes back with its own tone and its own room tone, and the result is heard as a series of splices.
- **Baked from separate renders**: the silences live inside one file assembled from clips that were rendered apart (a podcast with 3-second transitions, several voices on one timeline). Use `join`; deliver M4A (`.m4a`, AAC 192k) for apps and phones, WAV or FLAC for further editing.
- **App-timed**: the app inserts the silence at play time because the user sets it (a breathing exercise with a chosen exhale length, a review prompt with a chosen answer time). Use `clips --file lines.txt --out-dir dir`: one trimmed clip per line, `manifest.json` with id, text, duration, kept margins and sha256. The player concatenates clip, exact silence, clip.

## Truncation, and what the CLI does about it

v3 decides itself when speech ends and drops the tail of the last utterance of a request, whatever its length (an 89-character line, three renders out of three; the last line of a dialogue request; short greetings most of all). Every render is checked: the last 30 ms must decay below -30 dB and below the clip's own peak by 20 dB, or the command fails and names the file. Remedies the CLI applies by itself: lines under 60 characters render on v2 (`short_line_model`); `dialogue` renders v3 lines with a sacrificial closing sentence and cuts at the silence between (per-turn), or appends it to the dialogue request (segments). For plain `tts` on v3 the guard is the only protection: a failed line is re-rendered by the user's choice, never silently. `--allow-truncated` exists for inspection, not for shipping.

## Dialogue: writing for turn-taking

- One line per turn, `Name: text`. Markers at the start of a line: `[quick]` (the reply comes right in), `[gap 1.2]` (a thoughtful pause), `[overlap 0.4]` (an interruption, starts 0.4 s before the previous line ends). Without a marker the CLI draws a gap by rule (question, short reply, long statement, same speaker) from a seed, so no two turns are alike and the same script re-renders identically.
- Short turns, reactions and interjections ("Mm.", "Right.", "Go on.") are what make an exchange sound alive; they render on v2 automatically and get quick gaps.
- Per speaker, choose model and settings in `--speaker`: a calm elderly voice often sits better on v2 with `speed=0.9`; an energetic one on v3 with tags. Compare with `--mode segments` (the endpoint hears the whole exchange, voices react to each other) and let the user judge; record the verdict in the lab.
- Always `--dry-run` first: it prints who speaks, the planned gap and its reason, the model per line and the credits.

## One utterance per request, fixed seed

v3 has no request stitching, so consistency across a long piece comes from: the same voice, the same settings (`--stability`, `--similarity`, `--speed`), the same `--seed`, and one utterance (or one paragraph) per request. Render, listen to two or three clips in a row, and if the tone drifts, re-render the outlier with the same seed rather than changing settings mid-piece. v2 and Flash stitch automatically when `tts` splits a long text (previous request ids and neighbouring text are passed), which is the better choice for a single long narration that must flow as one take.

## Chunk limits

| model | max chars per request | split rule in `tts` |
|---|---|---|
| eleven_v3 | 5000 | on blank lines (paragraphs) |
| eleven_multilingual_v2 | 10000 | on sentence ends, stitched |
| eleven_flash_v2_5 / turbo_v2_5 | 40000 | on sentence ends, stitched |

A paragraph or sentence above the limit is an error, not a mid-sentence cut: rewrite the source. `[pause]` and `<break>` tags are not billed; other tags are.

## Stability

- v3 accepts three values: `--stability 0` (creative: most expressive, follows tags, least consistent), `0.5` (natural, the default choice for narration), `1` (robust: flattest, most consistent, ignores most tags). For guided audio start at 0.5; go to 1 only if takes vary too much.
- v2: `--stability` 0..1 continuous, `--similarity` 0..1, `--style` 0..1 (exaggeration; keep 0 for narration), `--speed` 0.7..1.2 (0.9 reads calmer without sounding slowed).

## v3 audio tags (delivery only)

Tags steer how a line is spoken, in square brackets at the start of the sentence they affect: `[whispers]`, `[softly]`, `[calmly]`, `[slowly]`, `[sighs]`, `[laughs]`, `[excited]`, `[sad]`. Keep one tag per sentence, put it where the change starts, and prefer plain punctuation (a full stop, an ellipsis, a line break) for rhythm. Tags cost characters. They are ignored by v2 and Flash, where the text must carry the delivery on its own.

## Per-language notes

- Pass `--language <iso>` for anything that is not plain English. It selects the alias table of the config and tells the model how to read numbers, dates and names.
- **fr**: pick a voice labelled `language=fr` or with a French accent tag; English-native voices read French with an accent even with `--language fr`. Write numbers as words when they must be read a specific way ("vingt et un").
- **en**: accent matters for guided content; the library labels american, british, australian, and so on. `--search "meditation"` or `"sleep"` finds narration voices trained for calm delivery.
- **zh**: always pass `--language zh`, otherwise numbers and mixed Latin text may be read in English. Use a voice labelled `language=zh`. Keep one sentence per line; separate clauses with full-width punctuation. Pinyin in the text is read as English letters; write characters.
- Mixed-language lines (a French sentence quoting an English title): render each language segment as its own utterance and `join` them; a single request reads everything in one language.

## Loudness: normalise the take, not the lines

A single voice reading one script is normalised once, over the whole take (`piece --lufs -25`). Normalising clip by clip (`join --lufs`) is for a timeline that carries different voices, which genuinely differ by several LU. Applied to one voice it does real harm: a short quiet render gains whatever it takes to reach the target, about 19 dB in one measured case, and its room tone rises with it until the hiss sits 15 dB under the speech and is plainly audible. Calm content is more exposed than most, because it is quiet and heard on headphones.

## Volume

Leave `default_lufs` at null while comparing voices (loudness normalisation hides differences). For a finished piece set `--lufs -18` on `join` (calm content sits lower than the -16 typical of podcasts); it normalises each clip on its own, which evens out voices of different loudness, and never alters timing. `measure` reports integrated loudness, true peak and range per file; compare clips with it before touching settings.

## Rate, formats, beds

- Everything works at 48 kHz (config `sample_rate`); the CLI requests lossless clips from the API and converts foreign inputs once in `join` and `mix`. Do not pass `--format` except for an MP3 the user only wants to hear.
- `.wav` for everything that feeds `join` or `mix`, `.m4a` for delivery (AAC: gapless on iOS and Android), `.flac` for a master worth keeping.
- Speech is mono. Music and noise beds are stereo; a mixed piece is stereo with the voice centred.
- App content: the piece stays speech-only with its baked pauses; beds are separate seamless loops the app mixes. Exports (podcast, video, a piece for friends): `mix` after `join` and `verify`, bed at -18 to -24 dB under the speech, `--duck 6` for spoken content over music, fades of a few seconds.

## Review loop for guided content

1. Render one section (a handful of utterances) with the candidate voice and settings.
2. `join` it with the intended silences and listen once at real speed, with eyes closed.
3. Adjust the text (shorter sentences, an ellipsis where a breath goes) before adjusting settings; adjust settings before changing the voice.
4. Only then render the whole piece, with the same seed.
