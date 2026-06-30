"""
igpsport_credentials.py

Single source of truth for iGPSPORT credentials, shared by the CLI and the MCP
server.

Credentials are read from the OS keychain (macOS Keychain, Windows Credential
Manager, Linux Secret Service) via the `keyring` library, so they never live in
plaintext inside `claude_desktop_config.json` or any project file.

Environment variables `IGPSPORT_USER` / `IGPSPORT_PASSWORD` remain a fallback
for quick local development; the keychain takes precedence when present.

One-time setup:

    uv run python igpsport_credentials.py set      # prompts and stores
    uv run python igpsport_credentials.py status   # shows whether creds exist
    uv run python igpsport_credentials.py delete    # removes stored creds
"""

from __future__ import annotations

import getpass
import os
import sys

import keyring

# Keychain service name and the logical key under which the username is stored.
SERVICE_NAME = "igpsport_tool"
# The username field of the password entry holds the iGPSPORT login user; we
# also store the login user under a fixed pointer key so we can look it up.
_USER_POINTER_KEY = "__igpsport_user__"


class CredentialsError(Exception):
    """No usable iGPSPORT credentials were found."""


def get_credentials() -> tuple[str, str]:
    """Return (user, password), preferring the OS keychain over env vars.

    Raises CredentialsError if neither source has both values.
    """
    user = keyring.get_password(SERVICE_NAME, _USER_POINTER_KEY)
    password = keyring.get_password(SERVICE_NAME, user) if user else None

    if not user or not password:
        env_user = os.environ.get("IGPSPORT_USER")
        env_password = os.environ.get("IGPSPORT_PASSWORD")
        if env_user and env_password:
            return env_user, env_password
        raise CredentialsError(
            "No iGPSPORT credentials found. Run "
            "`uv run python igpsport_credentials.py set` to store them in the "
            "OS keychain, or set IGPSPORT_USER / IGPSPORT_PASSWORD."
        )

    return user, password


def set_credentials(user: str, password: str) -> None:
    """Store credentials in the OS keychain."""
    keyring.set_password(SERVICE_NAME, _USER_POINTER_KEY, user)
    keyring.set_password(SERVICE_NAME, user, password)


def delete_credentials() -> None:
    """Remove stored credentials from the OS keychain (idempotent)."""
    user = keyring.get_password(SERVICE_NAME, _USER_POINTER_KEY)
    if user:
        try:
            keyring.delete_password(SERVICE_NAME, user)
        except keyring.errors.PasswordDeleteError:
            pass
    try:
        keyring.delete_password(SERVICE_NAME, _USER_POINTER_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


def _cmd_set() -> None:
    user = input("iGPSPORT user (email): ").strip()
    if not user:
        print("Aborted: empty user.", file=sys.stderr)
        sys.exit(1)
    password = getpass.getpass("iGPSPORT password: ")
    if not password:
        print("Aborted: empty password.", file=sys.stderr)
        sys.exit(1)
    set_credentials(user, password)
    print(f"Stored credentials for {user} in the OS keychain (service '{SERVICE_NAME}').")


def _cmd_status() -> None:
    user = keyring.get_password(SERVICE_NAME, _USER_POINTER_KEY)
    has_pw = bool(user and keyring.get_password(SERVICE_NAME, user))
    if user and has_pw:
        print(f"Keychain: credentials present for {user}.")
    else:
        print("Keychain: no credentials stored.")
    env_ok = bool(os.environ.get("IGPSPORT_USER") and os.environ.get("IGPSPORT_PASSWORD"))
    print(f"Env vars: {'present' if env_ok else 'not set'}.")


def _cmd_delete() -> None:
    delete_credentials()
    print("Removed any stored iGPSPORT credentials from the OS keychain.")


def main() -> None:
    commands = {"set": _cmd_set, "status": _cmd_status, "delete": _cmd_delete}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(f"Usage: python igpsport_credentials.py [{' | '.join(commands)}]", file=sys.stderr)
        sys.exit(2)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
