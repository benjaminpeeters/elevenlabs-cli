"""The only module that imports the ElevenLabs SDK.

Every function takes plain arguments and returns plain data or bytes so the
commands (and the tests, which replace this module's functions) never touch
SDK objects. API errors are re-raised as ``CliError`` with the status and the
server's message.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError
from elevenlabs.types import DialogueInput, ModelSettingsResponseModel, VoiceSettings

from .errors import CliError

SAMPLE_TEXT = (
    "Take a moment to settle in. Let your shoulders drop, and let the breath find its own rhythm. "
    "There is nothing to do right now, nothing to fix. Notice the weight of your body, the air on your skin, "
    "and the small sounds around you. When you are ready, we will begin."
)


def make_client(api_key: str) -> ElevenLabs:
    if not api_key:
        raise CliError("empty API key")
    return ElevenLabs(api_key=api_key)


def describe_api_error(exc: ApiError, what: str) -> CliError:
    body: Any = exc.body
    detail = body
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, dict) and "message" in detail:
            detail = f"{detail.get('status', '')}: {detail['message']}".strip(": ")
    return CliError(f"ElevenLabs API error while {what} (HTTP {exc.status_code}): {detail}")


def network_error(exc: Exception, what: str) -> CliError:
    return CliError(f"network error while {what}: {exc} (is api.elevenlabs.io reachable?)")


def collect(chunks: Any) -> bytes:
    return b"".join(chunks)


# --- account -----------------------------------------------------------------


def subscription(client: ElevenLabs) -> dict[str, Any]:
    try:
        sub = client.user.subscription.get()
    except ApiError as exc:
        raise describe_api_error(exc, "reading the subscription") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "reading the subscription") from exc
    return sub.model_dump()


def models(client: ElevenLabs) -> list[dict[str, Any]]:
    try:
        items = client.models.list()
    except ApiError as exc:
        raise describe_api_error(exc, "listing models") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "listing models") from exc
    return [m.model_dump() for m in items]


# --- voices --------------------------------------------------------------------


def my_voices(client: ElevenLabs, search: str | None = None) -> list[dict[str, Any]]:
    """Every voice in the account (paginated)."""
    voices: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        try:
            page = client.voices.search(page_size=100, search=search, next_page_token=token)
        except ApiError as exc:
            raise describe_api_error(exc, "listing voices") from exc
        voices.extend(v.model_dump() for v in page.voices)
        if not page.has_more or not page.next_page_token:
            return voices
        token = page.next_page_token


def get_voice(client: ElevenLabs, voice_id: str) -> dict[str, Any]:
    try:
        return client.voices.get(voice_id).model_dump()
    except ApiError as exc:
        raise describe_api_error(exc, f"reading voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"reading voice {voice_id}") from exc


def library_voices(client: ElevenLabs, **filters: Any) -> list[dict[str, Any]]:
    """Voices from the shared library; filter names follow the API (language, gender, age, use_cases, search, ...)."""
    try:
        page = client.voices.get_shared(**filters)
    except ApiError as exc:
        raise describe_api_error(exc, "searching the voice library") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "searching the voice library") from exc
    return [v.model_dump() for v in page.voices]


def add_library_voice(client: ElevenLabs, public_user_id: str, voice_id: str, new_name: str) -> str:
    try:
        response = client.voices.share(public_user_id, voice_id, new_name=new_name)
    except ApiError as exc:
        raise describe_api_error(exc, f"adding library voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"adding library voice {voice_id}") from exc
    return response.voice_id


def delete_voice(client: ElevenLabs, voice_id: str) -> None:
    try:
        client.voices.delete(voice_id)
    except ApiError as exc:
        raise describe_api_error(exc, f"deleting voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"deleting voice {voice_id}") from exc


# --- generation ------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    stability: float | None = None
    similarity: float | None = None
    style: float | None = None
    speed: float | None = None
    speaker_boost: bool | None = None

    def to_sdk(self) -> VoiceSettings | None:
        if all(v is None for v in (self.stability, self.similarity, self.style, self.speed, self.speaker_boost)):
            return None
        return VoiceSettings(
            stability=self.stability,
            similarity_boost=self.similarity,
            style=self.style,
            speed=self.speed,
            use_speaker_boost=self.speaker_boost,
        )


@dataclass(frozen=True)
class TtsResult:
    audio: bytes
    request_id: str | None


def text_to_speech(
    client: ElevenLabs,
    voice_id: str,
    text: str,
    model_id: str,
    output_format: str,
    language: str | None,
    settings: Settings,
    seed: int | None,
    previous_request_ids: list[str] | None,
    previous_text: str | None,
    next_text: str | None,
) -> TtsResult:
    kwargs: dict[str, Any] = {
        "voice_id": voice_id,
        "text": text,
        "model_id": model_id,
        "output_format": output_format,
    }
    if language is not None:
        kwargs["language_code"] = language
    sdk_settings = settings.to_sdk()
    if sdk_settings is not None:
        kwargs["voice_settings"] = sdk_settings
    if seed is not None:
        kwargs["seed"] = seed
    if previous_request_ids:
        kwargs["previous_request_ids"] = previous_request_ids
    if previous_text is not None:
        kwargs["previous_text"] = previous_text
    if next_text is not None:
        kwargs["next_text"] = next_text
    try:
        with client.text_to_speech.with_raw_response.convert(**kwargs) as response:
            audio = collect(response.data)
            request_id = response.headers.get("request-id")
    except ApiError as exc:
        raise describe_api_error(exc, f"generating speech with voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"generating speech with voice {voice_id}") from exc
    return TtsResult(audio, request_id)


@dataclass(frozen=True)
class Alignment:
    """Character-level timing returned with a render."""

    characters: list[str]
    starts: list[float]
    ends: list[float]


@dataclass(frozen=True)
class TimedResult:
    audio: bytes
    alignment: Alignment | None
    segments: list[tuple[int, float, float]]  # (line index, start, end) for dialogue renders


def alignment_from(model: Any) -> Alignment | None:
    if model is None:
        return None
    return Alignment(list(model.characters), list(model.character_start_times_seconds), list(model.character_end_times_seconds))


def text_to_speech_timed(
    client: ElevenLabs,
    voice_id: str,
    text: str,
    model_id: str,
    output_format: str,
    language: str | None,
    settings: Settings,
    seed: int | None,
    previous_text: str | None,
    next_text: str | None,
) -> TimedResult:
    """Like ``text_to_speech`` but through the timestamps endpoint: audio plus character alignment."""
    kwargs: dict[str, Any] = {"voice_id": voice_id, "text": text, "model_id": model_id, "output_format": output_format}
    if language is not None:
        kwargs["language_code"] = language
    sdk_settings = settings.to_sdk()
    if sdk_settings is not None:
        kwargs["voice_settings"] = sdk_settings
    if seed is not None:
        kwargs["seed"] = seed
    if previous_text is not None:
        kwargs["previous_text"] = previous_text
    if next_text is not None:
        kwargs["next_text"] = next_text
    try:
        response = client.text_to_speech.convert_with_timestamps(**kwargs)
    except ApiError as exc:
        raise describe_api_error(exc, f"generating timed speech with voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"generating timed speech with voice {voice_id}") from exc
    return TimedResult(base64.b64decode(response.audio_base_64), alignment_from(response.alignment), [])


def text_to_dialogue_timed(
    client: ElevenLabs,
    lines: list[tuple[str, str]],
    model_id: str,
    output_format: str,
    language: str | None,
    stability: float | None,
    seed: int | None,
) -> TimedResult:
    """The dialogue endpoint with timestamps: audio, alignment and one (line, start, end) per voice segment."""
    kwargs: dict[str, Any] = {
        "inputs": [DialogueInput(text=text, voice_id=voice_id) for voice_id, text in lines],
        "model_id": model_id,
        "output_format": output_format,
    }
    if language is not None:
        kwargs["language_code"] = language
    if stability is not None:
        kwargs["settings"] = ModelSettingsResponseModel(stability=stability)
    if seed is not None:
        kwargs["seed"] = seed
    try:
        response = client.text_to_dialogue.convert_with_timestamps(**kwargs)
    except ApiError as exc:
        raise describe_api_error(exc, "generating the timed dialogue") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "generating the timed dialogue") from exc
    segments = [(s.dialogue_input_index, s.start_time_seconds, s.end_time_seconds) for s in response.voice_segments]
    return TimedResult(base64.b64decode(response.audio_base_64), alignment_from(response.alignment), segments)


def text_to_dialogue(
    client: ElevenLabs,
    lines: list[tuple[str, str]],
    model_id: str,
    output_format: str,
    language: str | None,
    stability: float | None,
    seed: int | None,
) -> bytes:
    kwargs: dict[str, Any] = {
        "inputs": [DialogueInput(text=text, voice_id=voice_id) for voice_id, text in lines],
        "model_id": model_id,
        "output_format": output_format,
    }
    if language is not None:
        kwargs["language_code"] = language
    if stability is not None:
        kwargs["settings"] = ModelSettingsResponseModel(stability=stability)
    if seed is not None:
        kwargs["seed"] = seed
    try:
        return collect(client.text_to_dialogue.convert(**kwargs))
    except ApiError as exc:
        raise describe_api_error(exc, "generating the dialogue") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "generating the dialogue") from exc


def sound_effect(client: ElevenLabs, text: str, duration: float | None, prompt_influence: float | None, output_format: str, loop: bool) -> bytes:
    kwargs: dict[str, Any] = {"text": text, "output_format": output_format, "loop": loop}
    if duration is not None:
        kwargs["duration_seconds"] = duration
    if prompt_influence is not None:
        kwargs["prompt_influence"] = prompt_influence
    try:
        return collect(client.text_to_sound_effects.convert(**kwargs))
    except ApiError as exc:
        raise describe_api_error(exc, "generating the sound effect") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "generating the sound effect") from exc


def music(client: ElevenLabs, prompt: str, seconds: float, output_format: str, instrumental: bool, seed: int | None) -> bytes:
    kwargs: dict[str, Any] = {"prompt": prompt, "music_length_ms": int(seconds * 1000), "output_format": output_format, "force_instrumental": instrumental}
    if seed is not None:
        kwargs["seed"] = seed
    try:
        return collect(client.music.compose(**kwargs))
    except ApiError as exc:
        raise describe_api_error(exc, "composing music") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "composing music") from exc


def speech_to_text(client: ElevenLabs, path: Path, model_id: str, language: str | None, diarize: bool, num_speakers: int | None, audio_events: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model_id": model_id, "diarize": diarize, "tag_audio_events": audio_events}
    if language is not None:
        kwargs["language_code"] = language
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers
    try:
        with path.open("rb") as handle:
            result = client.speech_to_text.convert(file=handle, **kwargs)
    except ApiError as exc:
        raise describe_api_error(exc, f"transcribing {path}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"transcribing {path}") from exc
    return result.model_dump()


def isolate(client: ElevenLabs, path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return collect(client.audio_isolation.convert(audio=handle))
    except ApiError as exc:
        raise describe_api_error(exc, f"isolating voice in {path}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"isolating voice in {path}") from exc


def speech_to_speech(client: ElevenLabs, path: Path, voice_id: str, model_id: str, output_format: str, settings: Settings, seed: int | None, remove_noise: bool) -> bytes:
    kwargs: dict[str, Any] = {"voice_id": voice_id, "model_id": model_id, "output_format": output_format, "remove_background_noise": remove_noise}
    sdk_settings = settings.to_sdk()
    if sdk_settings is not None:
        kwargs["voice_settings"] = sdk_settings.model_dump_json()
    if seed is not None:
        kwargs["seed"] = seed
    try:
        with path.open("rb") as handle:
            return collect(client.speech_to_speech.convert(audio=handle, **kwargs))
    except ApiError as exc:
        raise describe_api_error(exc, f"converting {path} to voice {voice_id}") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, f"converting {path} to voice {voice_id}") from exc


@dataclass(frozen=True)
class VoicePreview:
    generated_voice_id: str
    audio: bytes
    media_type: str
    duration: float | None
    language: str | None


def design_voice(client: ElevenLabs, description: str, text: str | None, model_id: str | None, seed: int | None, guidance: float | None, loudness: float | None) -> list[VoicePreview]:
    kwargs: dict[str, Any] = {"voice_description": description}
    if text is not None:
        kwargs["text"] = text
    else:
        kwargs["auto_generate_text"] = True
    if model_id is not None:
        kwargs["model_id"] = model_id
    if seed is not None:
        kwargs["seed"] = seed
    if guidance is not None:
        kwargs["guidance_scale"] = guidance
    if loudness is not None:
        kwargs["loudness"] = loudness
    try:
        response = client.text_to_voice.design(**kwargs)
    except ApiError as exc:
        raise describe_api_error(exc, "designing a voice") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "designing a voice") from exc
    return [
        VoicePreview(p.generated_voice_id, base64.b64decode(p.audio_base_64), p.media_type, p.duration_secs, p.language)
        for p in response.previews
    ]


def create_designed_voice(client: ElevenLabs, name: str, description: str, generated_voice_id: str) -> str:
    try:
        voice = client.text_to_voice.create(voice_name=name, voice_description=description, generated_voice_id=generated_voice_id)
    except ApiError as exc:
        raise describe_api_error(exc, "saving the designed voice") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "saving the designed voice") from exc
    return voice.voice_id


def clone_voice(client: ElevenLabs, name: str, samples: list[Path], description: str | None, remove_noise: bool) -> str:
    handles = [p.open("rb") for p in samples]
    try:
        kwargs: dict[str, Any] = {"name": name, "files": handles, "remove_background_noise": remove_noise}
        if description is not None:
            kwargs["description"] = description
        response = client.voices.ivc.create(**kwargs)
    except ApiError as exc:
        raise describe_api_error(exc, "cloning the voice") from exc
    except httpx.HTTPError as exc:
        raise network_error(exc, "cloning the voice") from exc
    finally:
        for handle in handles:
            handle.close()
    return response.voice_id
