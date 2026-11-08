"""Tests for tools/lsfx.py against a fake WLED that compiles with the real lsf_cli.

Run after tests/run.sh (it builds build/lsf_cli):  python3 -I -m unittest discover -s wled/tests
"""
import contextlib
import io
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import lsfx  # noqa: E402

CLI = os.path.join(HERE, "..", "build", "lsf_cli")
SLOTS = 4


class QuietServer(HTTPServer):
    """HTTPServer without the reverse-DNS lookup in server_bind(), which can stall for seconds."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class FakeWled(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _reply(self):
        body = json.dumps({"on": True, "lsf": {"slots": FakeWled.state["slots"], "last": FakeWled.state["last"],
                                                 "limits": {"src": 1024, "instrs": 256, "locals": 16, "slots": SLOTS, "budgetUs": 15000, "pixels": FakeWled.state["pixels"]}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeWled.state["posts"].append(req)
        o = req.get("lsf")
        if o:
            slot = o["slot"]
            if o.get("clear"):
                FakeWled.state["slots"][slot].update(ok=False, src="")
                FakeWled.state["last"] = {"slot": slot, "ok": True, "dry": False}
            else:
                with tempfile.NamedTemporaryFile("w", suffix=".lsf", delete=False) as f:
                    f.write(o["src"])
                r = subprocess.run([CLI, f.name], capture_output=True, text=True)
                os.unlink(f.name)
                if r.returncode == 0:
                    m = re.search(r"ok: (\d+) instructions", r.stderr)
                    cost = int(re.search(r"cost (\d+)", r.stderr).group(1))
                    est = (FakeWled.state["pixels"] * (cost * 3 + 10) + 9) // 10
                    maxpx = 15000 * 10 // (cost * 3 + 10)
                    last = {"slot": slot, "ok": True, "dry": o.get("dry", False), "instrs": int(m.group(1)), "cost": cost, "estUs": est, "maxPx": maxpx}
                    if est > 15000:   # same rule as the device: reject, no source line involved
                        last.update(ok=False, err="over budget: ~%d ms for %d LEDs, this formula fits %d LEDs" % ((est + 999) // 1000, FakeWled.state["pixels"], maxpx), line=0, pos=0)
                    FakeWled.state["last"] = last
                    if last["ok"] and not o.get("dry"):
                        FakeWled.state["slots"][slot].update(ok=True, src=o["src"], instrs=int(m.group(1)), cost=cost, estUs=est, maxPx=maxpx,
                                                            name=o.get("name", "LS Formula %d" % (slot + 1)))
                else:
                    m = re.search(r":(\d+): error: (.*)", r.stderr)
                    FakeWled.state["last"] = {"slot": slot, "ok": False, "dry": o.get("dry", False), "err": m.group(2), "line": int(m.group(1)), "pos": 0}
        self._reply()


class LsfxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run wled/tests/run.sh first")
        cls.srv = QuietServer(("127.0.0.1", 0), FakeWled)
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        FakeWled.state = {"posts": [], "pixels": 50, "last": {"slot": -1, "ok": False},
                          "slots": [{"slot": k, "fx": 200 + k, "name": "LS Formula %d" % (k + 1), "ok": False, "src": ""} for k in range(SLOTS)]}

    def run_cmd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = lsfx.main([self.host] + list(argv))
        return rc, out.getvalue(), err.getvalue()

    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".lsf", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_push_activate(self):
        path = self.write("hsv(x + t / 8, 255, 255)\n")
        rc, out, _ = self.run_cmd("push", "1", path, "--name", "Wave", "--activate", "--seg", "2")
        self.assertEqual(rc, 0)
        self.assertIn("installed in slot 1 as 'Wave' (effect id 201)", out)
        self.assertEqual(FakeWled.state["posts"][-1], {"seg": [{"id": 2, "fx": 201}]})

    def test_error_shows_source_line(self):
        path = self.write("let a = 1\nfoo(a)\n")
        rc, _, err = self.run_cmd("push", "0", path)
        self.assertEqual(rc, 1)
        self.assertIn("unknown function (line 2)", err)
        self.assertIn("  foo(a)", err)
        self.assertFalse(FakeWled.state["slots"][0]["ok"])

    def test_over_budget_is_rejected_without_a_source_line(self):
        FakeWled.state["pixels"] = 5000   # far more LEDs than a heavy formula can drive
        path = self.write("noise(x, y) + noise(t, i) + noise(x + 7, y) + noise(t, i + 9) + noise(x, t)\n")
        rc, _, err = self.run_cmd("push", "0", path)
        self.assertEqual(rc, 1)
        self.assertIn("over budget: ~", err)
        self.assertIn("this formula fits", err)
        self.assertNotIn("line 0", err)
        self.assertNotIn("\n  noise", err)           # no source excerpt for a non-source error
        self.assertFalse(FakeWled.state["slots"][0]["ok"])

    def test_push_reports_estimate(self):
        rc, out, _ = self.run_cmd("push", "0", self.write("x\n"))
        self.assertEqual(rc, 0)
        self.assertIn("est 65 us per frame, fits", out)       # 50 px * (1*0.3+1) us = 65 us

    def test_validate_does_not_install(self):
        path = self.write("x\n")
        rc, out, _ = self.run_cmd("validate", "0", path)
        self.assertEqual(rc, 0)
        self.assertIn("valid:", out)
        self.assertFalse(FakeWled.state["slots"][0]["ok"])

    def test_slot_range(self):
        rc, _, err = self.run_cmd("push", "9", self.write("x\n"))
        self.assertEqual(rc, 2)
        self.assertIn("slot must be 0..3", err)

    def test_pull_and_clear(self):
        self.run_cmd("push", "3", self.write("x + 1\n"))
        rc, out, _ = self.run_cmd("pull", "3")
        self.assertEqual((rc, out), (0, "x + 1\n"))
        rc, out, _ = self.run_cmd("clear", "3")
        self.assertEqual(rc, 0)
        self.assertFalse(FakeWled.state["slots"][3]["ok"])

    def test_info(self):
        rc, out, _ = self.run_cmd("info")
        self.assertEqual(rc, 0)
        self.assertIn("slot 0  fx=200", out)
        self.assertIn("limits: 1024 source bytes", out)

    def test_unreachable(self):
        rc = lsfx.main(["127.0.0.1:1", "info"])
        self.assertEqual(rc, 2)

    def test_missing_usermod(self):
        class Bare(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"on":true}')
        s = QuietServer(("127.0.0.1", 0), Bare)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = lsfx.main(["127.0.0.1:%d" % s.server_address[1], "info"])
        self.assertEqual(rc, 2)
        self.assertIn("usermod is not in this firmware", err.getvalue())


if __name__ == "__main__":
    unittest.main()
