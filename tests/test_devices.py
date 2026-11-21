"""The formulas board_setup/devices/*/apply.sh installs must exist, compile and fit the default limits.

Needs build/lsf_cli: run tests/run.sh first. Run: python3 -I -m unittest discover -s tests
"""
import colorsys
import glob
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "lsf_cli")
ENTRY = re.compile(r'^\s*"(\d+)\|([\w.-]+\.lsf)\|([^|]+)\|(\d+)"\s*$', re.M)   # "slot|file|preset name|preset id"


def installed():
    out = []
    for script in sorted(glob.glob(os.path.join(ROOT, "board_setup", "devices", "*", "apply.sh"))):
        for slot, fname, name, preset in ENTRY.findall(open(script, encoding="utf-8").read()):
            out.append((os.path.relpath(script, ROOT), int(slot), os.path.join(ROOT, "examples", fname), name, int(preset)))
    return out


class DeviceFormulas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def test_scripts_list_formulas(self):
        self.assertTrue(installed())

    def test_formulas_exist_compile_and_fit(self):
        for script, slot, path, name, preset in installed():
            with self.subTest("%s: %s" % (script, os.path.basename(path))):
                self.assertTrue(os.path.exists(path), "%s installs %s, which is not in examples/" % (script, path))
                self.assertLessEqual(os.path.getsize(path), 1024)
                r = subprocess.run([CLI, path, "-n", "50", "-t", "12345", "-f", "1"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stdout.split()), 50)

    def test_slots_and_presets_are_unique(self):
        for script in {s for s, *_ in installed()}:
            rows = [r for r in installed() if r[0] == script]
            self.assertEqual(len({r[1] for r in rows}), len(rows), "duplicate slot in " + script)
            self.assertEqual(len({r[4] for r in rows}), len(rows), "duplicate preset id in " + script)


def sparkle_frames(c1, times):
    path = os.path.join(ROOT, "examples", "bar_sparkles.lsf")
    for t in times:
        r = subprocess.run([CLI, path, "-n", "256", "-t", str(t), "-f", "1", "-c1", str(c1)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        yield r.stdout.split(), r.stderr


def hue_class(rgb):
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h *= 360
    if s < 0.2:
        return "white"
    return "red" if (h >= 345 or h <= 20) else "yellow" if 35 <= h <= 65 else "green" if 80 <= h <= 160 else "pink" if 300 <= h < 345 else "other"


class BarSparkles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def classes(self, c1):
        seen, counts = set(), []
        for px, _ in sparkle_frames(c1, range(1000, 60000, 211)):
            lit = [int(x, 16) for x in px if int(x, 16)]
            counts.append(len(lit))
            seen |= {hue_class(v) for v in lit if max((v >> 16) & 255, (v >> 8) & 255, v & 255) >= 150}
        return seen, counts

    def test_default_has_no_red_or_green(self):
        seen, counts = self.classes(128)
        self.assertEqual(seen, {"yellow", "white", "pink"})
        self.assertTrue(5 <= min(counts) and max(counts) <= 10, (min(counts), max(counts)))

    def test_slider_adds_red_and_green(self):
        seen, counts = self.classes(255)
        self.assertEqual(seen, {"yellow", "white", "pink", "red", "green"})
        self.assertTrue(5 <= min(counts) and max(counts) <= 10, (min(counts), max(counts)))

    def test_fits_the_bars_speed_budget(self):
        # cost 160 is what fits 306 LEDs (the Bar's panel plus strand) in the default 15 ms budget
        _, err = next(sparkle_frames(255, [1]))
        cost = int(err.split("cost")[1].split(",")[0])
        self.assertLessEqual(cost, 160, err)


class HappyHourPalette(unittest.TestCase):
    def test_palette_file_is_what_wled_loads(self):
        path = os.path.join(ROOT, "board_setup", "devices", "bar", "happy_hour_palette.json")
        flat = json.load(open(path))["palette"]
        self.assertEqual(len(flat) % 4, 0)
        self.assertLessEqual(len(flat), 72)                      # WLED reads at most 18 stops
        stops = [flat[i:i + 4] for i in range(0, len(flat), 4)]
        pos = [st[0] for st in stops]
        self.assertEqual(pos, sorted(pos))
        self.assertEqual(pos[-1], 255)
        self.assertTrue(all(0 <= c <= 255 for st in stops for c in st))
        # one stop at the start of each of the 16 palette slots, so a random pick is always an exact colour
        self.assertEqual(pos[:16], [16 * k for k in range(16)])
        colours = {tuple(st[1:]) for st in stops}
        self.assertEqual(colours, {(255, 200, 0), (255, 255, 255), (255, 105, 180), (255, 0, 0), (0, 255, 0)})


if __name__ == "__main__":
    unittest.main()
