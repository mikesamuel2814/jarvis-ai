"""
Jarvis v3 Nano-Bot Swarm — Bot Types

Seven specialized bot roles (spec Part 5.2). Each bot is a lightweight async
worker that handles a class of tasks. The Guard bot is the central security
gatekeeper — every state-changing tool execution must pass its authorize()
check before running.
"""

from .base import BaseBot, BotType, BotResult
from .scanner_bot import ScannerBot
from .verifier_bot import VerifierBot
from .fetcher_bot import FetcherBot
from .analyzer_bot import AnalyzerBot
from .builder_bot import BuilderBot
from .test_bot import TestBot
from .guard_bot import GuardBot
from .dispatcher import BotDispatcher

__all__ = [
    "BaseBot",
    "BotType",
    "BotResult",
    "ScannerBot",
    "VerifierBot",
    "FetcherBot",
    "AnalyzerBot",
    "BuilderBot",
    "TestBot",
    "GuardBot",
    "BotDispatcher",
]
