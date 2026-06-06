"""
Jarvis v3 @jarvis_tool Decorator
Declares tool metadata: name, description, parameters, rank, scope, category, tags.
"""

import inspect
import json
from functools import wraps
from typing import Callable, Dict, List, Optional, Any

from .result import ToolResult

# Tool registry populated by the decorator
_TOOL_REGISTRY: Dict[str, dict] = {}


def jarvis_tool(
    name: str,
    description: str,
    params: Optional[Dict[str, Any]] = None,
    rank: str = "R0",
    scope: str = "READ",
    category: str = "general",
    tags: Optional[List[str]] = None,
):
    """
    Decorator that registers a function as a Jarvis tool.

    Args:
        name: Unique tool name (snake_case)
        description: Human-readable description for the LLM planner
        params: JSON-schema-ish dict of parameters { "arg_name": {"type": "string", "required": True} }
        rank: Action rank R0-R6 (progressive trust)
        scope: READ | LOCAL | NETWORK | PRIVILEGED
        category: Tool category for grouping
        tags: Semantic tags for search
    """
    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        # Build param schema from decorator + signature defaults
        param_schema = {}
        for p_name, p in sig.parameters.items():
            if p_name in ("ctx", "context"):
                continue  # skip injected context param
            schema = (params or {}).get(p_name, {"type": "string", "required": p.default is inspect.Parameter.empty})
            if p.default is not inspect.Parameter.empty and p.default is not None:
                schema["default"] = p.default
            param_schema[p_name] = schema

        metadata = {
            "name": name,
            "description": description,
            "params": param_schema,
            "rank": rank,
            "scope": scope,
            "category": category,
            "tags": tags or [],
            "func": func,
            "doc": func.__doc__ or description,
        }
        _TOOL_REGISTRY[name] = metadata

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._jarvis_tool = metadata
        return wrapper

    return decorator


def get_tool_metadata(name: str) -> Optional[dict]:
    meta = _TOOL_REGISTRY.get(name)
    if meta is None:
        return None
    # Return a copy without the func reference (for serialization)
    return {k: v for k, v in meta.items() if k != "func"}


def list_registered_tools() -> List[str]:
    return list(_TOOL_REGISTRY.keys())


def get_all_metadata() -> Dict[str, dict]:
    return {k: get_tool_metadata(k) for k in _TOOL_REGISTRY}


def get_tool_func(name: str) -> Optional[Callable]:
    meta = _TOOL_REGISTRY.get(name)
    return meta["func"] if meta else None
