"""Publishing-hygiene checks: fail if a tracked file leaks details of a private network or a secret.

This repository is public. Run: python3 -I -m unittest discover -s tests -v

What is flagged in every tracked text file:
  * private IPv4 addresses (10/8, 172.16/12, 192.168/16): use the documentation range 192.0.2.0/24
    (RFC 5737) or an mDNS name such as wled-1234.local in examples and tests;
  * MAC addresses that look vendor-assigned (the locally-administered bit is clear): use a made-up
    locally-administered one such as 02:00:00:00:00:01;
  * credentials and tokens (private keys, GitHub / AWS / Slack tokens);
  * absolute home-directory paths (/Users/name, /home/name, C:\\Users\\name);
  * files that should never be tracked (.env, .wled.secrets, platformio_override.ini, keys, flash images).

A line that is a deliberate, harmless exception can carry the marker `leak-ok` in a comment.
This file is exempt from the content scan because it has to spell the patterns out.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
MARKER = "leak-ok"

# WLED's own factory access-point address; public, documented, and not on anyone's LAN.
ALLOWED_IPS = {"192.168.4.1"}

PRIVATE_IP = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.]*\d)"
)
# 6 octets (EUI-48) or 8 octets (EUI-64), colon- or dash-separated.
MAC = re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}([:-])){5,7}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
SECRET = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]
HOME_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)(?!\.\.\.|<|\$|you\b|name\b|user\b|username\b|me\b|runner\b)[A-Za-z0-9._-]+")

FORBIDDEN_NAMES = (".env", ".wled.secrets", "platformio_override.ini")
FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".bin", ".elf", ".log")


def tracked_files():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.decode().split("\0") if p]


def read_text(path):
    data = (ROOT / path).read_bytes()
    if b"\0" in data[:4096]:
        return None  # binary
    return data.decode("utf-8", "replace")


def locally_administered(mac):
    return bool(int(re.split(r"[:-]", mac)[0], 16) & 0x02)


def findings():
    files = tracked_files()
    if files is None:
        raise unittest.SkipTest("not a git checkout")
    found = []
    for path in files:
        name = Path(path).name
        if name in FORBIDDEN_NAMES and name != "platformio_override.sample.ini":
            found.append((path, 0, "file must not be tracked", name))
        if path.endswith(FORBIDDEN_SUFFIXES):
            found.append((path, 0, "file type must not be tracked", name))
        if path == SELF or not (ROOT / path).is_file():
            continue
        text = read_text(path)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if MARKER in line:
                continue
            for m in PRIVATE_IP.finditer(line):
                if m.group(0) not in ALLOWED_IPS:
                    found.append((path, n, "private IP address", m.group(0)))
            for m in MAC.finditer(line):
                if not locally_administered(m.group(0)):
                    found.append((path, n, "vendor-assigned MAC address", m.group(0)))
            for label, rx in SECRET:
                if rx.search(line):
                    found.append((path, n, label, "(redacted)"))
            for m in HOME_PATH.finditer(line):
                found.append((path, n, "home directory path", m.group(0)))
    return found


class NoLeaks(unittest.TestCase):
    def test_tracked_files_are_clean(self):
        found = findings()
        report = "\n".join(f"  {p}:{n}: {what}: {val}" for p, n, what, val in found)
        self.assertEqual(
            found, [],
            "possible leak of private network details or secrets (use 192.0.2.x / 02:00:00:00:00:01 / "
            f"placeholders, or add `{MARKER}` to a deliberate exception):\n{report}",
        )


class Detectors(unittest.TestCase):
    """The patterns themselves, so a refactor cannot quietly stop them matching."""

    def test_private_ips(self):
        for s in ("10.0.0.5", "192.168.1.50", "172.16.0.1", "172.31.255.254"):
            self.assertTrue(PRIVATE_IP.search(s), s)
        for s in ("192.0.2.50", "172.32.0.1", "172.15.0.1", "8.8.8.8", "1.10.0.0.1", "v10.1.2.3.4"):
            self.assertFalse(PRIVATE_IP.search(s), s)

    def test_mac_vendor_vs_local(self):
        for s in ("98:88:e0:71:09:E4", "98:88:e0:ff:fe:71:09:E4", "A4-BB-CC-DD-EE-00"):
            m = MAC.search(s)
            self.assertTrue(m and not locally_administered(m.group(0)), s)
        for s in ("02:00:00:00:00:01", "02:00:00:ff:fe:00:00:01", "ff:ff:ff:ff:ff:ff"):
            m = MAC.search(s)
            self.assertTrue(m and locally_administered(m.group(0)), s)
        self.assertFalse(MAC.search("12:34:56"))  # a time-like value is not a MAC

    def test_secrets_and_paths(self):
        self.assertTrue(SECRET[0][1].search("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertTrue(SECRET[1][1].search("AKIA" + "A" * 16))
        self.assertTrue(SECRET[2][1].search("ghp_" + "a" * 36))
        self.assertTrue(HOME_PATH.search("/Users/someone/devel"))
        self.assertTrue(HOME_PATH.search("/home/someone/x"))
        self.assertFalse(HOME_PATH.search("/Users/you/devel"))
        self.assertFalse(HOME_PATH.search("/home/runner/work"))


if __name__ == "__main__":
    unittest.main()
