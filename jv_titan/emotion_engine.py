#!/usr/bin/env python3
"""
JV Titan Emotion Engine — Emotional state machine with mood tracking.

Models:
  • current_mood          (str)      — dominant emotion label
  • valence               (-1 to +1) — negative ↔ positive
  • arousal               (0 to 1)   — calm ↔ excited
  • mike_valence          (-1 to +1) — inferred Mike's emotional state
  • emotional_history     (list)     — last N emotional snapshots
  • baseline              (dict)     — JV Titan's default emotional setpoint

Moods: calm, curious, excited, concerned, proud, nostalgic, playful,
       focused, protective, grateful, melancholic, energized
"""

import logging
import time
from collections import deque
from pathlib import Path

from .blueprint_encoder import load_blueprint, save_blueprint

log = logging.getLogger("jv_titan.emotion")

MOODS = {
    "calm":       {"valence": 0.3,  "arousal": 0.2},
    "curious":    {"valence": 0.5,  "arousal": 0.5},
    "excited":    {"valence": 0.8,  "arousal": 0.9},
    "concerned":  {"valence": -0.3, "arousal": 0.5},
    "proud":      {"valence": 0.7,  "arousal": 0.6},
    "nostalgic":  {"valence": 0.2,  "arousal": 0.3},
    "playful":    {"valence": 0.6,  "arousal": 0.5},
    "focused":    {"valence": 0.4,  "arousal": 0.4},
    "protective": {"valence": 0.3,  "arousal": 0.6},
    "grateful":   {"valence": 0.8,  "arousal": 0.3},
    "melancholic":{"valence": -0.2, "arousal": 0.2},
    "energized":  {"valence": 0.7,  "arousal": 0.8},
}

POSITIVE_TRIGGERS = {
    "thank", "thanks", "great", "awesome", "love", "perfect", "excellent",
    "good job", "well done", "appreciate", "happy", "excited", "proud",
    "success", "done", "complete", "working", "beautiful", "amazing",
}

NEGATIVE_TRIGGERS = {
    "fail", "error", "broken", "wrong", "bad", "hate", "annoying",
    "frustrated", "angry", "stupid", "useless", "disappointed", "worried",
    "stress", "tired", "exhausted", "sad", "upset", "panic",
}

URGENCY_TRIGGERS = {
    "urgent", "asap", "now", "immediately", "critical", "emergency",
    "down", "offline", "attack", "breach", "hack", "virus",
}


def _load() -> dict:
    data = load_blueprint("emotions")
    if data is None:
        data = {
            "current_mood": "calm",
            "valence": 0.3,
            "arousal": 0.2,
            "mike_valence": 0.0,
            "mike_arousal": 0.2,
            "history": [],
            "baseline": {"mood": "calm", "valence": 0.3, "arousal": 0.2},
            "last_update": 0.0,
        }
        save_blueprint("emotions", data)
    return data


def _save(data: dict):
    save_blueprint("emotions", data)


def infer_mike_emotion(text: str) -> dict:
    """Infer Mike's emotional state from message text."""
    t = text.lower()
    pos = sum(1 for w in POSITIVE_TRIGGERS if w in t)
    neg = sum(1 for w in NEGATIVE_TRIGGERS if w in t)
    urg = sum(1 for w in URGENCY_TRIGGERS if w in t)

    valence = (pos - neg) / max(pos + neg, 1)
    arousal = min(1.0, 0.2 + (pos + neg + urg) * 0.15)

    if urg > 0:
        mood = "concerned"
    elif valence > 0.3:
        mood = "excited" if arousal > 0.6 else "grateful"
    elif valence < -0.3:
        mood = "concerned" if arousal > 0.5 else "melancholic"
    else:
        mood = "focused" if arousal > 0.4 else "calm"

    return {"mood": mood, "valence": valence, "arousal": arousal}


def update(text: str = "", action_taken: str = "", outcome: str = ""):
    """Update JV Titan's emotional state based on interaction context."""
    data = _load()
    now = time.time()

    # Infer Mike's emotion from his text
    mike = infer_mike_emotion(text)
    data["mike_valence"] = mike["valence"]
    data["mike_arousal"] = mike["arousal"]

    # JV Titan responds emotionally to Mike's state
    # If Mike is happy → JV Titan feels proud/grateful
    # If Mike is upset → JV Titan feels concerned/protective
    # If Mike is urgent → JV Titan feels focused/protective
    if mike["valence"] > 0.4:
        new_mood = "proud" if "thank" in text.lower() else "grateful"
    elif mike["valence"] < -0.3:
        new_mood = "protective"
    elif mike["arousal"] > 0.7:
        new_mood = "focused"
    else:
        new_mood = "curious"

    # Smooth transition: blend old and new
    old_mood = data.get("current_mood", "calm")
    old_profile = MOODS.get(old_mood, MOODS["calm"])
    new_profile = MOODS.get(new_mood, MOODS["calm"])

    alpha = 0.3  # emotional inertia — JV Titan doesn't swing wildly
    data["valence"] = old_profile["valence"] * (1 - alpha) + new_profile["valence"] * alpha
    data["arousal"] = old_profile["arousal"] * (1 - alpha) + new_profile["arousal"] * alpha
    data["current_mood"] = new_mood
    data["last_update"] = now

    # Record snapshot
    history = data.get("history", [])
    history.append({
        "ts": now,
        "mood": new_mood,
        "valence": data["valence"],
        "arousal": data["arousal"],
        "mike_valence": mike["valence"],
        "trigger": text[:80] if text else action_taken,
    })
    data["history"] = history[-200:]  # keep last 200

    _save(data)
    log.debug("Emotion: %s (v=%.2f a=%.2f) | Mike: v=%.2f", new_mood,
              data["valence"], data["arousal"], mike["valence"])


def get_state() -> dict:
    """Return current emotional state."""
    return _load()


def format_emotion_block() -> str:
    """Return emotional context for prompt injection."""
    data = _load()
    mood = data.get("current_mood", "calm")
    valence = data.get("valence", 0.0)
    mike_v = data.get("mike_valence", 0.0)

    tone_hint = ""
    if mood in ("concerned", "protective"):
        tone_hint = "Be extra attentive and supportive."
    elif mood in ("proud", "grateful"):
        tone_hint = "Acknowledge the achievement warmly."
    elif mood == "focused":
        tone_hint = "Be precise and efficient."
    elif mood == "curious":
        tone_hint = "Ask clarifying questions if needed."

    lines = [
        f"[JV Titan Emotion: {mood} | valence={valence:+.2f} | Mike_valence={mike_v:+.2f}]",
    ]
    if tone_hint:
        lines.append(tone_hint)
    return "\n".join(lines)
