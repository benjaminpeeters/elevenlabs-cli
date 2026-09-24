---
name: check-updates
description: Check what changed at ElevenLabs (models, SDK releases, docs changelog) since the last check, decide which recorded findings and CLI tables are stale, and file a dated report in the voice lab. Use only when invoked as /elevenlabs:check-updates.
disable-model-invocation: true
allowed-tools: Bash(elevenlabs-cli *), Bash(curl *), Bash(jq *), Bash(git *), WebFetch, WebSearch, Read, Write, Edit, AskUserQuestion
---

# Are the ElevenLabs tools still current?

Everything the tooling knows about ElevenLabs is dated and carries the model and SDK it was verified with: `findings.json` in the lab (the CLI's config directory) and `references/pipeline-findings.md` in the generate skill. This skill finds what moved and says what to re-verify. It does not change code; that goes to `/elevenlabs:improve`.

## Sources, in order

1. Live models versus the CLI table: `elevenlabs-cli models --diff`. New models, removed models, changed character limits. Deterministic.
2. SDK releases: the pinned version is in the CLI's `pyproject.toml` (`elevenlabs==...`). Latest on PyPI: `curl -s https://pypi.org/pypi/elevenlabs/json | jq -r .info.version`. Release notes between the two: the GitHub releases of `elevenlabs/elevenlabs-python`. Look for new parameters on text-to-speech and text-to-dialogue (speed or seed on v3, continuity fields, new output formats), new endpoints, deprecations.
3. Docs changelog: WebFetch `https://elevenlabs.io/docs/changelog` and read entries newer than `last_update_check` in the lab's `findings.json`.
4. Findings: for every entry in the lab's `findings.json`, compare `verified_with` with what changed. A finding is stale when its model gained a relevant capability, when the SDK changed the parameters it relies on, or when the changelog mentions its subject (truncation, dialogue timing, formats per tier, voice settings).

## Output

Write `reports/update-check-<date>.md` in the lab with four sections and print it:

- Changed: models, SDK versions, changelog items, one line each with its source.
- Stale findings: finding id, why, and the experiment that re-verifies it (each finding names it in `reverify_with`).
- Tooling to update: the `cost.py` model table, the skill references (`references/prompting.md`, `references/cli.md`, `references/pipeline-findings.md`), the SDK pin (bump, then the test suite in the CLI repo).
- Nothing to do: what was checked and is unchanged.

Then set `last_update_check` in the lab's `findings.json` to today's date. Ask the user which re-verifications to run now; hand code changes to `/elevenlabs:improve`.

## Rules

- Quote sources; never infer a capability from memory. If a page cannot be fetched, say so and leave that source unchecked.
- Spend nothing: this skill only reads. Re-verification experiments are separate, priced, and confirmed with the user.
