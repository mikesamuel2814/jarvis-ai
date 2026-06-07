"""
Jarvis v3 Dynamic Tool Builder (Milestone 5)

5-phase pipeline that synthesizes new @jarvis_tool tools on demand:

  1. Requirement Analysis  — parse the need, infer rank/scope, I/O schema.
  2. Code Generation       — Builder bot (Kimi K2.6) writes the tool.
  3. Security Review       — SecurityBot static-audits the generated code.
  4. Integration + Test    — Test bot compiles/validates in a sandbox.
  5. Deployment            — versioned write to tools/dynamic/<name>/vN/.

Generated tools are never executed during the build; only compiled/validated.
"""

from .security_bot import SecurityBot, SecurityFinding
from .builder import ToolBuilder, BuildResult

__all__ = ["SecurityBot", "SecurityFinding", "ToolBuilder", "BuildResult"]
