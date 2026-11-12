#!/usr/bin/env python3
"""Provision a flashed WLED board: WiFi, names and LED strips, written straight to its filesystem.

Generates WLED's own config files (cfg.json, wsec.json, presets.json), packs them into a LittleFS image,
flashes just the filesystem partition over USB and then checks the device over the network. No access-point
hop, no firmware rebuild. Re-run it any time to change the setup (it replaces the whole configuration,
including saved presets).

  board_setup/provision.py --name "Desk" --mdns desk \\
      --strip type=ws2812,order=grb,pin=7,len=50

  # two strips on one board; --dry-run shows what would be written, --out DIR also keeps the files
  board_setup/provision.py --name Shelf --mdns shelf --dry-run \\
      --strip pin=7,len=50 --strip type=sk6812,order=grb,pin=6,len=30

Secrets come from a KEY=VALUE file (default: .wled.secrets in the repository root; see
.wled.secrets.example). They are never taken from the command line, never printed and only touch disk in a
temporary directory that is deleted afterwards (plus the device's own flash, where WLED keeps them anyway).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SECRETS = REPO / ".wled.secrets"
DEFAULT_WLED_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "wled-ls-formula" / "WLED"
DEFAULT_ENV = "lsf_esp32c6_4MB"

# Digital LED chips: name -> WLED bus type id (wled00/const.h). Add rows here to support more chips.
LED_TYPES = {
    "ws2812": 22,    # WS2812/WS2812B/WS2813/WS2815 and clones: three colour channels
    "ws2813": 22,
    "ws2811": 22,
    "ws2811_400": 24,  # old half-speed WS2811
    "apa106": 27,
    "sk6812": 30,    # RGBW
}
# WLED colour-order ids (const.h COL_ORDER_*).
COLOR_ORDERS = {"grb": 0, "rgb": 1, "brg": 2, "rbg": 3, "bgr": 4, "gbr": 5}

# Per-board limits. GPIO numbers are those printed on the board.
BOARDS = {
    "esp32c6": {
        "gpio_max": 23,                 # GPIO24..30 are the in-package flash on the C6FH4 and not usable
        "reserved": {12: "USB D-", 13: "USB D+"},  # using these kills the USB serial/flash connection
        "strapping": {4, 5, 8, 9, 15},  # sampled at reset: usable, but can interfere with booting
        "max_strips": 8,
        "max_leds": 4096,               # MAX_LEDS for C3/C5/C6 in WLED's const.h
    },
}


class ProvisionError(Exception):
    """A problem with the user's input or environment, reported without a traceback."""


@dataclass(frozen=True)
class Strip:
    type_name: str
    order: str
    pin: int
    length: int
    reversed: bool = False

    @property
    def type_id(self) -> int:
        return LED_TYPES[self.type_name]


# ---------------------------------------------------------------------------------------------------------
# Input parsing and validation (pure functions; covered by tests/test_provision.py)
# ---------------------------------------------------------------------------------------------------------

def parse_strip(spec: str, board: dict) -> Strip:
    """Parse 'type=ws2812,order=grb,pin=7,len=50'. type and order default to ws2812 and grb."""
    fields: dict[str, str] = {}
    for part in spec.split(","):
        key, sep, value = part.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if not sep or not key or not value:
            raise ProvisionError(f"--strip {spec!r}: expected key=value pairs, got {part!r}")
        if key in fields:
            raise ProvisionError(f"--strip {spec!r}: {key!r} given twice")
        fields[key] = value
    allowed = {"type", "order", "pin", "len", "reversed"}
    unknown = set(fields) - allowed
    if unknown:
        raise ProvisionError(f"--strip {spec!r}: unknown key(s) {sorted(unknown)}; use {sorted(allowed)}")
    for required in ("pin", "len"):
        if required not in fields:
            raise ProvisionError(f"--strip {spec!r}: missing {required}=")

    type_name = fields.get("type", "ws2812")
    if type_name not in LED_TYPES:
        raise ProvisionError(f"--strip {spec!r}: unknown type {type_name!r}; known: {', '.join(LED_TYPES)}")
    order = fields.get("order", "grb")
    if order not in COLOR_ORDERS:
        raise ProvisionError(f"--strip {spec!r}: unknown order {order!r}; known: {', '.join(COLOR_ORDERS)}")
    try:
        pin, length = int(fields["pin"]), int(fields["len"])
    except ValueError:
        raise ProvisionError(f"--strip {spec!r}: pin and len must be whole numbers") from None
    if length < 1:
        raise ProvisionError(f"--strip {spec!r}: len must be at least 1")
    if not 0 <= pin <= board["gpio_max"]:
        raise ProvisionError(f"--strip {spec!r}: pin {pin} is outside GPIO 0..{board['gpio_max']} on this board")
    if pin in board["reserved"]:
        raise ProvisionError(f"--strip {spec!r}: GPIO {pin} is the {board['reserved'][pin]} line of the USB port")
    reversed_ = fields.get("reversed", "no") in ("1", "yes", "true", "on")
    return Strip(type_name, order, pin, length, reversed_)


