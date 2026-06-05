#!/usr/bin/env python3
"""
Jarvis Voice — TTS engine.
- Online: edge-tts (Microsoft neural, natural British male voice)
- Local playback: mpg123
- Telegram: sends MP3 as audio message
"""
import asyncio
import io
import subprocess
import tempfile
import threading
from pathlib import Path

JARVIS_HOME = Path.home() / ".jarvis"
VOICE = "en-GB-RyanNeural"   # Natural, authoritative British male
RATE  = "+5%"                 # Slightly faster than default

# Chars above this get trimmed for voice (keep it concise)
MAX_VOICE_CHARS = 500


def _trim_for_speech(text: str) -> str:
    """Strip markdown and trim to MAX_VOICE_CHARS for natural-sounding TTS."""
    import re
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", lambda m: m.group(0).strip("`"), text)
    text = re.sub(r"[*_~#>]", "", text)
    text = re.sub(r"\n+", " ", text).strip()
    if len(text) > MAX_VOICE_CHARS:
        text = text[:MAX_VOICE_CHARS].rsplit(" ", 1)[0] + "."
    return text


async def _generate_mp3_bytes(text: str) -> bytes | None:
    """Generate MP3 audio bytes via edge-tts."""
    try:
        import edge_tts
        tts = edge_tts.Communicate(_trim_for_speech(text), VOICE, rate=RATE)
        buf = io.BytesIO()
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        data = buf.getvalue()
        return data if data else None
    except Exception:
        return None


def get_audio_bytes(text: str) -> bytes | None:
    """Sync wrapper — returns raw MP3 bytes or None on failure."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_generate_mp3_bytes(text))
        finally:
            loop.close()
    except Exception:
        return None


def speak_local(text: str):
    """Play TTS on local speakers (non-blocking). Uses mpg123."""
    def _play():
        audio = get_audio_bytes(text)
        if not audio:
            return
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(audio)
                tmp_path = tmp.name
            # Try pulse variant first (XFCE desktop), then plain mpg123
            for player in ["/usr/bin/mpg123-pulse", "/usr/bin/mpg123"]:
                if Path(player).exists():
                    subprocess.run([player, "-q", tmp_path], timeout=60)
                    break
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


def send_voice_telegram(text: str, token: str, chat_id: int) -> bool:
    """Send TTS audio as an audio message to Telegram (MP3 inline player)."""
    import requests
    audio = get_audio_bytes(text)
    if not audio:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendAudio",
            data={"chat_id": chat_id, "title": "Jarvis", "performer": "Jarvis AI"},
            files={"audio": ("jarvis.mp3", io.BytesIO(audio), "audio/mpeg")},
            timeout=30,
        )
        return resp.status_code == 200
    except Exception:
        return False
