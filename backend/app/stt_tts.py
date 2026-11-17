"""Speech-to-text and text-to-speech providers — PRD Sec 8 / Sec 11.

Simulated providers run with zero credentials (tests / local dev).
Groq Whisper is used only when GROQ_API_KEY is set; nothing here ever
decides a booking — transcription is input to the rule-based dialogue.
"""
import os
from typing import Protocol

from .config import settings

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class STTProvider(Protocol):
    """Speech-to-text interface."""

    def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe raw audio bytes to text (Hinglish expected)."""
        ...


class SimulatedSTT:
    """Test/dev STT: returns the input text unchanged.

    Accepts ``str`` directly (tests) or UTF-8 ``bytes``; anything that
    is not decodable text returns an empty string.
    """

    def transcribe(self, audio_bytes) -> str:
        if isinstance(audio_bytes, str):
            return audio_bytes
        if isinstance(audio_bytes, (bytes, bytearray)):
            try:
                return bytes(audio_bytes).decode("utf-8")
            except UnicodeDecodeError:
                return ""
        return ""


class GroqWhisperSTT:
    """Groq Whisper STT (whisper-large-v3-turbo). Live only.

    Requires GROQ_API_KEY; raises ValueError without one so callers can
    fall back to SimulatedSTT instead of failing silently.
    """

    def __init__(self, api_key: str | None = None,
                 model: str = "whisper-large-v3-turbo"):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is required for GroqWhisperSTT")
        self.model = model

    def transcribe(self, audio_bytes: bytes) -> str:
        import httpx

        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {"model": self.model, "language": "hi",
                "response_format": "json"}
        resp = httpx.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files=files, data=data, timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")


class TTSProvider(Protocol):
    """Text-to-speech interface."""

    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` to audio bytes."""
        ...


class SimulatedTTS:
    """Test/dev TTS: returns empty audio bytes with metadata.

    ``last_metadata`` records what would have been spoken, so tests can
    assert on the text without needing any audio pipeline.
    """

    def __init__(self):
        self.last_metadata: dict = {}

    def synthesize(self, text: str) -> bytes:
        self.last_metadata = {
            "text": text, "provider": "simulated",
            "language": "hinglish", "audio_bytes": 0,
        }
        return b""


def get_stt() -> STTProvider:
    """Groq Whisper when GROQ_API_KEY is set, else the simulated STT."""
    if settings.groq_api_key:
        return GroqWhisperSTT(api_key=settings.groq_api_key,
                              model=settings.groq_stt_model)
    return SimulatedSTT()


def get_tts() -> TTSProvider:
    """Simulated TTS (no live TTS provider is configured yet)."""
    return SimulatedTTS()
