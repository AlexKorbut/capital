"""Speech-to-text abstraction (provider-agnostic).

Default is OpenAI Whisper; swap via STT_PROVIDER in .env without touching the
voice-input agent.
"""
from __future__ import annotations

import io
from typing import Protocol

from core.config import settings
from core.llm import api_key_for


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, filename: str = "audio.webm") -> str:
        ...


class OpenAIWhisperTranscriber:
    """OpenAI Whisper (whisper-1)."""

    def __init__(self, api_key: str, language: str = "ru") -> None:
        self._api_key = api_key
        self._language = language

    async def transcribe(self, audio: bytes, filename: str = "audio.webm") -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        buffer = io.BytesIO(audio)
        buffer.name = filename
        result = await client.audio.transcriptions.create(
            model="whisper-1",
            file=buffer,
            language=self._language,
            response_format="text",
        )
        return result if isinstance(result, str) else getattr(result, "text", "")


class DemoTranscriber:
    """Returns a canned transcript so voice input is testable with no API key."""

    async def transcribe(self, audio: bytes, filename: str = "audio.webm") -> str:
        from core.demo import DEMO_TRANSCRIPT

        return DEMO_TRANSCRIPT


def get_transcriber() -> Transcriber:
    # No OpenAI key (or explicit demo) -> deterministic stub, no network call.
    # BYOK: the user's own OpenAI key (mode "own") or the server key.
    openai_key = api_key_for("openai")
    if settings.demo_mode or not openai_key:
        return DemoTranscriber()
    if settings.stt_provider == "openai":
        return OpenAIWhisperTranscriber(
            api_key=openai_key, language=settings.stt_language
        )
    # Placeholders for future providers (local faster-whisper, groq).
    raise NotImplementedError(
        f"STT provider '{settings.stt_provider}' not implemented yet."
    )
