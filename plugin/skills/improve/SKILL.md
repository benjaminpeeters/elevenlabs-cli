---
name: improve
description: Maintenance of the ElevenLabs tooling. Use only when invoked as /elevenlabs:improve, after a session produced audio that was not right (robotic or inconsistent voice, wrong loudness, pauses off, wrong mood or speed, artificial dialogue, audible cuts between lines, a CLI error) and the fix should serve every future session, not just this one.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
---

# Improve the ElevenLabs tooling

A generating session solves today's problem as fast as it can. This skill exists to move what was learned into the layer where it helps every later session. Do not generate audio here; diagnose, fix the right layer, test, record.

## The map

The plugin and the CLI live in one repository, so a change can span both in a single commit. Paths below are relative to that repository root.

| layer | where | what belongs there |
|---|---|---|
| generating skill | `plugin/skills/generate/SKILL.md` | rules the model must follow on every audio task (short; every line costs context on every run) |
| CLI reference | `plugin/skills/generate/references/cli.md` | one line per subcommand and flag; must match `elevenlabs-cli <cmd> --help` |
| prompting notes | `plugin/skills/generate/references/prompting.md` | how to write text, tags, settings, pacing, per-language notes, the review loop |
| pipeline findings | `plugin/skills/generate/references/pipeline-findings.md` | dated, measured facts about the models, ffmpeg and the pipeline that hold for any account. Nothing account-specific: no voice ids, no verdicts |
| default protocols | `plugin/assets/protocols/` | audition scripts a new user starts from; `/elevenlabs:setup` copies them into the lab |
| CLI | `src/elevenlabs_cli/`, tests in `tests/` | behaviour: trim, piece, join, verify, mix, measure, spend guard. Run the test suite from the repo's `.venv` |
| config and lab | the CLI's config directory (`~/.config/elevenlabs-cli`) | `config.json` (this machine's values), `protocols/`, `samples/` (trials with verdicts), `screenings/`, `findings.json`, `experiments/`, `reports/`. Per-account measured knowledge lives here, never in the plugin |
| triggering | the `description` of each skill | what decides whether a skill loads. There is deliberately no router hook: one was tried and removed after it misrouted on bare keyword matches. A skill that failed to load is a description problem |

## Procedure

1. Collect the evidence. Ask for (or read from the session the user points at) the exact symptom, the audio file, the command lines that produced it, the voice id, model and settings. Measure before guessing: `elevenlabs-cli measure <file> [--timing <sidecar>]` gives duration, loudness and the gap table; `ffprobe` gives rate and channels; characters per second come from the text length and the trimmed duration.
2. Classify the symptom with the table below and decide the layer. One symptom, one layer, one change. If the fix is a CLI change, write the failing test first.
3. Apply the change in that layer. Keep `SKILL.md` short: durable rules only; details go to the references. A finding goes to `pipeline-findings.md` as a dated entry with numbers, never as an opinion, and to the lab's `findings.json` with its `verified_with` and `reverify_with`.
4. Verify. CLI: the test suite green, then the real command on the offending file. Skill text: re-read the whole file for contradictions; the reference must still match `--help`.
5. Record and offer the commit. Summarise what changed and why in one paragraph, list the files, and ask the user whether to commit.

## Symptom to layer

| symptom | measure | usual cause | layer and fix |
|---|---|---|---|
| voice sounds robotic or flat | listen; compare with the library preview | stability too high, or a voice trained for another use case | lab: voice card with the setting that worked; prompting: stability guidance. Try stability 0.5 then 0 on v3 before changing voice |
| 80% good, 20% odd (wrong emphasis, glitch, mispronounced word) | which utterances, same seed? | per-request variance; long request; a word the model misreads | prompting: one utterance per request with a fixed seed, re-render the outlier only; spell the word phonetically or add a pronunciation dictionary (v2) |
| the piece sounds like spliced fragments | listen at the cuts; `measure` each clip's loudness | every line rendered as its own request, each with its own tone and room tone | render the script as one take with `piece`; normalise the take, not the lines |
| audible hiss, especially in short lines | room-tone floor of each clip against the speech level | per-clip loudness normalisation raised a quiet render's floor | `piece --lufs` (one gain for the take) instead of `join --lufs` (per clip); cast cleaner voices; denoise only as a last resort |
| some voices much quieter than others | `measure` per clip: integrated LUFS | voices differ by several LU | that is what `join --lufs` is for: different voices on one timeline |
| pauses not respected | `verify` / `measure --timing` | bed above the silence threshold, clips at mixed rates, verification on a mixed file | prompting: join and verify on speech only, mix last; CLI bug if the sidecar shows the wrong plan |
| not expressive enough | listen | v2 or Flash model, stability too high, no tags | prompting: v3 with tags and stability 0.5 or 0; findings: which tags the voice honours |
| mood does not match the instruction | listen | tag placed wrong or ignored, text carries no cue | prompting: cue in the text itself (punctuation, sentence length), tag at the start of the sentence; consider another voice |
| too fast or slow for the content | characters per second on the trimmed clip | the voice's natural pace | cast for pace: `--speed` moves it only slightly. Findings: cps per voice and context |
| dialogue feels artificial | listen; turn lengths | monologue-length turns, no interjections, both voices same energy | prompting: shorter turns, reactions, overlapping topics; different stability per role |
| a CLI error or wrong output | the exact stderr line | a bug | CLI: failing test, fix, run the suite |
| the skill did not load or did the wrong thing | the transcript | the description | skill description. Never a hook |

## Findings format

```
## 2026-09-16  one-take renders (eleven_multilingual_v2, SDK 2.68.0)
- 17 lines in one request, cut at the pauses: 17 of 17 gaps verified, no truncation
- the same voice read the take at 0.85 of its per-line pace
- consequence: render guided pieces with `piece`; set speed from the measured ratio
```

Every entry has the model, the SDK, a number and a consequence. Remove entries a later entry supersedes.
