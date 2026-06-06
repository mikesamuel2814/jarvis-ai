#!/usr/bin/env python3
"""
Jarvis v3 Tool Framework — Simple Verification Tests
Run: python3 tools/tests/test_tools_simple.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path("/home/kali/.jarvis")))

# Import categories one by one
categories = [
    "tools.core.system",
    "tools.core.process",
    "tools.core.file",
    "tools.core.network",
    "tools.core.security",
    "tools.core.security_audit",
    "tools.core.dev",
    "tools.core.database",
    "tools.core.docker",
    "tools.core.web",
    "tools.core.backup",
    "tools.core.automation",
    "tools.core.comms",
]

for cat in categories:
    __import__(cat)

from tools.decorator import list_registered_tools, get_all_metadata
from tools.registry import ToolRegistry
from tools.result import ToolResult

errors = []

# Test 1: Count
tools = list_registered_tools()
print(f"1. Registered tools: {len(tools)}")
if len(tools) < 127:
    errors.append(f"Expected 127+ tools, got {len(tools)}")

# Test 2: Metadata
for name, meta in get_all_metadata().items():
    if meta.get("name") != name:
        errors.append(f"{name}: name mismatch")
    if not meta.get("description"):
        errors.append(f"{name}: missing description")
print(f"2. Metadata check: {'PASS' if not [e for e in errors if 'name' in e or 'description' in e] else 'FAIL'}")

# Test 3: Category counts
from collections import Counter
cats = Counter(m["category"] for m in get_all_metadata().values())
expected = {
    "system": 15, "process": 12, "file": 12, "network": 12,
    "security": 15, "security_audit": 10, "dev": 10, "database": 8,
    "docker": 8, "web": 8, "backup": 6, "automation": 6, "comms": 5,
}
for cat, count in expected.items():
    actual = cats.get(cat, 0)
    if actual != count:
        errors.append(f"Category {cat}: expected {count}, got {actual}")
print(f"3. Category counts: {'PASS' if all(cats.get(k) == v for k, v in expected.items()) else 'FAIL'}")

# Test 4: Execute safe tool
reg = ToolRegistry()
r = reg.execute("os_info")
print(f"4. Execute os_info: {'PASS' if r.success else 'FAIL'} ({r.output[:40]}...)")
if not r.success:
    errors.append(f"os_info failed: {r.error}")

# Test 5: Execute another safe tool
r = reg.execute("ram_usage")
print(f"5. Execute ram_usage: {'PASS' if r.success else 'FAIL'} ({r.output[:40]}...)")

# Test 6: Missing tool
r = reg.execute("nonexistent_tool")
print(f"6. Missing tool: {'PASS' if not r.success and 'not found' in r.error.lower() else 'FAIL'}")

# Test 7: Registry keyword search
results = reg.find_tools("cpu usage", n=5)
print(f"7. Keyword search: {'PASS' if any(r['name'] == 'cpu_info' for r in results) else 'FAIL'}")

# Test 8: v2 compat
from tools.compat import run_action, list_v2_actions
v2_actions = list_v2_actions()
print(f"8. v2 compat actions: {'PASS' if len(v2_actions) >= 60 else 'FAIL'} ({len(v2_actions)} actions)")

r = run_action("disk")
print(f"9. v2 run_action(disk): {'PASS' if r['success'] else 'FAIL'}")

print("\n" + "=" * 40)
if errors:
    print(f"FAILED: {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED ✅")
    sys.exit(0)
