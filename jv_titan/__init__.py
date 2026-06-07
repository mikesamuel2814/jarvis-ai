#!/usr/bin/env python3
"""
JV Titan — Jarvis Core Brain Protocol
=====================================
Consciousness • Emotion • Memory • Decision • Growth • Persona

Public API for integrating JV Titan into Jarvis v3.

Usage:
    from jv_titan import get_titan_context, train_mike_input, touch_interaction

    # Inject consciousness + emotion + memory into any LLM prompt
    context = get_titan_context(query="check cpu")

    # Process a training session from Mike
    result = train_mike_input("I grew up in Dhaka and love spicy food")

    # Mark a normal interaction (silent growth)
    touch_interaction("Hello Jarvis", action_taken="", outcome="")
"""

from .blueprint_encoder import export_full_blueprint, import_full_blueprint
from .consciousness import (
    get_state as get_consciousness_state,
    get_identity,
    get_mike_model,
    touch as touch_consciousness,
    update_mike_model,
    format_context_block as format_consciousness_block,
)
from .emotion_engine import (
    get_state as get_emotion_state,
    update as update_emotion,
    format_emotion_block,
)
from .memory_tape import (
    add_memory,
    retrieve_memories,
    get_timeline_summary,
    format_memory_context,
)
from .decision_core import (
    get_autonomy_tier,
    can_act_without_approval,
    log_decision,
    add_suggestion,
    get_pending_suggestions,
    format_decision_context,
)
from .growth_tracker import (
    get_state as get_growth_state,
    add_xp,
    format_growth_card,
)
from .persona import (
    get_communication_style,
    format_persona_block,
    deepen_relationship,
    update_preference,
    add_shared_joke,
)
from .training_interface import (
    process_training_input as train_mike_input,
    process_interaction as touch_interaction,
    get_training_summary,
)

__all__ = [
    # Core state
    "get_titan_context",
    "get_consciousness_state",
    "get_emotion_state",
    "get_growth_state",
    "get_mike_model",
    "get_identity",
    # Interaction
    "touch_consciousness",
    "update_emotion",
    "touch_interaction",
    "train_mike_input",
    # Memory
    "add_memory",
    "retrieve_memories",
    "get_timeline_summary",
    "format_memory_context",
    # Decision
    "get_autonomy_tier",
    "can_act_without_approval",
    "log_decision",
    "add_suggestion",
    "get_pending_suggestions",
    # Growth
    "add_xp",
    "format_growth_card",
    "get_training_summary",
    # Persona
    "get_communication_style",
    "deepen_relationship",
    "update_preference",
    "add_shared_joke",
    # Blueprint
    "export_full_blueprint",
    "import_full_blueprint",
]


def get_titan_context(query: str = "", include_memory: bool = True) -> str:
    """
    Generate the full JV Titan context block for injection into LLM prompts.
    This gives the AI model access to consciousness, emotion, persona,
    decision-making status, and relevant memories.
    """
    parts = [
        format_consciousness_block(),
        format_emotion_block(),
        format_persona_block(),
        format_decision_context(),
    ]
    if include_memory:
        mem_ctx = format_memory_context(query=query, n=3)
        if mem_ctx:
            parts.append(mem_ctx)

    return "\n\n".join(parts)
