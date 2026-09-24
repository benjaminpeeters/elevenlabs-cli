# Measured facts about the models and the pipeline

Everything here was measured, not inferred, with the model and SDK named against each entry. These are facts about the API and about ffmpeg, so they hold for any account. Facts about particular voices (who sounds right for a role, at which settings) are not here: they are per-account measurements and live in the voice lab, the CLI's config directory, where `voices audition` writes trials and `voices shortlist` ranks them. `/elevenlabs:check-updates` says which of these has gone stale.

Verified 2026-09-14 to 2026-09-16, SDK 2.68.0, Creator tier.

## Models

- 48 kHz lossless everywhere. `wav_48000` for speech and dialogue, `pcm_48000` for effects and music (stereo); 44.1 kHz WAV and raw PCM are Pro-only. The server resamples, so asking for 48 kHz costs nothing.
- v3 truncates the last utterance of a request, whatever its length: an 89-character line was cut in 3 renders of 3. v2 does not. The guard runs on every render.
- Every single-line request is therefore a last utterance. Rendering line by line on v3 failed within the first three lines in 6 runs of 6, across two voices and two granularities. Per-line rendering is v2 work unless the command adds a sacrificial tail, which `dialogue --mode per-turn` and `piece` do.
- v3 has no seed, no speed and no continuity context (`previous_text` is rejected with HTTP 400). v2 has all three plus request stitching. Working split: v3 for expressive content and dialogue reactions, v2 for calm narration, short lines, and any piece that must stay consistent.
- `eleven_v3_conversational` is listed by the models endpoint but the dialogue endpoint refuses it.
- The single-request dialogue endpoint places about 0.5 s between every turn and offers no timing control. Its voice segments touch exactly, so the acoustic gap sits inside the next segment's span: a line boundary is the first silence after a segment ends, never the next segment's nominal start.
- Speech-to-speech has a high minimum charge (275 credits for 1.3 s of input). Audio isolation refuses inputs under 4.6 s.

## Reproducibility

- A seeded v2 render is the same performance, not the same bytes. Two renders of the same 17 lines with the same voice, stability and seed gave trimmed durations within 2 ms and sample correlation above 0.99 on 32 of 34 clips, but no clip was byte-identical (peak differences about 1 percent of full scale). Cache renders by input hash and reuse them; never expect a re-render to match bit for bit.

## Pace

- Pace is a property of the voice and the text. `--speed 0.85` on a one-take v2 render lengthened the spoken time by 4 percent, not the 15 percent the setting nominally asks. Use it as a fine adjustment; choose a slower voice if you need a slower piece.
- The same voice and settings read a whole take faster than the same text line by line: 0.85 of the reference pace in one request against 1.04 line by line. Measure the take you actually render.
- Per-utterance timing of a human guide is not reproducible. Against a transcribed reference, the ratio of rendered to original duration ranged 0.4 to 1.9 per line: two-word fragments render far faster than a person says them, long enumerations slower. Write scripts that pause at clause and sentence boundaries a model can hold.

## One take versus per line

- A timed script rendered as one request (`piece`) keeps one tone and one room tone throughout, and the character alignment plus a silence search cuts every line cleanly: 17 of 17 gaps verified on both v2 (with `<break>` tags at the pauses) and v3 (paragraph per line plus a sacrificial tail). This is how a guided piece should be rendered. Rendering each utterance separately gives exact timing but a new tone and a new room tone at every cut, which is audible as a series of splices.

## Room tone and denoising

- Renders carry a room tone: about -62 to -75 dBFS in a one-take render, -55 to -72 dBFS per line, and it differs from request to request.
- Per-clip loudness normalisation is what makes it audible. A short quiet render normalised on its own gained about 19 dB, and its room tone rose from -59 to -40 dBFS, 15 dB under the voice. Normalise the take, not the lines.
- On a clean voice no denoiser lowers the floor by more than a few dB without changing the voice: gentle filters (an expander, non-local means) change nothing measurable there. On a floor 15 to 20 dB under the voice, DeepFilterNet 3 lowers it 7 to 14 dB at 4 to 8 dB of spectral change, an attenuation limit trading some of both; RNNoise at partial mix gives about a third of the reduction for half the change; spectral gating (noisereduce) lowers the floor by pulling the speech down with it, 4 to 7 LU, and is not usable.
- ElevenLabs audio isolation raised the floor (-70 to -61 dBFS) and returned a re-synthesised, resampled signal. Do not use it to clean a TTS render.
- The cut feel between lines comes from the contrast between a render's tone and the digital silence of the gaps, not from the level of the tone. Filling the gaps with the take's own room tone makes them continuous.

## ffmpeg

- The `loudnorm` filter outputs 192 kHz unless the rate is forced after it. An unforced bed once stretched a 333 s piece to 366 s. Every ffmpeg output in this pipeline forces `-ar`.
- Brown noise from `anoisesrc` is an integrated random walk: its first and last samples sit far from zero, so an unfaded bed thumps at both edges (a sample step of 0.13 measured, against under 0.05 for speech). The `noise` command applies a 40 Hz high-pass and 0.5 s fades by default.
