"""Secret Vault — SQLCipher-style encrypted secret storage."""

from .vault import SecretVault, VaultError, VaultAccessDenied

__all__ = ["SecretVault", "VaultError", "VaultAccessDenied"]
