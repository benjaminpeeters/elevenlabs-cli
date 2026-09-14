"""Character counting, credit estimates and chunking.

The table is the only place that knows model limits and rates; an unknown
model is an error, never a guess. ``elevenlabs-cli models`` lists what the
account can use and the live limits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import CliError


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    credits_per_char: float
    max_chars: int  # per request, subscribed tiers
    stitching: bool  # supports previous/next request ids for long-text continuity
    audio_tags: bool  # accepts v3 delivery tags such as [whispers]


MODELS: dict[str, ModelInfo] = {
    "eleven_v3": ModelInfo("eleven_v3", 1.0, 5000, stitching=False, audio_tags=True),
    "eleven_v3_conversational": ModelInfo("eleven_v3_conversational", 1.0, 5000, stitching=False, audio_tags=True),
    "eleven_multilingual_v2": ModelInfo("eleven_multilingual_v2", 1.0, 10000, stitching=True, audio_tags=False),
    "eleven_flash_v2_5": ModelInfo("eleven_flash_v2_5", 0.5, 40000, stitching=True, audio_tags=False),
    "eleven_turbo_v2_5": ModelInfo("eleven_turbo_v2_5", 0.5, 40000, stitching=True, audio_tags=False),
    "eleven_flash_v2": ModelInfo("eleven_flash_v2", 0.5, 30000, stitching=True, audio_tags=False),
    "eleven_turbo_v2": ModelInfo("eleven_turbo_v2", 0.5, 30000, stitching=True, audio_tags=False),
}

PAUSE_TAG = re.compile(r"\[pause[^\]]*\]|<break\s+time=\"[^\"]+\"\s*/>", re.IGNORECASE)
AUDIO_TAG = re.compile(r"\[[a-zA-Z][^\]]*\]")
SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def model_info(model_id: str) -> ModelInfo:
    if model_id not in MODELS:
        raise CliError(f"unknown model '{model_id}'. Known: {', '.join(MODELS)}. Run 'elevenlabs-cli models' for the live list.")
    return MODELS[model_id]


def billable_chars(text: str) -> int:
    """Characters ElevenLabs bills for: pause and break tags are excluded."""
    return len(PAUSE_TAG.sub("", text))


def estimate_credits(text: str, model_id: str) -> float:
    return billable_chars(text) * model_info(model_id).credits_per_char


def chunk_text(text: str, model_id: str) -> list[str]:
    """Split ``text`` into requests under the model limit.

    v3 (no stitching) splits on blank lines so every request is a paragraph
    that stands on its own; other models split on sentence boundaries and rely
    on request stitching for continuity. A single sentence or paragraph over
    the limit is an error: the text must be rewritten, not cut mid-sentence.
    """
    info = model_info(model_id)
    text = text.strip()
    if not text:
        raise CliError("text is empty")
    if len(text) <= info.max_chars:
        return [text]
    units = [u.strip() for u in re.split(r"\n\s*\n", text)] if not info.stitching else SENTENCE_END.split(text)
    units = [u for u in units if u]
    chunks: list[str] = []
    current = ""
    separator = "\n\n" if not info.stitching else " "
    for unit in units:
        if len(unit) > info.max_chars:
            kind = "paragraph" if not info.stitching else "sentence"
            raise CliError(f"a single {kind} has {len(unit)} characters, above the {info.max_chars} limit of {model_id}; split it in the source")
        candidate = unit if not current else current + separator + unit
        if len(candidate) > info.max_chars:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def strip_audio_tags(text: str) -> str:
    """Remove ``[tag]`` markers (used to count spoken characters for pacing)."""
    return AUDIO_TAG.sub("", text)
