"""Run board_setup/devices/{mbr-door,mantle}/apply.sh against a fake WLED and check what ends up on the "board".

The fake keeps the state the scripts rely on: usermod slots (compiled with the real lsf_cli), custom palette files,
the segment, presets.json (presets and playlists), and the boot preset. Needs build/lsf_cli: run tests/run.sh first.
Run: python3 -I -m unittest discover -s tests
"""
import json
import os
import re
import socketserver
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "lsf_cli")
DEVICES = os.path.join(ROOT, "board_setup", "devices")


class QuietServer(HTTPServer):
    def server_bind(self):   # skip the reverse-DNS lookup in HTTPServer.server_bind(), which can stall for seconds
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class Board:
    def __init__(self):
        self.slots = [{"slot": i, "ok": False, "src": "", "name": "LS Formula %d" % (i + 1), "fx": 200 + i} for i in range(4)]
        self.files = {}      # "/palette0.json" -> bytes
        self.presets = {}
        self.seg = {"id": 0}
        self.boot = 0
        self.pushes = 0
        self.playing = None


def make_handler(board):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/json/info":
                return self.send_json({"leds": {"bootps": board.boot}})
            if self.path == "/json/state":
                return self.send_json({"on": True, "seg": [board.seg], "lsf": {"slots": board.slots, "last": {}, "limits": {"src": 1024, "instrs": 256, "locals": 16, "slots": 4}}})
            if self.path == "/presets.json":
                return self.send_json(board.presets)
            if self.path in board.files:
                body = board.files[self.path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload":   # multipart: the file name is in the part's Content-Disposition
                m = re.search(rb'filename="([^"]+)"\r\n(?:[^\r\n]+\r\n)*\r\n(.*)\r\n--', raw, re.S)
                board.files[m.group(1).decode()] = m.group(2)
                return self.send_json({"success": True})
            req = json.loads(raw)
            if self.path == "/json/cfg":
                board.boot = req["def"]["ps"]
            elif self.path == "/json/state":
                self.state(req)
            self.send_json({"success": True})

        def state(self, req):
            if "lsf" in req:
                o = req["lsf"]
                with tempfile.NamedTemporaryFile("w", suffix=".lsf", delete=False) as f:
                    f.write(o["src"])
                r = subprocess.run([CLI, f.name], capture_output=True, text=True)
                os.unlink(f.name)
                ok = r.returncode == 0
                if ok:
                    board.pushes += 1
                    board.slots[o["slot"]].update(ok=True, src=o["src"], name=o.get("name", ""), instrs=int(re.search(r"ok: (\d+)", r.stderr).group(1)))
                return self.send_json({"on": True, "lsf": {"slots": board.slots, "last": {"ok": ok, "err": r.stderr}, "limits": {"slots": 4}}})
            if "ps" in req:
                board.playing = req["ps"]
            for seg in req.get("seg", []):
                board.seg.update(seg)
            if "psave" in req:
                if "playlist" in req:
                    board.presets[str(req["psave"])] = {"n": req["n"], "playlist": req["playlist"]}
                else:
                    board.presets[str(req["psave"])] = {"n": req["n"], "seg": [dict(board.seg)]}
                if req.get("ql"):
                    board.presets[str(req["psave"])]["ql"] = req["ql"]

    return Handler


class ApplyScripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def run_script(self, device, board, **env):
        srv = QuietServer(("127.0.0.1", 0), make_handler(board))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            e = dict(os.environ, APPLY_SETTLE="0", SECONDS_PER_PATTERN="7", **env)
            return subprocess.run(["bash", os.path.join(DEVICES, device, "apply.sh"), "127.0.0.1:%d" % srv.server_address[1]],
                                  capture_output=True, text=True, env=e, timeout=120)
        finally:
            srv.shutdown()
            srv.server_close()

    def assert_playlist(self, board, pid, name, ps, secs=7):
        p = board.presets[str(pid)]
        self.assertEqual(p["n"], name)
        self.assertEqual(p["playlist"]["ps"], ps)
        self.assertEqual(p["playlist"]["dur"], [secs * 10] * len(ps))

    def assert_preset(self, board, pid, name, slot, c1, palette):
        p = board.presets[str(pid)]
        seg = p["seg"][0]
        self.assertEqual((p["n"], seg["fx"], seg["c1"], seg["pal"]), (name, board.slots[slot]["fx"], c1, palette), "preset %d" % pid)

    def check_palettes(self, board):
        for n, name in enumerate(["christmas", "fall"]):
            with open(os.path.join(DEVICES, "palettes", name + ".json")) as f:
                want = json.load(f)
            self.assertEqual(json.loads(board.files["/palette%d.json" % n]), want)

    def test_mbr_door_has_both_seasons(self):
        board = Board()
        r = self.run_script("mbr-door", board)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.check_palettes(board)
        self.assertEqual([s["name"] for s in board.slots], ["Palette Drops", "Palette Twinkle", "End Flames", "Palette Fill"])
        xmas, fall = 200, 199      # custom palette ids: palette0.json = 200, palette1.json = 199
        self.assert_preset(board, 3, "Falling Leaves", 0, 128, fall)
        self.assert_preset(board, 4, "Autumn Twinkle", 1, 255, fall)
        self.assert_preset(board, 5, "End Flames", 2, 128, fall)
        self.assert_preset(board, 9, "Autumn Fill", 3, 128, fall)
        self.assert_preset(board, 6, "Christmas Fall", 0, 0, xmas)
        self.assert_preset(board, 7, "Christmas Twinkle", 1, 0, xmas)
        self.assert_preset(board, 10, "Christmas Fill", 3, 128, xmas)
        self.assert_playlist(board, 2, "Fall patterns", [3, 4, 5, 9])
        self.assert_playlist(board, 8, "Christmas", [6, 7, 10])
        self.assertEqual((board.boot, board.playing), (2, 2))   # Fall stays the boot preset
        # a second run changes nothing and writes no formula to flash again
        pushes = board.pushes
        r = self.run_script("mbr-door", board)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(board.pushes, pushes)

    def test_mantle_has_both_seasons(self):
        board = Board()
        r = self.run_script("mantle", board)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.check_palettes(board)
        self.assertEqual([s["name"] for s in board.slots], ["Palette Twinkle", "Palette Chase", "Palette Garland", "Palette Fill"])
        xmas, fall = 200, 199
        for pid, name, slot, c1, pal in [(20, "Christmas Twinkle", 0, 0, xmas), (21, "Christmas Chase", 1, 128, xmas),
                                         (23, "Garland Sway", 2, 128, xmas), (24, "Christmas Fill", 3, 128, xmas),
                                         (30, "Autumn Twinkle", 0, 255, fall), (31, "Autumn Chase", 1, 128, fall),
                                         (33, "Autumn Garland", 2, 128, fall), (34, "Autumn Fill", 3, 128, fall)]:
            self.assert_preset(board, pid, name, slot, c1, pal)
        self.assert_playlist(board, 22, "Christmas", [20, 21, 23, 24])
        self.assert_playlist(board, 32, "Fall", [30, 31, 33, 34])
        self.assertEqual(board.boot, 0)   # the boot preset is not touched

    def test_mantle_fall_colors_is_a_scene(self):
        """Preset 1 is a built-in effect, not a formula; its quick-load label is what lists it as a scene."""
        board = Board()
        r = self.run_script("mantle", board)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = board.presets["1"]
        seg = p["seg"][0]
        self.assertEqual((p["n"], p["ql"]), ("Fall Colors", "FC"))
        self.assertEqual([seg[k] for k in ("fx", "sx", "ix", "c1", "c2", "c3", "pal")], [97, 70, 177, 128, 128, 16, 47])
        self.assertEqual(seg["col"][0], [255, 160, 0])
        # Only the label separates a scene from a component: the playlist's patterns must not have one.
        for pid in ("20", "21", "23", "24", "30", "31", "33", "34"):
            self.assertNotIn("ql", board.presets[pid])
        # Re-running keeps it, and it also replaces a freshly provisioned device's default "Boot" preset.
        board2 = Board()
        board2.presets["1"] = {"n": "Boot", "seg": [{}]}
        r = self.run_script("mantle", board2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(board2.presets["1"]["ql"], "FC")
        r = self.run_script("mantle", board2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(board2.presets["1"]["ql"], "FC")
        # ...but not a preset somebody else made there.
        board3 = Board()
        board3.presets["1"] = {"n": "Somebody else's preset", "seg": [{}]}
        r = self.run_script("mantle", board3)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not overwriting", r.stderr)

    def test_refuses_to_overwrite_foreign_preset_or_palette(self):
        board = Board()
        board.presets["20"] = {"n": "Somebody else's preset", "seg": [{}]}
        r = self.run_script("mantle", board)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not overwriting", r.stderr)
        self.assertEqual(board.pushes, 0)
        board = Board()
        board.files["/palette1.json"] = b'{"palette": [0, 1, 2, 3, 255, 4, 5, 6]}'
        r = self.run_script("mbr-door", board)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("different palette", r.stderr)


if __name__ == "__main__":
    unittest.main()
