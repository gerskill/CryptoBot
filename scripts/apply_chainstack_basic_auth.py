#!/usr/bin/env python3
"""Applique CHAINSTACK_USERNAME/PASSWORD depuis .tmp_secrets sans rien afficher.
Usage: /usr/local/bin/python3 scripts/apply_chainstack_basic_auth.py
Attend user_len=18 pass_len=37 par défaut (meta).
"""
from __future__ import annotations
import os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROP = ROOT / ".tmp_secrets"
ENV = ROOT / ".env"
USER_F = DROP / "chainstack_basic_user.txt"
PASS_F = DROP / "chainstack_basic_pass.txt"
META_F = DROP / "chainstack_basic.meta"

def main() -> int:
    if not USER_F.is_file() or not PASS_F.is_file():
        print("NEED_BASIC_AUTH_ON_MAC: fichiers absents dans .tmp_secrets/")
        return 2
    user = USER_F.read_text().strip()
    pw = PASS_F.read_text().strip()
    expect_u, expect_p = 18, 37
    if META_F.is_file():
        meta = META_F.read_text()
        m = re.search(r"username_len=(\d+)", meta)
        n = re.search(r"password_len=(\d+)", meta)
        if m: expect_u = int(m.group(1))
        if n: expect_p = int(n.group(1))
    if len(user) != expect_u or len(pw) != expect_p:
        print(f"NEED_BASIC_AUTH_ON_MAC: lens mismatch user={len(user)}/{expect_u} pass={len(pw)}/{expect_p}")
        return 3
    text = ENV.read_text() if ENV.exists() else ""
    def upsert(text: str, key: str, val: str) -> str:
        line = f"{key}={val}"
        pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        if pat.search(text):
            return pat.sub(line, text, count=1)
        if text and not text.endswith("\n"):
            text += "\n"
        return text + line + "\n"
    text = upsert(text, "CHAINSTACK_USERNAME", user)
    text = upsert(text, "CHAINSTACK_PASSWORD", pw)
    # Keep path-token keys but basic auth takes priority in code.
    ENV.write_text(text)
    os.chmod(ENV, 0o600)
    # wipe drop files
    for f in (USER_F, PASS_F, META_F):
        try:
            f.write_text("")
            f.unlink(missing_ok=True)
        except Exception:
            pass
    print(f"OK applied basic auth user_len={len(user)} pass_len={len(pw)} (values not shown)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
