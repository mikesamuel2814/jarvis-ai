#!/usr/bin/env python3
"""
Jarvis v3 Security Module Unit Tests
Run: python3 -m pytest security/tests/test_security_modules.py -v
"""

import os
import sys
import tempfile
import sqlite3
from pathlib import Path

# Ensure security modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from security.vault.vault import SecretVault, VaultError, VaultAccessDenied
from security.scope_enforcer import ScopeEnforcer, ScopeLevel, ScopeViolation
from security.port_monitor import PortMonitor
from security.sudo_auditor import SudoAuditor
from security.ids_hook import IDSHook
from security.key_rotator import KeyRotator


class TestSecretVault:
    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            vault = SecretVault(master_key=b"x" * 32)
            vault.set_secret("test_key", "test_value", tool_name="test_tool")
            assert vault.get_secret("test_key", tool_name="test_tool") == "test_value"

    def test_tool_allowlist_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            vault = SecretVault(master_key=b"x" * 32)
            vault.set_secret("api_key", "secret123", tool_allowlist=["allowed_tool"])
            assert vault.get_secret("api_key", tool_name="allowed_tool") == "secret123"
            try:
                vault.get_secret("api_key", tool_name="bad_tool")
                assert False, "Should have raised VaultAccessDenied"
            except VaultAccessDenied:
                pass

    def test_resolve_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            vault = SecretVault(master_key=b"x" * 32)
            vault.set_secret("db_pass", "hunter2")
            assert vault.resolve("${VAULT:db_pass}") == "hunter2"


class TestScopeEnforcer:
    def test_read_can_run_read(self):
        se = ScopeEnforcer(ScopeLevel.READ)
        assert se.check("cpu_info", ScopeLevel.READ) is True

    def test_read_cannot_run_privileged(self):
        se = ScopeEnforcer(ScopeLevel.READ)
        assert se.check("apt_upgrade", ScopeLevel.PRIVILEGED) is False

    def test_escalation(self):
        se = ScopeEnforcer(ScopeLevel.READ)
        se.escalate(ScopeLevel.LOCAL, "need to edit file")
        assert se.current_scope == ScopeLevel.LOCAL
        assert len(se.get_log()) == 1


class TestPortMonitor:
    def test_baseline_and_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            pm = PortMonitor()
            pm.learn_baseline()
            assert len(pm.baseline) >= 0
            anomalies = pm.check()
            assert isinstance(anomalies, list)


class TestSudoAuditor:
    def test_parse_line(self):
        auditor = SudoAuditor()
        parsed = auditor._parse_line("kali ALL=(ALL) NOPASSWD: /usr/bin/apt update")
        assert parsed is not None
        assert parsed["user"] == "kali"
        assert parsed["nopasswd"] is True
        assert parsed["cmd"] == "/usr/bin/apt update"

    def test_generate_policy(self):
        auditor = SudoAuditor()
        policy = auditor.generate_policy()
        assert "rules" in policy
        assert "high_risk_commands" in policy


class TestIDSHook:
    def test_check_ssh_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            ids = IDSHook()
            # Should run without error even if no real authorized_keys
            alerts = ids.check_ssh_keys()
            assert isinstance(alerts, list)

    def test_check_cron(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            ids = IDSHook()
            alerts = ids.check_cron()
            assert isinstance(alerts, list)


class TestKeyRotator:
    def test_register_and_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            kr = KeyRotator()
            kr.register("test_api", "api_key")
            due = kr.check_rotation_due()
            assert isinstance(due, list)
            # Should not be overdue immediately
            assert len(due) == 0

    def test_record_rotation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["JARVIS_HOME"] = tmpdir
            kr = KeyRotator()
            kr.register("my_key", "api_key")
            kr.record_rotation("my_key", old_hint="old", new_hint="new")
            due = kr.check_rotation_due()
            assert len(due) == 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