def validate_strips(strips: list[Strip], board: dict) -> list[str]:
    """Raise on impossible setups; return warnings for risky ones."""
    if not strips:
        raise ProvisionError("at least one --strip is required")
    if len(strips) > board["max_strips"]:
        raise ProvisionError(f"{len(strips)} strips requested, this board supports at most {board['max_strips']}")
    pins = [s.pin for s in strips]
    dupes = sorted({p for p in pins if pins.count(p) > 1})
    if dupes:
        raise ProvisionError(f"GPIO {dupes} used by more than one strip (one data pin drives one strip)")
    total = sum(s.length for s in strips)
    if total > board["max_leds"]:
        raise ProvisionError(f"{total} LEDs in total exceeds the limit of {board['max_leds']} for this board")
    return [f"GPIO {s.pin} is a strapping pin: it is read at reset and can stop the board booting if the strip "
            f"holds it at the wrong level" for s in strips if s.pin in board["strapping"]]


SECRET_KEYS = {"WIFI_SSID", "WIFI_PASSWORD", "AP_PASSWORD"}


def parse_secrets(text: str) -> dict[str, str]:
    """KEY=VALUE lines; '#' starts a comment line; matching single or double quotes around a value are removed."""
    out: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            raise ProvisionError(f"secrets file line {n}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in SECRET_KEYS:
            raise ProvisionError(f"secrets file line {n}: unknown key {key!r}; known: {', '.join(sorted(SECRET_KEYS))}")
        out[key] = value
    return out


def load_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ProvisionError(f"{path} not found. Create it from the template:\n"
                             f"    cp {REPO / '.wled.secrets.example'} {path}   # then edit it\n"
                             f"or pass --no-wifi to skip the home WiFi.")
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"warning: {path} is readable by other users; run: chmod 600 {path}", file=sys.stderr)
    return parse_secrets(path.read_text(encoding="utf-8"))


def _check_wpa(label: str, value: str, allow_empty: bool) -> None:
    n = len(value.encode("utf-8"))
    if n == 0 and allow_empty:
        return
    if not 8 <= n <= 63:
        raise ProvisionError(f"{label} must be 8 to 63 characters (WPA2){' or empty for an open network' if allow_empty else ''}")


def check_names(name: str, mdns: str, ap_ssid: str | None) -> None:
    if not 1 <= len(name.encode("utf-8")) <= 32:
        raise ProvisionError("--name must be 1 to 32 bytes")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", mdns):
        raise ProvisionError("--mdns must be 1-32 lowercase letters, digits or hyphens, not starting or ending with a hyphen "
                             "(the device is then reachable as <mdns>.local)")
    if ap_ssid is not None and not 1 <= len(ap_ssid.encode("utf-8")) <= 32:
        raise ProvisionError("--ap-ssid must be 1 to 32 bytes")


