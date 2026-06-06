"""
Jarvis v3 Secret Vault
AES-256-GCM encrypted SQLite storage for secrets.
Replaces plaintext .env files and duplicate config storage.

Location: ~/.jarvis/security/vault/vault.py
Data: ~/.jarvis/vault/vault.db
Master Key: ~/.jarvis/vault/vault.key (chmod 600)
Audit Log: ~/.jarvis/vault/audit.log
"""

import os
import json
import sqlite3
import logging
import hashlib
import secrets
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
VAULT_DIR = JARVIS_HOME / "vault"
DB_PATH = VAULT_DIR / "vault.db"
KEY_PATH = VAULT_DIR / "vault.key"
AUDIT_PATH = VAULT_DIR / "audit.log"


class VaultError(Exception):
    """Base vault exception."""
    pass


class VaultAccessDenied(VaultError):
    """Tool or session lacks required scope for secret."""
    pass


class VaultCorrupted(VaultError):
    """Database integrity failure."""
    pass


class SecretVault:
    """
    Encrypted secret vault with tool-scoped ACL and audit trail.
    Secrets are referenced as ${VAULT:key_name} throughout Jarvis.
    """

    _instance: Optional["SecretVault"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, master_key: Optional[bytes] = None):
        if self._initialized:
            return
        self._initialized = True

        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_key(master_key)
        self._init_db()
        self._setup_audit()

    # ── Key Management ─────────────────────────────────────────────────────

    def _ensure_key(self, master_key: Optional[bytes] = None):
        if KEY_PATH.exists():
            raw = KEY_PATH.read_bytes()
            self._master_key = base64.b64decode(raw)
        elif master_key:
            self._master_key = master_key
            KEY_PATH.write_bytes(base64.b64encode(master_key))
            KEY_PATH.chmod(0o600)
        else:
            self._master_key = AESGCM.generate_key(bit_length=256)
            KEY_PATH.write_bytes(base64.b64encode(self._master_key))
            KEY_PATH.chmod(0o600)
            logging.warning("[Vault] Generated new master key. Back up %s securely.", KEY_PATH)

    def _derive_key(self, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480_000,
        )
        return kdf.derive(self._master_key)

    # ── Database ───────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    key_name TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    salt BLOB NOT NULL,
                    nonce BLOB NOT NULL,
                    scope TEXT DEFAULT 'LOCAL',
                    tool_allowlist TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    rotation_due TEXT,
                    checksum TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    action TEXT NOT NULL,
                    key_name TEXT,
                    tool_name TEXT,
                    session_id TEXT,
                    success INTEGER NOT NULL,
                    details TEXT
                )
            """)
            conn.commit()

    def _setup_audit(self):
        handler = logging.FileHandler(AUDIT_PATH)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        self._audit_log = logging.getLogger("vault_audit")
        self._audit_log.setLevel(logging.INFO)
        self._audit_log.addHandler(handler)

    # ── Encryption ─────────────────────────────────────────────────────────

    def _encrypt(self, plaintext: str) -> Dict[str, bytes]:
        salt = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        key = self._derive_key(salt)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return {"ciphertext": ciphertext, "salt": salt, "nonce": nonce}

    def _decrypt(self, ciphertext: bytes, salt: bytes, nonce: bytes) -> str:
        key = self._derive_key(salt)
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    def _checksum(self, data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    # ── Public API ─────────────────────────────────────────────────────────

    def set_secret(
        self,
        key_name: str,
        value: str,
        scope: str = "LOCAL",
        tool_allowlist: Optional[List[str]] = None,
        rotation_days: Optional[int] = None,
        tool_name: str = "vault_cli",
        session_id: str = "local",
    ) -> bool:
        """Store or update a secret."""
        try:
            enc = self._encrypt(value)
            due = None
            if rotation_days:
                due = datetime.now(timezone.utc).isoformat()
            allow = json.dumps(tool_allowlist or [])
            chk = self._checksum(value)

            with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
                conn.execute(
                    """INSERT INTO secrets
                        (key_name, ciphertext, salt, nonce, scope, tool_allowlist,
                         created_at, updated_at, rotation_due, checksum)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(key_name) DO UPDATE SET
                         ciphertext=excluded.ciphertext,
                         salt=excluded.salt,
                         nonce=excluded.nonce,
                         scope=excluded.scope,
                         tool_allowlist=excluded.tool_allowlist,
                         updated_at=excluded.updated_at,
                         rotation_due=excluded.rotation_due,
                         checksum=excluded.checksum""",
                    (
                        key_name,
                        enc["ciphertext"],
                        enc["salt"],
                        enc["nonce"],
                        scope,
                        allow,
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                        due,
                        chk,
                    ),
                )
                conn.commit()
            self._audit("SET", key_name, tool_name, session_id, True)
            return True
        except Exception as exc:
            self._audit("SET", key_name, tool_name, session_id, False, str(exc))
            raise VaultError(f"Failed to set secret {key_name}: {exc}") from exc

    def get_secret(
        self,
        key_name: str,
        tool_name: str = "unknown",
        session_id: str = "local",
    ) -> str:
        """Retrieve a secret if the tool/session is authorized."""
        try:
            with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
                row = conn.execute(
                    "SELECT ciphertext, salt, nonce, scope, tool_allowlist, checksum FROM secrets WHERE key_name=?",
                    (key_name,),
                ).fetchone()

            if not row:
                self._audit("GET", key_name, tool_name, session_id, False, "not_found")
                raise VaultError(f"Secret '{key_name}' not found in vault.")

            ciphertext, salt, nonce, scope, allow_json, checksum = row
            allowlist = json.loads(allow_json)

            # ACL check
            if allowlist and tool_name not in allowlist:
                self._audit("GET", key_name, tool_name, session_id, False, "access_denied")
                raise VaultAccessDenied(
                    f"Tool '{tool_name}' is not in allowlist for '{key_name}'."
                )

            plaintext = self._decrypt(ciphertext, salt, nonce)
            if self._checksum(plaintext) != checksum:
                self._audit("GET", key_name, tool_name, session_id, False, "checksum_fail")
                raise VaultCorrupted(f"Secret '{key_name}' failed integrity check.")

            self._audit("GET", key_name, tool_name, session_id, True)
            return plaintext

        except (VaultError, VaultAccessDenied, VaultCorrupted):
            raise
        except Exception as exc:
            self._audit("GET", key_name, tool_name, session_id, False, str(exc))
            raise VaultError(f"Failed to get secret {key_name}: {exc}") from exc

    def delete_secret(
        self,
        key_name: str,
        tool_name: str = "vault_cli",
        session_id: str = "local",
    ) -> bool:
        try:
            with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
                cur = conn.execute("DELETE FROM secrets WHERE key_name=?", (key_name,))
                conn.commit()
            success = cur.rowcount > 0
            self._audit("DELETE", key_name, tool_name, session_id, success)
            return success
        except Exception as exc:
            self._audit("DELETE", key_name, tool_name, session_id, False, str(exc))
            raise VaultError(f"Failed to delete secret {key_name}: {exc}") from exc

    def list_secrets(self) -> List[str]:
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute("SELECT key_name FROM secrets ORDER BY key_name").fetchall()
        return [r[0] for r in rows]

    def resolve(self, token: str, tool_name: str = "unknown", session_id: str = "local") -> str:
        """Resolve ${VAULT:key_name} tokens."""
        if token.startswith("${VAULT:") and token.endswith("}"):
            key_name = token[8:-1]
            return self.get_secret(key_name, tool_name, session_id)
        return token

    def rotate_needed(self) -> List[Dict[str, Any]]:
        """Return secrets whose rotation_due has passed."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT key_name, rotation_due FROM secrets WHERE rotation_due IS NOT NULL AND rotation_due <= ?",
                (now,),
            ).fetchall()
        return [{"key_name": r[0], "rotation_due": r[1]} for r in rows]

    def _audit(self, action: str, key_name: str, tool_name: str, session_id: str, success: bool, details: str = ""):
        status = "OK" if success else "FAIL"
        self._audit_log.info(
            "[%s] action=%s key=%s tool=%s session=%s %s",
            status, action, key_name, tool_name, session_id, details
        )
        try:
            with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
                conn.execute(
                    "INSERT INTO audit (ts, action, key_name, tool_name, session_id, success, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        action,
                        key_name,
                        tool_name,
                        session_id,
                        1 if success else 0,
                        details,
                    ),
                )
                conn.commit()
        except Exception:
            pass  # best-effort

    def migrate_from_env(self, env_path, tool_allowlist: Optional[List[str]] = None):
        """Bulk-import KEY=VALUE pairs from a .env file into the vault."""
        env_path = Path(env_path)
        if not env_path.exists():
            return
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                self.set_secret(key.strip(), val.strip(), tool_allowlist=tool_allowlist or [])


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis Secret Vault CLI")
    parser.add_argument("action", choices=["set", "get", "delete", "list", "rotate-check", "migrate"])
    parser.add_argument("--key", "-k")
    parser.add_argument("--value", "-v")
    parser.add_argument("--scope", default="LOCAL")
    parser.add_argument("--tools", default="", help="Comma-separated allowlist")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()

    vault = SecretVault()

    if args.action == "set":
        tools = [t.strip() for t in args.tools.split(",") if t.strip()] or None
        vault.set_secret(args.key, args.value, scope=args.scope, tool_allowlist=tools)
        print(f"[Vault] Secret '{args.key}' stored.")
    elif args.action == "get":
        print(vault.get_secret(args.key))
    elif args.action == "delete":
        vault.delete_secret(args.key)
        print(f"[Vault] Secret '{args.key}' deleted.")
    elif args.action == "list":
        for k in vault.list_secrets():
            print(k)
    elif args.action == "rotate-check":
        for entry in vault.rotate_needed():
            print(f"ROTATION_DUE: {entry['key_name']} (due {entry['rotation_due']})")
    elif args.action == "migrate":
        if not args.env_file:
            raise SystemExit("--env-file required for migrate")
        vault.migrate_from_env(args.env_file)
        print(f"[Vault] Migrated secrets from {args.env_file}")
