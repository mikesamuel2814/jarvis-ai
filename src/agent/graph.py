"""
JARVIS Agent State Machine (LangGraph)
Orchestrates intent -> plan -> execute -> observe -> synthesize.
"""
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from dataclasses import dataclass, field
import json


class AgentState(TypedDict):
    """LangGraph state shape."""
    user_input: str
    intent: str
    intent_category: str
    urgency: str
    scope: str
    complexity: int
    subtasks: List[Dict[str, Any]]
    results: Annotated[List[Dict[str, Any]], lambda x, y: x + y]
    synthesized_response: str
    session_id: str
    metadata: Dict[str, Any]


@dataclass
class SubTask:
    id: str
    description: str
    tool: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed
    result: Any = None


class JarvisGraph:
    """LangGraph-based agent orchestrator."""

    def __init__(self):
        self.nodes = {}
        self.edges = {}

    def classify_intent(self, state: AgentState) -> AgentState:
        """Classify user intent into category, urgency, scope, complexity."""
        # Stub: in production, use phi4-mini or rule-based classifier
        state["intent_category"] = "system"  # system/security/coding/project/info
        state["urgency"] = "normal"          # low/normal/high/critical
        state["scope"] = "READ"              # READ/LOCAL/NETWORK/PRIVILEGED
        state["complexity"] = 3              # 1-10
        return state

    def decompose(self, state: AgentState) -> AgentState:
        """Break request into sub-tasks with dependency graph."""
        # Stub: rule-based decomposition
        if "cpu" in state["user_input"].lower():
            state["subtasks"] = [
                {"id": "t1", "description": "Get CPU info", "tool": "cpu_info", "parameters": {}, "dependencies": []},
                {"id": "t2", "description": "Get RAM usage", "tool": "ram_usage", "parameters": {}, "dependencies": []},
                {"id": "t3", "description": "Synthesize report", "tool": None, "parameters": {}, "dependencies": ["t1", "t2"]},
            ]
        else:
            state["subtasks"] = [
                {"id": "t1", "description": "Process user request", "tool": None, "parameters": {}, "dependencies": []}
            ]
        return state

    def scope_check(self, state: AgentState) -> AgentState:
        """Validate each sub-task within session scope."""
        for st in state["subtasks"]:
            if st.get("tool") in ("service_restart", "apt_upgrade", "reboot"):
                state["scope"] = "PRIVILEGED"
        return state

    def dispatch(self, state: AgentState) -> AgentState:
        """Dispatch sub-tasks to tools (or simulate)."""
        results = []
        for st in state["subtasks"]:
            tool_name = st.get("tool")
            if tool_name:
                results.append({
                    "task_id": st["id"],
                    "tool": tool_name,
                    "status": "done",
                    "output": f"[stub] {tool_name} executed."
                })
            else:
                results.append({
                    "task_id": st["id"],
                    "tool": None,
                    "status": "done",
                    "output": "Synthesis step."
                })
        state["results"] = results
        return state

    def synthesize(self, state: AgentState) -> AgentState:
        """Gather results and generate final response."""
        outputs = [r["output"] for r in state["results"]]
        state["synthesized_response"] = "Sir, here is the result:\n" + "\n".join(outputs)
        return state

    def run(self, user_input: str, session_id: str = "default") -> AgentState:
        """Execute the full graph."""
        state: AgentState = {
            "user_input": user_input,
            "intent": "",
            "intent_category": "",
            "urgency": "normal",
            "scope": "READ",
            "complexity": 1,
            "subtasks": [],
            "results": [],
            "synthesized_response": "",
            "session_id": session_id,
            "metadata": {},
        }
        state = self.classify_intent(state)
        state = self.decompose(state)
        state = self.scope_check(state)
        state = self.dispatch(state)
        state = self.synthesize(state)
        return state