def build_files(*, name: str, mdns: str, ap_ssid: str | None, secrets: dict[str, str], use_wifi: bool,
                strips: list[Strip]) -> dict[str, dict]:
    """The three WLED files as plain dicts. Passwords are only in wsec.json, as WLED itself stores them."""
    if use_wifi:
        for key in ("WIFI_SSID", "WIFI_PASSWORD"):
            if key not in secrets:
                raise ProvisionError(f"{key} is missing from the secrets file (or pass --no-wifi)")
        ssid = secrets["WIFI_SSID"]
        if not 1 <= len(ssid.encode("utf-8")) <= 32:
            raise ProvisionError("WIFI_SSID must be 1 to 32 bytes")
        _check_wpa("WIFI_PASSWORD", secrets["WIFI_PASSWORD"], allow_empty=True)
    if "AP_PASSWORD" in secrets:
        _check_wpa("AP_PASSWORD", secrets["AP_PASSWORD"], allow_empty=False)

    cfg: dict = {"id": {"mdns": mdns, "name": name}}
    wsec: dict = {}
    if use_wifi:
        cfg["nw"] = {"ins": [{"ssid": secrets["WIFI_SSID"]}]}
        wsec["nw"] = {"ins": [{"psk": secrets["WIFI_PASSWORD"]}]}
    ap: dict = {}
    if ap_ssid is not None:
        ap["ssid"] = ap_ssid
    if ap:
        cfg["ap"] = ap
    if "AP_PASSWORD" in secrets:
        wsec["ap"] = {"psk": secrets["AP_PASSWORD"]}

    start, ins, segs = 0, [], []
    for i, s in enumerate(strips):
        ins.append({"start": start, "len": s.length, "pin": [s.pin], "order": COLOR_ORDERS[s.order],
                    "rev": s.reversed, "skip": 0, "type": s.type_id, "ref": False})
        segs.append({"id": i, "start": start, "stop": start + s.length, "n": f"Strip {i + 1} (GPIO {s.pin})",
                     "on": True})
        start += s.length
    cfg["hw"] = {"led": {"total": start, "ins": ins}}
    # Boot into preset 1, which has one segment per strip so each can be controlled on its own.
    cfg["def"] = {"ps": 1, "on": True, "bri": 128}
    presets = {"0": {}, "1": {"n": "Boot", "on": True, "bri": 128, "seg": segs}}
    return {"cfg.json": cfg, "wsec.json": wsec, "presets.json": presets}


