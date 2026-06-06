#!/usr/bin/env python3
"""
Jarvis v3 Tool Framework Tests
Parametric tests over all 127 registered tools.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/kali/.jarvis")))

# Import all categories to register tools
from tools.core.system import *
from tools.core.process import *
from tools.core.file import *
from tools.core.network import *
from tools.core.security import *
from tools.core.security_audit import *
from tools.core.dev import *
from tools.core.database import *
from tools.core.docker import *
from tools.core.web import *
from tools.core.backup import *
from tools.core.automation import *
from tools.core.comms import *

from tools.decorator import list_registered_tools, get_tool_metadata, get_all_metadata
from tools.registry import ToolRegistry
from tools.result import ToolResult


def test_all_tools_registered():
    tools = list_registered_tools()
    assert len(tools) >= 127, f"Expected 127+ tools, got {len(tools)}"
    print(f"✓ {len(tools)} tools registered")


def test_tool_metadata_complete():
    for name, meta in get_all_metadata().items():
        assert meta.get("name") == name, f"{name}: name mismatch"
        assert meta.get("description"), f"{name}: missing description"
        assert meta.get("rank") in ("R0", "R1", "R2", "R3", "R4", "R5", "R6"), f"{name}: invalid rank"
        assert meta.get("scope") in ("READ", "LOCAL", "NETWORK", "PRIVILEGED"), f"{name}: invalid scope"
        assert meta.get("category"), f"{name}: missing category"
        assert isinstance(meta.get("tags"), list), f"{name}: tags not a list"
    print("✓ All metadata valid")


def test_category_counts():
    from collections import Counter
    cats = Counter(m["category"] for m in get_all_metadata().values())
    expected = {
        "system": 15,
        "process": 12,
        "file": 12,
        "network": 12,
        "security": 15,
        "security_audit": 10,
        "dev": 10,
        "database": 8,
        "docker": 8,
        "web": 8,
        "backup": 6,
        "automation": 6,
        "comms": 5,
    }
    for cat, count in expected.items():
        actual = cats.get(cat, 0)
        assert actual == count, f"Category {cat}: expected {count}, got {actual}"
    print("✓ Category counts match spec")


def test_registry_keyword_search():
    reg = ToolRegistry()
    results = reg.find_tools("cpu usage", n=5)
    assert len(results) > 0, "Keyword search returned no results"
    names = [r["name"] for r in results]
    assert "cpu_info" in names, f"cpu_info not in search results: {names}"
    print("✓ Registry keyword search works")


def test_registry_execute_ok():
    reg = ToolRegistry()
    result = reg.execute("os_info")
    assert result.success, f"os_info failed: {result.error}"
    assert "Linux" in result.output, f"Unexpected output: {result.output}"
    print("✓ Registry execution works")


def test_registry_execute_fail():
    reg = ToolRegistry()
    result = reg.execute("nonexistent_tool_xyz")
    assert not result.success
    assert "not found" in result.error.lower()
    print("✓ Registry handles missing tools")


def test_tool_result_dataclass():
    r = ToolResult.ok(output="test", data={"x": 1})
    assert r.success
    assert r.output == "test"
    assert r.data["x"] == 1
    r2 = ToolResult.fail(error="boom")
    assert not r2.success
    assert r2.error == "boom"
    print("✓ ToolResult dataclass works")


def test_v2_compat_shim():
    from tools.compat import run_action, list_v2_actions
    actions = list_v2_actions()
    assert len(actions) >= 60, f"Expected 60+ v2 actions, got {len(actions)}"
    # Test a safe read-only action
    result = run_action("disk")
    assert result["success"], f"disk action failed: {result['output']}"
    print("✓ v2 compat shim works")


if __name__ == "__main__":
    test_all_tools_registered()
    test_tool_metadata_complete()
    test_category_counts()
    test_registry_keyword_search()
    test_registry_execute_ok()
    test_registry_execute_fail()
    test_tool_result_dataclass()
    test_v2_compat_shim()
    print("\n=== All tests passed ===")
