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
        self.segs = {0: {"id": 0}}
        self.boot = 0
        self.pushes = 0
        self.playing = None
        self.on = True
        self.pl_stops = 0        # how many times a running playlist was stopped ("pl": -1)
        self.uploads = 0
        self.http_status = {}    # path -> status code to answer with instead of the normal reply
        self.name_limit = None   # segment names are cut to this many characters (a firmware without longtext)
        self.last_state_posts = []

    @property
    def seg(self):
        return self.segs[0]


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
            if self.path in board.http_status:
                self.send_response(board.http_status[self.path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/json/info":
                return self.send_json({"leds": {"bootps": board.boot}})
            if self.path == "/json/state":
                return self.send_json({"on": True, "on": board.on, "seg": [board.segs[k] for k in sorted(board.segs)], "lsf": {"slots": board.slots, "last": {}, "limits": {"src": 1024, "instrs": 256, "locals": 16, "slots": 4}}})
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
                board.uploads += 1
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
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "slot.lsf")
                    with open(path, "w") as f:
                        f.write(o["src"])
                    r = subprocess.run([CLI, path], capture_output=True, text=True)
                ok = r.returncode == 0
                if ok:
                    board.pushes += 1
                    board.slots[o["slot"]].update(ok=True, src=o["src"], name=o.get("name", ""), instrs=int(re.search(r"ok: (\d+)", r.stderr).group(1)))
                return self.send_json({"on": True, "lsf": {"slots": board.slots, "last": {"ok": ok, "err": r.stderr}, "limits": {"slots": 4}}})
            if req.get("pl") == -1:
                board.playing = None
                board.pl_stops += 1
            if "ps" in req:
                board.playing = req["ps"]
            if "on" in req:
                board.on = req["on"]
            for seg in req.get("seg", []):
                cur = board.segs.setdefault(seg.get("id", 0), {"id": seg.get("id", 0)})
                cur.update(seg)
                if board.name_limit and "n" in cur:
                    cur["n"] = cur["n"][:board.name_limit]
            if "psave" in req:
                if "playlist" in req:
                    board.presets[str(req["psave"])] = {"n": req["n"], "playlist": req["playlist"]}
                else:
                    board.presets[str(req["psave"])] = {"n": req["n"], "seg": [dict(board.segs[k]) for k in sorted(board.segs)]}
                if req.get("ql"):
                    board.presets[str(req["psave"])]["ql"] = req["ql"]

    return Handler


class ApplyScripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def serve(self, board):
        srv = QuietServer(("127.0.0.1", 0), make_handler(board))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "127.0.0.1:%d" % srv.server_address[1]

    def run_script(self, device, board, host=None, **env):
        host = host or self.serve(board)
        e = dict(os.environ, APPLY_SETTLE="0", SECONDS_PER_PATTERN="7")
        e.update(env)
        return subprocess.run(["bash", os.path.join(DEVICES, device, "apply.sh"), host],
                              capture_output=True, text=True, env=e, timeout=120)

    def run_lib(self, board, snippet, **env):
        """Source lib.sh against the fake board and run a snippet of shell."""
        host = self.serve(board)
        e = dict(os.environ, APPLY_SETTLE="0", SECONDS_PER_PATTERN="7")
        e.update(env)
        script = 'set -euo pipefail; . "%s/lib.sh"; dev_init "%s"; %s' % (DEVICES, host, snippet)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e, timeout=60)

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

    def test_unreadable_palette_slot_aborts_without_uploading(self):
        """A timeout / server error on the check is not "nothing there": overwriting a palette is a flash write."""
        for code in (500, 503):
            for device in ("mantle", "mbr-door", "bar"):
                with self.subTest(device=device, code=code):
                    board = Board()
                    board.http_status["/palette0.json"] = code
                    r = self.run_script(device, board)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("could not check /palette0.json", r.stderr)
                    self.assertEqual(board.uploads, 0)

    def test_unreachable_board_aborts_cleanly(self):
        r = self.run_script("mantle", Board(), host="127.0.0.1:1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not answer", r.stderr)

    def test_palettes_are_checked_before_any_is_uploaded(self):
        board = Board()
        board.files["/palette1.json"] = b'{"palette": [0, 1, 2, 3]}'    # the second one is foreign
        r = self.run_script("mbr-door", board)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(board.uploads, 0)                            # ...so the first was not uploaded either
        self.assertNotIn("/palette0.json", board.files)

    def test_identical_palettes_are_not_uploaded_again(self):
        board = Board()
        self.assertEqual(self.run_script("mantle", board).returncode, 0)
        self.assertEqual(board.uploads, 2)
        self.assertEqual(self.run_script("mantle", board).returncode, 0)
        self.assertEqual(board.uploads, 2)

    def test_running_playlist_is_stopped_before_saving(self):
        board = Board()
        board.playing = 22
        self.assertEqual(self.run_script("mantle", board).returncode, 0)
        self.assertGreater(board.pl_stops, 8)      # before every preset and playlist that is saved

    def test_an_off_device_is_left_off(self):
        for device in ("mantle", "mbr-door", "bar"):
            with self.subTest(device):
                board = Board()
                board.on = False
                r = self.run_script(device, board)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse(board.on)

    def test_off_device_restored_even_when_the_script_fails(self):
        board = Board()
        board.on = False
        board.presets["20"] = {"n": "Somebody else's preset", "seg": [{}]}
        self.assertNotEqual(self.run_script("mantle", board).returncode, 0)
        self.assertFalse(board.on)

    def test_unregistered_effect_is_not_saved(self):
        board = Board()
        for slot in board.slots:
            slot["fx"] = 255
        r = self.run_script("mantle", board)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("255", r.stderr)
        self.assertNotIn("20", board.presets)

    def test_bad_environment_values_are_rejected(self):
        for value in ("1;touch pwned", "$(id)", "a", "-5", "0"):
            with self.subTest(value):
                r = self.run_script("mantle", Board(), SECONDS_PER_PATTERN=value)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertTrue(r.stderr)
        r = self.run_script("bar", Board(), HAPPY_HOUR_SECONDS="1+1;id")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(os.path.exists("pwned"))

    def test_bad_host_is_rejected(self):
        for host in ("evil.local/../x", "a b", "h;id", "host?x=1", "[::1]", "$(id)"):
            with self.subTest(host):
                r = self.run_script("mantle", Board(), host=host)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("invalid host", r.stderr)

    def test_preset_names_are_json_escaped(self):
        board = Board()
        name = 'He said "hi" \\ \\n'
        r = self.run_lib(board, 'save_preset 40 "$NAME" 0 0 fall', NAME=name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(board.presets["40"]["n"], name)

    def test_bar(self):
        board = Board()
        r = self.run_script("bar", board)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(board.boot, 1)
        with open(os.path.join(DEVICES, "bar", "happy_hour_palette.json")) as f:
            self.assertEqual(json.loads(board.files["/palette0.json"]), json.load(f))
        p1, p2 = board.presets["1"], board.presets["2"]
        self.assertEqual((p1["n"], p1["ql"], p2["n"]), ("Birdie's Bar", "BB", "Happy Hour sign"))
        self.assertEqual([s["fx"] for s in p1["seg"]], [122, board.slots[0]["fx"], 74])
        self.assertEqual(board.presets["3"]["playlist"]["ps"], [2])
        self.assertEqual(board.presets["3"]["playlist"]["end"], 1)
        pushes, uploads = board.pushes, board.uploads
        self.assertEqual(self.run_script("bar", board).returncode, 0)    # re-runnable: no more flash writes
        self.assertEqual((board.pushes, board.uploads), (pushes, uploads))

    def test_bar_refuses_foreign_presets(self):
        for pid in ("1", "2", "3"):
            board = Board()
            board.presets[pid] = {"n": "Somebody else's preset", "seg": [{}]}
            r = self.run_script("bar", board)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not overwriting", r.stderr)
            self.assertEqual(board.pushes, 0)

    def test_bar_firmware_check_failure_leaves_the_segment_name_alone(self):
        board = Board()
        board.segs[0]["n"] = "Panel"
        board.name_limit = 64
        r = self.run_script("bar", board)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("keeps only 64 characters", r.stderr)
        self.assertEqual(board.segs[0]["n"], "Panel")
        self.assertEqual(board.presets, {})
        self.assertEqual(board.pushes, 0)


if __name__ == "__main__":
    unittest.main()
