---
name: casting
description: Choose voices for a use case with the ElevenLabs voice lab. Use only when invoked as /elevenlabs:casting, to run the casting funnel (free preview screen, isolation, settings grid, long-form and contrast dialogue), record the user's ratings and keep every trial for later comparison.
disable-model-invocation: true
allowed-tools: Bash(elevenlabs-cli *), Bash(ffprobe *), Bash(ls *), Read, Write, AskUserQuestion
---

# Casting with the voice lab

Casting is a funnel: cheap and wide first, expensive and narrow last, a rating recorded after every stage. Nothing is thrown away; a trial rated 2/5 today is still evidence next year, when the model has changed. The lab is the CLI's own config directory (`~/.config/elevenlabs-cli` unless `$ELEVENLABS_CLI_CONFIG` says otherwise): it holds `config.json`, `protocols/`, `samples/`, `findings.json` and `screenings/`.

## Before starting

1. `elevenlabs-cli config show`: threshold, `reference_partner` (the fixed partner voice for contrast dialogues), sample rate.
2. `elevenlabs-cli voices shortlist --use-case <use-case>`: what the lab already knows. Do not re-audition a voice on a protocol it was rated on unless the model changed (`voices index` shows dates and models).
3. `elevenlabs-cli account`: credits and voice slots (30 slots on Creator; a limited number of voice additions or removals per month). Each stage below states its cost; ask before every stage.
4. Protocols for the use case must exist in `protocols/<use-case>/{isolation,longform,contrast}.txt` of the lab. The plugin ships defaults for several use cases, which `/elevenlabs:setup` copies in. If the one you need is missing, write it with the user: the contrast script has `Candidate:` and `Partner:` lines, and every line must be long enough not to trip the short-line rule, at least 60 characters.

## The stages

1. Screen, free. Library previews for a category: `voices search --language en --age old --gender female --page-size 50 --preview-dir <lab>/screen/<category>`, plus free-text searches (`--search grandmother`, `--search elderly`). Give the user the folder and a table (name, accent, use case, style, uses). They listen and name 6 to 8 candidates. Record what they said: the user marks the previews by renaming them (a rank digit, or a letter for another role), and those verdicts are copied into `screenings/<use-case>-<gender>.tsv` with the voice and owner ids, so the free stage survives the previews being deleted. Then add the chosen voices with `voices add <id> --owner <public_owner_id> --name "<name>"` and count the slots.
2. Isolation. `voices audition --use-case <uc> --protocol isolation --voices <ids> --grid single` (v3 at stability 0.35 and v2 at 0.5, two trials per voice, about 600 characters each). Print the saved paths. The user listens and rates each trial: `voices rate <trial-id> <1..5> --note "..."`. Keep the best 3.
3. Grid. `voices audition ... --protocol isolation --voices <3> --grid default` (v3 stability 0.0 with a `[warmly]` tag, 0.35, 0.6; v2 stability 0.35 and 0.5 at speed 1.0 and 0.9). Rate. Keep 2, and note the settings that worked in the rating note.
4. Context. `voices audition ... --protocol longform --voices <2>` with the winning settings, and `voices audition ... --protocol contrast --voices <2>`: a per-turn dialogue against the reference partner. Rate. The winner gets an alias: `config set voices.<lang>.<role> <voice id>`, named by the user after the role and project, not after a physical description.

After each stage: `voices shortlist --use-case <uc>` shows the standing. Remove voices that were only added for the audition and lost (`voices delete <id>`), unless the user wants to keep them; say how many edits and slots remain.

## What to measure besides the verdict

Every trial file already records loudness, true peak, range, characters per second and tail level. Two of those are casting criteria in their own right, so read them out to the user rather than leaving them in the file: characters per second says whether the voice can hold the pace the content needs, since `--speed` moves it only slightly; and the room-tone floor says how clean the voice is, which decides whether its renders will ever need denoising. A clean voice beats a denoised one.

## Costs, for the ask before each stage

- Screen: 0. Isolation: characters of the protocol times 2 trials times voices (about 1,200 per voice). Grid: times 7 trials (about 4,000 per voice). Long-form: about 1,800 per trial; contrast: about 900 per trial plus the partner's lines.
- A round on 8 candidates ends near 25,000 credits. The user's monthly budget is in `account`.

## Recording

Every trial file already holds voice card, protocol id, model, SDK version, settings, seed, date and measurements. Your job is only to add the user's verdicts and notes through `voices rate`, and to tell `/elevenlabs:improve` when a mechanism (not a voice) turned out wrong.

## What not to do

- Never rate for the user, never guess a verdict from measurements.
- Never audition a voice that is not in the account by rendering through another voice.
- Never skip the screen to save time: the free stage is where most of the elimination happens.
