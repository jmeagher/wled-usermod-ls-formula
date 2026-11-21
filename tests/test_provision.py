"""board_setup/provision.py: input validation and the WLED config files it generates (no hardware needed).

Run: python3 -I -m unittest discover -s tests
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_spec = importlib.util.spec_from_file_location("provision", os.path.join(ROOT, "board_setup", "provision.py"))
P = importlib.util.module_from_spec(_spec)
sys.modules["provision"] = P  # dataclasses (py3.12+) looks the module up in sys.modules
_spec.loader.exec_module(P)

BOARD = P.BOARDS["esp32c6"]
SECRETS = {"WIFI_SSID": "home", "WIFI_PASSWORD": "hunter2hunter2", "AP_PASSWORD": "ap-secret-1"}


def files(strips, **kw):
    args = dict(name="Desk", mdns="desk", ap_ssid=None, secrets=SECRETS, use_wifi=True, strips=strips)
    args.update(kw)
    return P.build_files(**args)


class ParseStrip(unittest.TestCase):
    def test_requested_setup(self):
        s = P.parse_strip("type=ws2812,order=grb,pin=7,len=50", BOARD)
        self.assertEqual((s.type_id, P.COLOR_ORDERS[s.order], s.pin, s.length), (22, 0, 7, 50))

    def test_defaults_and_case(self):
        s = P.parse_strip("PIN=7, Len=10", BOARD)
        self.assertEqual((s.type_name, s.order), ("ws2812", "grb"))

    def test_errors(self):
        for bad in ["pin=7", "len=5", "pin=7,len=0", "pin=x,len=5", "pin=7,len=5,type=nope", "pin=7,len=5,order=xyz",
                    "pin=7,len=5,color=red", "pin=7,pin=8,len=5", "pin=99,len=5", "pin=-1,len=5", "pin=12,len=5",
                    "pin=13,len=5", "pin=7,len", "garbage"]:
            with self.subTest(bad), self.assertRaises(P.ProvisionError):
                P.parse_strip(bad, BOARD)


class ValidateStrips(unittest.TestCase):
    def strip(self, pin, n=10):
        return P.Strip("ws2812", "grb", pin, n)

    def test_ok_and_strapping_warning(self):
        self.assertEqual(P.validate_strips([self.strip(7)], BOARD), [])
        self.assertEqual(len(P.validate_strips([self.strip(9)], BOARD)), 1)

    def test_rejects(self):
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([], BOARD)
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([self.strip(7), self.strip(7)], BOARD)  # shared pin
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([self.strip(7, 3000), self.strip(6, 3000)], BOARD)  # too many LEDs
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([self.strip(p) for p in range(9)], BOARD)  # too many strips


class MatrixStrips(unittest.TestCase):
    M = "pin=18,len=256,matrix=32x8"

    def test_parse_defaults_and_derived_len(self):
        s = P.parse_strip("pin=18,matrix=32x8", BOARD)
        self.assertEqual((s.length, s.matrix), (256, P.Matrix(32, 8, True, True, False, False)))
        self.assertEqual(P.parse_strip("pin=18,matrix=8x8,vertical=no,serpentine=no,bottom=yes", BOARD).matrix,
                         P.Matrix(8, 8, False, False, True, False))

    def test_bar_panel_starts_bottom_right(self):
        s = P.parse_strip("pin=18,matrix=32x8,bottom=yes,right=yes", BOARD)
        p = files([s])["cfg.json"]["hw"]["led"]["matrix"]["panels"][0]
        self.assertEqual((p["b"], p["r"], p["v"], p["s"]), (True, True, True, True))

    def test_errors(self):
        for bad in ["pin=18,matrix=32", "pin=18,matrix=0x8", "pin=18,len=100,matrix=32x8", "pin=18,len=9,serpentine=yes"]:
            with self.subTest(bad), self.assertRaises(P.ProvisionError):
                P.parse_strip(bad, BOARD)

    def test_matrix_must_be_first_and_unique(self):
        m, st = P.parse_strip("pin=18,matrix=32x8", BOARD), P.Strip("ws2812", "grb", 19, 50)
        P.validate_strips([m, st], BOARD)
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([st, m], BOARD)
        with self.assertRaises(P.ProvisionError):
            P.validate_strips([m, P.parse_strip("pin=19,matrix=8x8", BOARD)], BOARD)

    def test_files_matrix_plus_strip(self):
        f = files([P.parse_strip("pin=18,matrix=32x8", BOARD), P.Strip("ws2812", "grb", 19, 50)])
        led = f["cfg.json"]["hw"]["led"]
        self.assertEqual(led["total"], 306)
        self.assertEqual(led["matrix"], {"mpc": 1, "panels": [{"b": False, "r": False, "v": True, "s": True,
                                                              "x": 0, "y": 0, "h": 8, "w": 32}]})
        self.assertEqual([(i["start"], i["len"], i["pin"]) for i in led["ins"]], [(0, 256, [18]), (256, 50, [19])])
        seg = f["presets.json"]["1"]["seg"]
        self.assertEqual([(s["start"], s["stop"], s.get("startY"), s.get("stopY")) for s in seg],
                         [(0, 32, 0, 8), (256, 306, None, None)])


class Secrets(unittest.TestCase):
    def test_parse(self):
        got = P.parse_secrets('# c\n\nWIFI_SSID = "my net"\nWIFI_PASSWORD=pa=ss\nAP_PASSWORD=\'x y z w v\'\n')
        self.assertEqual(got, {"WIFI_SSID": "my net", "WIFI_PASSWORD": "pa=ss", "AP_PASSWORD": "x y z w v"})

    def test_unknown_key_and_bad_line(self):
        for bad in ["WIFI_SSDI=x", "nonsense"]:
            with self.subTest(bad), self.assertRaises(P.ProvisionError):
                P.parse_secrets(bad)

    def test_trailing_comments(self):
        got = P.parse_secrets("WIFI_SSID=home   # my network\nWIFI_PASSWORD=pa#ss\nAP_PASSWORD=\"a #b c\"  # quoted\n")
        self.assertEqual(got, {"WIFI_SSID": "home", "WIFI_PASSWORD": "pa#ss", "AP_PASSWORD": "a #b c"})
        self.assertEqual(P.parse_secrets("WIFI_PASSWORD=")["WIFI_PASSWORD"], "")
        self.assertEqual(P.parse_secrets('WIFI_PASSWORD="#hash"')["WIFI_PASSWORD"], "#hash")
        with self.assertRaises(P.ProvisionError):   # would silently become an empty (open network) password
            P.parse_secrets("WIFI_PASSWORD=# open network")

    def test_readme_example_parses(self):
        with open(os.path.join(ROOT, "board_setup", "Readme.md"), encoding="utf-8") as f:
            block = f.read().split("```ini\n")[1].split("```")[0]
        got = P.parse_secrets(block)
        self.assertEqual(got["WIFI_SSID"], "my-home-network")
        self.assertEqual(got["WIFI_PASSWORD"], "...")

    def test_bad_quotes(self):
        for bad in ['WIFI_SSID="open', "WIFI_SSID='a' b", 'WIFI_SSID="a"b']:
            with self.subTest(bad), self.assertRaises(P.ProvisionError):
                P.parse_secrets(bad)

    def test_example_file_parses(self):
        with open(os.path.join(ROOT, ".wled.secrets.example"), encoding="utf-8") as f:
            self.assertEqual(set(P.parse_secrets(f.read())), P.SECRET_KEYS)


class SecretsFile(unittest.TestCase):
    def make(self, mode):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "secrets"
        path.write_text("WIFI_SSID=home\n")
        path.chmod(mode)
        return path

    def test_private_file_is_read(self):
        self.assertEqual(P.load_secrets(self.make(0o600)), {"WIFI_SSID": "home"})

    def test_group_or_world_readable_is_refused(self):
        for mode in (0o644, 0o640, 0o604):
            with self.subTest(oct(mode)), self.assertRaises(P.ProvisionError) as cm:
                P.load_secrets(self.make(mode))
            self.assertIn("chmod 600", str(cm.exception))

    def test_override_only_warns(self):
        with mock.patch.object(sys, "stderr") as err:
            self.assertEqual(P.load_secrets(self.make(0o644), allow_insecure=True), {"WIFI_SSID": "home"})
        self.assertTrue(err.write.called)

    def test_write_private_is_0600(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fresh, old = Path(d.name) / "a", Path(d.name) / "b"
        old.write_text("x")
        old.chmod(0o644)
        for path in (fresh, old):
            P.write_private(path, "secret")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(), "secret")


class FakeEsptool:
    """Stands in for P.run_tool: answers esptool read_flash from a dict {offset: bytes} and records every call."""
    BANNER = "Chip type:  ESP32-C6FH4\nBASE MAC:           02:00:00:00:00:01\n"

    def __init__(self, flash):
        self.flash, self.calls = flash, []

    def __call__(self, cmd, what):
        self.calls.append(cmd)
        if "read_flash" in cmd:
            i = cmd.index("read_flash")
            addr, size, out = int(cmd[i + 1], 0), int(cmd[i + 2], 0), cmd[i + 3]
            data = self.flash.get(addr, b"\xff" * size)
            Path(out).write_bytes(data[:size])
        return self.BANNER


class BoardGuards(unittest.TestCase):
    WLED_APP = b"\xe9\x03" + b"\0" * 100 + b"WLED-AP" + b"\0" * 100
    OTHER_APP = b"\xe9\x03" + b"\0" * 100 + b"blinky" + b"\0" * 100

    def probe(self, flash, **kw):
        fake = FakeEsptool(flash)
        with mock.patch.object(P, "run_tool", fake), mock.patch.object(P, "esptool_cmd", lambda chip, port: ["esptool"]), \
                mock.patch("builtins.print"):
            return P.probe_board("/dev/fake", "esp32c6", **kw), fake

    def test_wled_app_accepted(self):
        mac, _ = self.probe({0x0: b"\xe9\0\0\0", 0x10000: self.WLED_APP}, app_size=len(self.WLED_APP))
        self.assertEqual(mac, "020000000001")

    def test_foreign_app_refused_unless_forced(self):
        flash = {0x0: b"\xe9\0\0\0", 0x10000: self.OTHER_APP}
        with self.assertRaises(P.ProvisionError) as cm:
            self.probe(flash, app_size=len(self.OTHER_APP))
        self.assertIn("--force", str(cm.exception))
        mac, fake = self.probe(flash, app_size=len(self.OTHER_APP), force=True)
        self.assertEqual(mac, "020000000001")
        self.assertEqual(len(fake.calls), 2)   # --force does not even read the application

    def test_missing_app_refused_even_when_forced(self):
        with self.assertRaises(P.ProvisionError):
            self.probe({0x0: b"\xe9\0\0\0"}, force=True)

    def test_backup_is_private_and_holds_the_old_filesystem(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        old = b"old filesystem with wifi psk"
        fake = FakeEsptool({0x300000: old})
        with mock.patch.object(P, "run_tool", fake), mock.patch.object(P, "esptool_cmd", lambda chip, port: ["esptool"]):
            dest = P.backup_filesystem("/dev/fake", "esp32c6", 0x300000, len(old), "020000000001", Path(d.name) / "bk")
        self.assertEqual(dest.read_bytes(), old)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(dest.parent.stat().st_mode), 0o700)
        self.assertIn("020000000001", dest.name)

    def test_failed_backup_leaves_no_file_and_blocks_the_flash(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        calls = []

        def run_tool(cmd, what):
            calls.append(what)
            if "read_flash" in cmd:
                raise P.ProvisionError("boom")
            return ""
        with mock.patch.object(P, "run_tool", run_tool), mock.patch.object(P, "esptool_cmd", lambda chip, port: ["esptool", "read_flash"]), \
                mock.patch.object(P.glob, "glob", lambda pat: ["/fake/mklittlefs"]):
            with self.assertRaises(P.ProvisionError):
                P.build_and_flash({"cfg.json": {"a": 1}}, 0x300000, 4096, "/dev/fake", "esp32c6", "020000000001", Path(d.name) / "bk")
        self.assertEqual(list((Path(d.name) / "bk").iterdir()), [])
        self.assertFalse(any("write_flash" in c for c in calls))


class PioConfig(unittest.TestCase):
    CSV = "# Name, Type, SubType, Offset, Size\nnvs, data, nvs, 0x9000, 0x5000\napp0, app, ota_0, 0x10000, 0x1A0000\nspiffs, data, spiffs, 0x300000, 0x100000\n"

    def setup_dir(self, csv, **sections):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        wled = Path(d.name)
        (wled / "platformio.ini").write_text("")
        (wled / "p.csv").write_text(csv)
        return wled

    def run_with(self, wled, stdout="", returncode=0, exc=None, env_items=(("board_build.partitions", "p.csv"),)):
        out = stdout or json.dumps([["env:e", list(map(list, env_items))]])

        def fake(*a, **k):
            if exc:
                raise exc
            return subprocess.CompletedProcess(a, returncode, out, "stderr text")
        with mock.patch.object(P.subprocess, "run", fake):
            return P.read_partitions(wled, "e")

    def test_reads_table_and_tolerates_a_preamble(self):
        wled = self.setup_dir(self.CSV)
        rows = self.run_with(wled, "Warning: something [not json]\nupdate available\n" + json.dumps([["env:e", [["board_build.partitions", "p.csv"]]]]))
        self.assertIn(("spiffs", "data", "spiffs", 0x300000, 0x100000), rows)
        self.assertEqual(P.app_partition_size(rows), 0x1A0000)
        with mock.patch.object(P, "read_partitions", lambda *a: rows):
            self.assertEqual(P.fs_partition(wled, "e"), (0x300000, 0x100000))

    def test_errors_become_provision_errors(self):
        wled = self.setup_dir(self.CSV)
        cases = {
            "no pio": dict(exc=FileNotFoundError("pio")),
            "pio fails": dict(returncode=1),
            "not json": dict(stdout="nothing useful here"),
            "no partitions key": dict(env_items=()),
            "missing csv": dict(env_items=(("board_build.partitions", "nope.csv"),)),
        }
        for label, kw in cases.items():
            with self.subTest(label), self.assertRaises(P.ProvisionError):
                self.run_with(wled, **kw)
        bad = self.setup_dir("spiffs, data, spiffs, soon, 0x1000\n")
        with self.assertRaises(P.ProvisionError):
            self.run_with(bad)

    def test_run_tool_missing_binary(self):
        with mock.patch.object(P.subprocess, "run", side_effect=FileNotFoundError("x")):
            with self.assertRaises(P.ProvisionError):
                P.run_tool(["nonexistent"], "thing")


class VerifySsid(unittest.TestCase):
    def verify(self, cfg_ssid):
        info = {"mac": "020000000001", "name": "Desk", "leds": {"count": 5}, "ip": "1.2.3.4", "ver": "x"}
        cfg = {"nw": {"ins": [{"ssid": cfg_ssid}]},
               "hw": {"led": {"ins": [{"pin": [7], "len": 5, "type": 22, "order": 0}]}}}
        with mock.patch.object(P, "http_json", lambda url, timeout=4.0: info if url.endswith("info") else cfg), \
                mock.patch("builtins.print"):
            P.verify("desk", "Desk", [P.Strip("ws2812", "grb", 7, 5)], 1, "020000000001", "home")

    def test_matching_ssid(self):
        self.verify("home")

    def test_wrong_ssid(self):
        with self.assertRaises(P.ProvisionError) as cm:
            self.verify("other")
        self.assertIn("WiFi network", str(cm.exception))


class BoardProbe(unittest.TestCase):
    BANNER = ("Connected to ESP32-C6 on /dev/cu.usbmodem1101:\nChip type:  ESP32-C6FH4\n"
              "MAC:                02:00:00:ff:fe:00:00:01\nBASE MAC:           02:00:00:00:00:01\nMAC_EXT:            ff:fe\n")

    def test_parse_mac_prefers_base_mac(self):
        self.assertEqual(P.parse_mac(self.BANNER), "020000000001")

    def test_parse_mac_six_byte_chips(self):
        self.assertEqual(P.parse_mac("Chip is ESP32-D0WD\nMAC: 24:6f:28:aa:bb:cc\nUploading stub"), "246f28aabbcc")

    def test_parse_mac_absent(self):
        self.assertIsNone(P.parse_mac("A fatal error occurred"))

    def test_has_image(self):
        self.assertTrue(P.has_image(b"\xe9\x03\x02\x10"))
        self.assertFalse(P.has_image(b"\xff\xff\xff\xff"))  # erased flash
        self.assertFalse(P.has_image(b""))


class Names(unittest.TestCase):
    def test_mdns(self):
        P.check_names("Desk", "desk-1", None)
        for bad in ["Desk", "-x", "x-", "a_b", "", "a" * 33, "a.b"]:
            with self.subTest(bad), self.assertRaises(P.ProvisionError):
                P.check_names("Desk", bad, None)

    def test_name_length(self):
        with self.assertRaises(P.ProvisionError):
            P.check_names("n" * 33, "desk", None)


class BuildFiles(unittest.TestCase):
    def test_single_ws2812_grb_50(self):
        f = files([P.Strip("ws2812", "grb", 7, 50)])
        cfg = f["cfg.json"]
        self.assertEqual(cfg["id"], {"mdns": "desk", "name": "Desk"})
        self.assertEqual(cfg["hw"]["led"]["total"], 50)
        self.assertEqual(cfg["hw"]["led"]["ins"], [{"start": 0, "len": 50, "pin": [7], "order": 0, "rev": False,
                                                    "skip": 0, "type": 22, "ref": False}])
        self.assertEqual(cfg["nw"]["ins"], [{"ssid": "home"}])
        self.assertEqual(cfg["def"]["ps"], 1)
        seg = f["presets.json"]["1"]["seg"]
        self.assertEqual([(s["start"], s["stop"]) for s in seg], [(0, 50)])

    def test_two_strips_are_contiguous(self):
        f = files([P.Strip("ws2812", "grb", 7, 50), P.Strip("sk6812", "rgb", 6, 30)])
        ins = f["cfg.json"]["hw"]["led"]["ins"]
        self.assertEqual([(i["start"], i["len"], i["pin"], i["type"], i["order"]) for i in ins],
                         [(0, 50, [7], 22, 0), (50, 30, [6], 30, 1)])
        self.assertEqual([(s["id"], s["start"], s["stop"]) for s in f["presets.json"]["1"]["seg"]], [(0, 0, 50), (1, 50, 80)])
        self.assertEqual(f["cfg.json"]["hw"]["led"]["total"], 80)

    def test_passwords_only_in_wsec(self):
        f = files([P.Strip("ws2812", "grb", 7, 5)], ap_ssid="desk-ap")
        self.assertEqual(f["wsec.json"], {"nw": {"ins": [{"psk": "hunter2hunter2"}]}, "ap": {"psk": "ap-secret-1"}})
        self.assertEqual(f["cfg.json"]["ap"], {"ssid": "desk-ap"})
        self.assertNotIn("hunter2", json.dumps(f["cfg.json"]) + json.dumps(f["presets.json"]))

    def test_redact_hides_passwords(self):
        out = P.redact(files([P.Strip("ws2812", "grb", 7, 5)]))
        self.assertNotIn("hunter2", out)
        self.assertNotIn("ap-secret", out)
        self.assertIn("home", out)

    def test_no_wifi(self):
        f = files([P.Strip("ws2812", "grb", 7, 5)], use_wifi=False, secrets={})
        self.assertNotIn("nw", f["cfg.json"])
        self.assertEqual(f["wsec.json"], {})

    def test_bad_secrets(self):
        s = [P.Strip("ws2812", "grb", 7, 5)]
        for secrets in [{}, {"WIFI_SSID": "x"}, {**SECRETS, "WIFI_PASSWORD": "short"}, {**SECRETS, "AP_PASSWORD": "short"},
                        {**SECRETS, "WIFI_SSID": "s" * 33}]:
            with self.subTest(secrets), self.assertRaises(P.ProvisionError):
                files(s, secrets=secrets)

    def test_open_network_allowed(self):
        files([P.Strip("ws2812", "grb", 7, 5)], secrets={**SECRETS, "WIFI_PASSWORD": ""})


if __name__ == "__main__":
    unittest.main()
