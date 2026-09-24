# The `elevenlabs` plugin for Claude Code

Five skills that drive `elevenlabs-cli`, the command-line tool in this repository. Install both at once:

```
/plugin marketplace add benjaminpeeters/elevenlabs-cli
/plugin install elevenlabs@elevenlabs-cli
/elevenlabs:setup
```

The marketplace is this repository, so installing the plugin also brings the CLI source; `/elevenlabs:setup` installs it from that copy, creates the configuration, and seeds the voice lab with the default audition protocols. You need Python 3.10 or newer, `uv` or `pipx`, ffmpeg, and an ElevenLabs API key.

## The skills

| skill | invoked | what it does |
|---|---|---|
| `generate` | automatically, when you ask for audio | speech, dialogue, guided pieces with exact pauses, sound effects, noise beds, transcription. Estimates before spending and asks above the configured threshold |
| `setup` | `/elevenlabs:setup` | installs and configures the CLI, seeds the lab |
| `casting` | `/elevenlabs:casting` | picks voices for a role by measurement: a free preview screen, then auditions on fixed protocols, your ratings recorded against every trial |
| `check-updates` | `/elevenlabs:check-updates` | what changed at ElevenLabs since the last check, and which recorded findings went stale |
| `improve` | `/elevenlabs:improve` | fixes the tooling itself when a session produced audio that was not right, in whichever layer owns the problem |

## What the plugin knows, and what your machine knows

The plugin ships facts that hold for everyone: how the models behave, what ffmpeg does, how to write a script the model can pause in, and the default audition protocols. Those live in `skills/generate/references/` and `assets/protocols/`.

Everything measured about your own account stays on your machine, in the CLI's config directory: your voice trials and the ratings you gave them, your aliases, your findings, your experiments. That directory is the voice lab, and it is worth putting in a private git repository of its own. Nothing from it is ever sent back here.

## Why a plugin and not loose skills

The five skills make claims about how the CLI behaves, so they have to move with it. Keeping them in the CLI's own repository means one version covers the command and everything the model is told about it, and a change to both is one commit.
