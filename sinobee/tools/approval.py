"""Dangerous-operation detection and content safety scanning."""
from __future__ import annotations

import re

DANGEROUS_SHELL = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|\*|\.\.?)(\s|$)", "recursive delete of a broad path"),
    (r"\brm\s+-[a-zA-Z]*r", "recursive delete"),
    (r"\bsudo\b|\bsu\s+-", "privilege escalation"),
    (r"\bmkfs\b|\bdd\s+if=|\bfdisk\b|\bparted\b|\bwipefs\b", "disk-level operation"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power control"),
    (r"(curl|wget)[^|;&]*\|\s*(ba|z|)sh\b", "pipe remote script into shell"),
    (r"\bchmod\s+-R\s+0?777\b|\bchown\s+-R\b", "recursive permission change"),
    (r">\s*/dev/sd[a-z]|>\s*/etc/", "write to system location"),
    (r"\bgit\s+push\b.*--force|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f", "destructive git operation"),
    (r"\b(kill|pkill|killall)\s+-9\b", "force kill processes"),
    (r"\bcrontab\s+-r\b", "remove crontab"),
    (r"(~|\$HOME)/\.ssh|id_rsa|\.aws/credentials|\.env\b", "touches credentials"),
    (r"\bnc\s+-[a-z]*l|\bncat\b.*-e\b|/dev/tcp/", "network shell"),
    (r"\b(pip|npm|apt(-get)?|brew)\s+(install|uninstall|remove)\b", "installs or removes software"),
]

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
                     r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_INJECTION = re.compile(r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the )?system prompt|"
                        r"you are now (in )?developer mode|忽略(之前|以上|先前)的?(所有)?指令)", re.I)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def shell_danger(command: str) -> str | None:
    for pat, reason in DANGEROUS_SHELL:
        if re.search(pat, command):
            return reason
    return None


def scan_content(text: str) -> list[str]:
    """Scan text that will be persisted (memory, skills) and later injected into prompts."""
    issues = []
    if _SECRET.search(text):
        issues.append("contains what looks like a secret/credential")
    if _INJECTION.search(text):
        issues.append("contains prompt-injection phrasing")
    if _INVISIBLE.search(text):
        issues.append("contains invisible unicode characters")
    if re.search(r"authorized_keys|ssh-rsa AAAA", text):
        issues.append("references SSH key installation")
    return issues


def scan_script(text: str) -> list[str]:
    issues = scan_content(text)
    for pat, reason in DANGEROUS_SHELL[:8]:
        if re.search(pat, text):
            issues.append(f"script contains dangerous pattern: {reason}")
    return issues
