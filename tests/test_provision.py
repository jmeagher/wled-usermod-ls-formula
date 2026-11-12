"""board_setup/provision.py: input validation and the WLED config files it generates (no hardware needed).

Run: python3 -I -m unittest discover -s tests
"""
import importlib.util
import json
import os
import sys
import unittest

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

    def test_example_file_parses(self):
        with open(os.path.join(ROOT, ".wled.secrets.example"), encoding="utf-8") as f:
            self.assertEqual(set(P.parse_secrets(f.read())), P.SECRET_KEYS)


class BoardProbe(unittest.TestCase):
    BANNER = ("Connected to ESP32-C6 on /dev/cu.usbmodem1101:\nChip type:  ESP32-C6FH4\n"
              "MAC:                98:88:e0:ff:fe:71:09:E4\nBASE MAC:           98:88:e0:71:09:E4\nMAC_EXT:            ff:fe\n")

    def test_parse_mac_prefers_base_mac(self):
        self.assertEqual(P.parse_mac(self.BANNER), "9888e0710 9e4".replace(" ", ""))

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
