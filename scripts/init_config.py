#!/usr/bin/env python3
"""Create a private local configuration once; never overwrite an existing one."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bind", default="0.0.0.0", help="Listening IP; use a private LAN IP or 127.0.0.1 for a tunnel")
    args = parser.parse_args()
    import ipaddress
    try:
        address = ipaddress.ip_address(args.bind)
    except ValueError:
        parser.error("--bind must be an IPv4 address")
    if address.version != 4:
        parser.error("This setup script currently accepts IPv4 listening addresses")
    if not hasattr(os, "getuid"):
        parser.error("Run this script on the Linux server after copying the source there")
    if os.getuid() == 0:
        parser.error("Run as your normal Linux user, not with sudo, so the data directory stays user-owned")
    project = args.directory.expanduser().resolve()
    if not project.is_dir():
        parser.error("The target project directory does not exist")
    env_path = project / ".env"
    if env_path.exists() or env_path.is_symlink():
        parser.error(f"Refusing to overwrite {env_path}; existing configuration was kept")
    data = project / "data"
    if data.is_symlink() or (data.exists() and not data.is_dir()):
        parser.error("data must be a regular local directory, not a symlink or file")
    old_umask = os.umask(0o077)
    try:
        data.mkdir(mode=0o700, exist_ok=True)
        # Do not change permissions on an existing populated directory implicitly.
        if data.stat().st_uid != os.getuid():
            parser.error("data is owned by another user; select a fresh project directory")
        if data.stat().st_mode & 0o077:
            parser.error("data must be private; run chmod 700 data, then rerun this script")
        password = secrets.token_urlsafe(24)
        session_secret = secrets.token_urlsafe(48)
        values = {
            "GRADER_PASSWORD": password,
            "GRADER_SESSION_SECRET": session_secret,
            "GRADER_COOKIE_SECURE": "false",
            "GRADER_DATA_DIR": "./data",
            "GRADER_HOST": args.bind,
            "GRADER_PORT": "8765",
            "GRADER_MAX_UPLOAD_MB": "20",
            "GRADER_UID": str(os.getuid()),
            "GRADER_GID": str(os.getgid()),
            "OLLAMA_URL": "http://127.0.0.1:11434",
            "OLLAMA_MODEL": "qwen3-vl:4b-instruct-q4_K_M",
            "OLLAMA_NUM_CTX": "4096",
        }
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("# Private local configuration. Keep this file and backups on your equipment.\n")
            handle.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    finally:
        os.umask(old_umask)
    print(f"Created {env_path} (mode 600) and private data directory.")
    print(f"Teacher password: {password}")
    print("Save this password in your password manager. It is also in the private .env file.")
    print("Next: docker compose up -d --build")


if __name__ == "__main__":
    main()
