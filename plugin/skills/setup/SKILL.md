---
name: setup
description: Install and configure elevenlabs-cli so the other skills of this plugin work: check Python, uv or pipx and ffmpeg, install the CLI from the copy this plugin ships with, create the config, seed the voice lab with the default audition protocols, and check the API key and the account. Use only when invoked as /elevenlabs:setup, or when a skill reports that the elevenlabs-cli command is missing.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Set up elevenlabs-cli

This plugin is the interface; `elevenlabs-cli` is the engine. This skill puts the engine in place. Report what you find at each step and stop on the first thing the user must decide. Never install anything the user has not agreed to, and never write an API key into a file the user did not name.

## 1. What is already there

```sh
elevenlabs-cli --version; python3 --version; uv --version; pipx --version; ffmpeg -version | head -1
```

- `elevenlabs-cli` already present: skip to step 3, and say which version answered.
- Python 3.10 or newer is required.
- `uv` or `pipx` is required to install the tool cleanly. If neither is present, say so and let the user choose: `uv` is the faster option (`curl -LsSf https://astral.sh/uv/install.sh | sh`), `pipx` the one most distributions package. Do not install a package manager without asking.
- ffmpeg is required for everything except plain `tts` to an MP3: trimming, `piece`, `join`, `verify`, `clips`, `mix`, `measure`, `noise`. Name the distribution package rather than installing it yourself.

## 2. Install the CLI

The plugin ships inside the CLI's own repository, so the source is already on disk next to the plugin and no second download is needed:

```sh
ls "$CLAUDE_PLUGIN_ROOT/../pyproject.toml" && uv tool install "$CLAUDE_PLUGIN_ROOT/.."
```

If that file is not there, the plugin was materialised without the rest of the repository. Say so and install from the published source instead:

```sh
uv tool install git+https://github.com/benjaminpeeters/elevenlabs-cli
```

with `pipx install` in place of `uv tool install` when that is what the user has. Then confirm the command resolves: `elevenlabs-cli --version`. If the shell cannot find it, the tool directory (usually `~/.local/bin`) is not on PATH; tell the user, do not edit their shell profile.

## 3. Configuration and the key

```sh
elevenlabs-cli config init
```

This writes `config.json` into the config directory (`~/.config/elevenlabs-cli` unless `$ELEVENLABS_CLI_CONFIG` overrides it). The API key is never stored there. The CLI reads it from the environment variable named by `api_key_env` (default `ELEVENLABS_API_KEY`), or from a file named by `api_key_file` holding a bare key or `KEY=value` lines. Ask the user which they want and set only the pointer:

```sh
elevenlabs-cli config init --api-key-file ~/.secrets/elevenlabs.env
```

Never print, echo, copy or store the key itself. If the user has no key yet, point them at their ElevenLabs account settings and stop here.

## 4. Seed the voice lab

The config directory doubles as the lab. Copy the default audition protocols in, without overwriting anything the user already has:

```sh
cp -rn "$CLAUDE_PLUGIN_ROOT/assets/protocols/." "<config dir>/protocols/"
```

The defaults cover calm guided content (yoga nidra, meditation, breathing), explanatory narration, an elderly-voice dialogue, a French explainer and a Mandarin greeting. Each use case has `isolation.txt`, `longform.txt` and `contrast.txt`. Say which ones landed, and that `/elevenlabs:casting` writes trials, ratings and findings into the same directory.

If the user keeps the lab in version control, mention that `screen/` (downloaded previews) and `index.json` (derived) are the two things worth ignoring.

## 5. Check it works

```sh
elevenlabs-cli config show
elevenlabs-cli account
```

`account` is free and proves the key. Report the tier, the credits remaining and the reset date, and the spend threshold `confirm_above_chars` from the config, since that is the one number that decides how much a session may spend without asking. Suggest the user set it deliberately:

```sh
elevenlabs-cli config set confirm_above_chars 2000
```

Then say the plugin is ready and name the four skills: audio generation happens automatically when asked; `/elevenlabs:casting` picks voices; `/elevenlabs:check-updates` checks what moved at ElevenLabs; `/elevenlabs:improve` fixes the tooling itself.
