"""
JARVIS Text-to-Speech (TTS)
Supports: edge-tts (cloud, high quality) and piper-tts (offline, lightweight).
Integrates with v3 tools/ ecosystem.
"""
import os
import asyncio
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

JARVIS_DIR = Path("/home/kali/.jarvis")
PIPER_VOICE_DIR = JARVIS_DIR / "models" / "voices" / "piper"
DEFAULT_EDGE_VOICE = "en-US-AndrewNeural"


class TTSEngine:
    """Text-to-speech engine with cloud and offline backends."""

    def __init__(self, backend: str = "edge"):
        self.backend = backend
        self._piper_voice = None

    async def _edge_tts(self, text: str, voice: str = DEFAULT_EDGE_VOICE, output_path: Optional[str] = None) -> str:
        """Generate speech using edge-tts (Azure cloud)."""
        import edge_tts

        if output_path is None:
            output_path = tempfile.mktemp(suffix=".mp3")

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        return output_path

    def _piper_tts(self, text: str, output_path: Optional[str] = None) -> str:
        """Generate speech using piper-tts (offline)."""
        try:
            from piper import PiperVoice
        except ImportError:
            raise RuntimeError("piper-tts not installed. Run: pip install piper-tts")

        # Find first available voice model
        voice_files = list(PIPER_VOICE_DIR.glob("*.onnx"))
        if not voice_files:
            raise FileNotFoundError(f"No Piper voice models found in {PIPER_VOICE_DIR}. Download from HuggingFace rhasspy/piper-voices")

        voice_path = str(voice_files[0])
        config_path = voice_path + ".json"

        if self._piper_voice is None:
            self._piper_voice = PiperVoice.load(voice_path, config_path)

        if output_path is None:
            output_path = tempfile.mktemp(suffix=".wav")

        with open(output_path, "wb") as f:
            self._piper_voice.synthesize(text, f)

        return output_path

    def speak(self, text: str, backend: Optional[str] = None, voice: Optional[str] = None, output_path: Optional[str] = None) -> str:
        """Generate speech from text.

        Args:
            text: Text to speak
            backend: 'edge' (cloud) or 'piper' (offline)
            voice: Voice ID (edge-tts voice name)
            output_path: Output audio file path

        Returns:
            Path to generated audio file
        """
        backend = backend or self.backend

        if backend == "edge":
            return asyncio.run(self._edge_tts(text, voice or DEFAULT_EDGE_VOICE, output_path))
        elif backend == "piper":
            return self._piper_tts(text, output_path)
        else:
            raise ValueError(f"Unknown backend: {backend}")

    def list_voices(self) -> list:
        """List available edge-tts voices."""
        import edge_tts
        import asyncio
        voices = []
        voice_list = asyncio.run(edge_tts.list_voices())
        for voice in voice_list:
            if voice["Locale"].startswith("en"):
                voices.append({
                    "name": voice["ShortName"],
                    "gender": voice["Gender"],
                    "locale": voice["Locale"]
                })
        return voices


def jarvis_speak(text: str, backend: str = "edge", voice: str = DEFAULT_EDGE_VOICE) -> Dict[str, Any]:
    """Tool wrapper for JARVIS tool registry."""
    engine = TTSEngine(backend=backend)
    output_path = engine.speak(text, voice=voice)
    return {
        "status": "success",
        "audio_path": output_path,
        "backend": backend,
        "text": text
    }


if __name__ == "__main__":
    engine = TTSEngine()
    print("TTS Engine initialized")
    print(f"Available edge-tts voices: {len(engine.list_voices())}")