def redact(files: dict[str, dict]) -> str:
    """Pretty-print the files for humans with every password hidden."""
    def scrub(o):
        if isinstance(o, dict):
            return {k: ("********" if k == "psk" else scrub(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        return o
    return "\n".join(f"--- {n}\n{json.dumps(scrub(d), indent=2)}" for n, d in files.items())


# ---------------------------------------------------------------------------------------------------------
# Talking to the hardware
# ---------------------------------------------------------------------------------------------------------

def pio_home() -> Path:
    return Path(os.environ.get("PLATFORMIO_CORE_DIR") or Path.home() / ".platformio")


def find_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyACM*"))
    if len(found) != 1:
        raise ProvisionError(f"expected exactly one serial port, found {len(found)}: {found or 'none'}. Use --port.")
    return found[0]


def fs_partition(wled_dir: Path, env: str) -> tuple[int, int]:
    """(offset, size) of the LittleFS partition the firmware for this PlatformIO env was built with."""
    if not (wled_dir / "platformio.ini").is_file():
        raise ProvisionError(f"no WLED checkout at {wled_dir}; run board_setup/c6-install.sh build first")
    r = subprocess.run(["pio", "project", "config", "--json-output"], cwd=wled_dir, capture_output=True, text=True)
    if r.returncode != 0:
        raise ProvisionError(f"pio project config failed:\n{r.stderr.strip()}")
    sections = dict((name, dict(items)) for name, items in json.loads(r.stdout))
    cfg = sections.get(f"env:{env}")
    if cfg is None:
        raise ProvisionError(f"environment {env!r} not found in {wled_dir}/platformio_override.ini")
    csv = wled_dir / cfg["board_build.partitions"]
    for line in csv.read_text().splitlines():
        cols = [c.strip() for c in line.split("#")[0].split(",")]
        if len(cols) >= 5 and cols[1] == "data" and cols[2] == "spiffs":
            return int(cols[3], 0), int(cols[4], 0)
    raise ProvisionError(f"no spiffs/LittleFS partition in {csv}")


def run_tool(cmd: list[str], what: str) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ProvisionError(f"{what} failed (exit {r.returncode}):\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    return r.stdout


def esptool_cmd(chip: str, port: str) -> list[str]:
    esptool = pio_home() / "packages" / "tool-esptoolpy" / "esptool.py"
    py = pio_home() / "penv" / "bin" / "python"
    if not (esptool.is_file() and py.is_file()):
        raise ProvisionError("PlatformIO's esptool not found; build the firmware once with "
                             "board_setup/c6-install.sh build so PlatformIO installs it")
    return [str(py), str(esptool), "--chip", chip, "--port", port]


def parse_mac(esptool_output: str) -> str | None:
    """The chip's base MAC (the one WLED reports) as 12 lowercase hex digits, from esptool's connect banner."""
    m = re.search(r"BASE MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", esptool_output) or \
        re.search(r"^MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s*$", esptool_output, re.M)
    return m.group(1).replace(":", "").lower() if m else None


ESP_IMAGE_MAGIC = 0xE9  # first byte of every ESP bootloader / application image; erased flash reads 0xFF


def has_image(first_bytes: bytes) -> bool:
    return len(first_bytes) > 0 and first_bytes[0] == ESP_IMAGE_MAGIC


def probe_board(port: str, chip: str) -> str:
    """Identify the chip and require firmware on it; return its MAC. Read-only (it does reset the chip)."""
    base = esptool_cmd(chip, port)
    mac, missing = None, []
    with tempfile.TemporaryDirectory(prefix="wled-probe-") as tmp:
        for what, addr in (("bootloader", 0x0), ("WLED application", 0x10000)):
            out = Path(tmp) / f"{addr:x}.bin"
            banner = run_tool(base + ["read_flash", f"0x{addr:x}", "4", str(out)], f"esptool read_flash 0x{addr:x}")
            mac = mac or parse_mac(banner)
            if not has_image(out.read_bytes()):
                missing.append(what)
    if mac is None:
        raise ProvisionError("could not read the chip's MAC address from esptool")
    shown = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    if missing:
        raise ProvisionError(f"the board on {port} (MAC {shown}) has no {' and no '.join(missing)}, so it would not run the "
                             f"configuration. Install the firmware first: board_setup/c6-install.sh flash "
                             f"(or use `all` instead of `provision`).")
    print(f"board: ESP32 MAC {shown} on {port}, firmware present")
    return mac


def build_and_flash(files: dict[str, dict], offset: int, size: int, port: str, chip: str) -> None:
    mklittlefs = next(iter(sorted(glob.glob(str(pio_home() / "packages" / "tool-mklittlefs*" / "mklittlefs")))), None)
    if not mklittlefs:
        raise ProvisionError("PlatformIO's mklittlefs not found; build the firmware once with "
                             "board_setup/c6-install.sh build so PlatformIO installs it")
    # TemporaryDirectory is mode 0700 and removed on exit, so the passwords in wsec.json do not linger.
    with tempfile.TemporaryDirectory(prefix="wled-provision-") as tmp:
        root = Path(tmp) / "fs"
        root.mkdir()
        for fname, data in files.items():
            if data:
                (root / fname).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        image = Path(tmp) / "littlefs.bin"
        run_tool([mklittlefs, "-c", str(root), "-b", "4096", "-p", "256", "-s", str(size), str(image)], "mklittlefs")
        print(f"writing {image.stat().st_size // 1024} KiB filesystem image to {port} at 0x{offset:x} ...")
        run_tool(esptool_cmd(chip, port) + ["write_flash", f"0x{offset:x}", str(image)], "esptool write_flash")


def http_json(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (plain http to a LAN device)
        return json.load(r)


def verify(mdns: str, name: str, strips: list[Strip], wait: float, mac: str | None = None) -> None:
    """Wait for <mdns>.local to answer and compare what the device reports with what was requested."""
    base = f"http://{mdns}.local"
    deadline, last = time.time() + wait, None
    print(f"waiting up to {int(wait)} s for {base} ...")
    while time.time() < deadline:
        try:
            info = http_json(f"{base}/json/info")
            cfg = http_json(f"{base}/json/cfg")
            break
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            time.sleep(3)
    else:
        raise ProvisionError(f"{base} did not answer within {int(wait)} s ({last}). The files were written; check "
                             f"that the board joined your WiFi (router client list, or the serial console), or "
                             f"rerun with --no-verify.")
    problems = []
    if mac and info.get("mac", "").lower() != mac:
        problems.append(f"answering device has MAC {info.get('mac')}, but the board just written is {mac}: another WLED "
                        f"device is using the name or address {mdns}.local")
    if info.get("name") != name:
        problems.append(f"name is {info.get('name')!r}, expected {name!r}")
    total = sum(s.length for s in strips)
    if info.get("leds", {}).get("count") != total:
        problems.append(f"LED count is {info.get('leds', {}).get('count')}, expected {total}")
    got = [(i.get("pin"), i.get("len"), i.get("type"), i.get("order")) for i in cfg.get("hw", {}).get("led", {}).get("ins", [])]
    want = [([s.pin], s.length, s.type_id, COLOR_ORDERS[s.order]) for s in strips]
    if got != want:
        problems.append(f"strips are {got}, expected {want}")
    if problems:
        raise ProvisionError("device is up but does not match the request:\n  " + "\n  ".join(problems))
    print(f"verified: {info.get('name')!r} at {info.get('ip')} ({base}), WLED {info.get('ver')}, "
          f"{total} LEDs on {len(strips)} strip(s)")


# ---------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True, help="WLED server name shown in the UI and apps (1-32 chars)")
    p.add_argument("--mdns", required=True, help="local DNS name: the device answers at <mdns>.local")
    p.add_argument("--strip", action="append", default=[], metavar="SPEC",
                   help="an LED strip: pin=N,len=N[,type=ws2812][,order=grb][,reversed=yes]; repeat for more strips. "
                        f"types: {', '.join(LED_TYPES)}; orders: {', '.join(COLOR_ORDERS)}")
    p.add_argument("--ap-ssid", help="the device's own access-point name (WLED default: WLED-AP)")
    p.add_argument("--secrets", type=Path, default=DEFAULT_SECRETS, help=f"secrets file (default {DEFAULT_SECRETS})")
    p.add_argument("--no-wifi", action="store_true", help="do not configure the home WiFi (access point only)")
    p.add_argument("--board", choices=sorted(BOARDS), default="esp32c6")
    p.add_argument("--port", help="serial port (default: the only /dev/cu.usbmodem* or /dev/ttyACM*)")
    p.add_argument("--env", default=os.environ.get("ENV_NAME", DEFAULT_ENV), help="PlatformIO env the firmware was built with")
    p.add_argument("--wled-dir", type=Path, default=Path(os.environ.get("WLED_DIR", DEFAULT_WLED_DIR)))
    p.add_argument("--dry-run", action="store_true", help="validate and show the result; touch nothing")
    p.add_argument("--out", type=Path, help="also write the (secret-bearing) files to this directory; implies --dry-run")
    p.add_argument("--no-verify", action="store_true", help="do not wait for the device on the network")
    p.add_argument("--verify-wait", type=float, default=90, help="seconds to wait for the device (default 90)")
    a = p.parse_args(argv)

    try:
        board = BOARDS[a.board]
        strips = [parse_strip(s, board) for s in a.strip]
        warnings = validate_strips(strips, board)
        check_names(a.name, a.mdns, a.ap_ssid)
        secrets = {} if a.no_wifi and not a.secrets.is_file() else load_secrets(a.secrets)
        files = build_files(name=a.name, mdns=a.mdns, ap_ssid=a.ap_ssid, secrets=secrets,
                            use_wifi=not a.no_wifi, strips=strips)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        if "AP_PASSWORD" not in secrets:
            print("warning: AP_PASSWORD not set; the access point keeps WLED's default password (wled1234)", file=sys.stderr)

        print(redact(files))
        if a.dry_run or a.out:
            if a.out:
                a.out.mkdir(parents=True, exist_ok=True)
                for fname, data in files.items():
                    path = a.out / fname
                    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    path.chmod(0o600)
                print(f"wrote {', '.join(files)} to {a.out} (contains passwords)")
            return 0

        offset, size = fs_partition(a.wled_dir, a.env)
        port = find_port(a.port)
        chip = "esp32c6" if a.board == "esp32c6" else a.board
        mac = probe_board(port, chip)
        build_and_flash(files, offset, size, port, chip)
        if a.no_verify or a.no_wifi:
            print("done (not verified over the network).")
        else:
            verify(a.mdns, a.name, strips, a.verify_wait, mac)
        return 0
    except ProvisionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
