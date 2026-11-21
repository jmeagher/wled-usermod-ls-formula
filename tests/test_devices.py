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
ENTRY = re.compile(r'^\s*"(\d+)\|([\w.-]+\.lsf)\|([^|]+)\|(\d+)"\s*$', re.M)   # bar: "slot|file|preset name|preset id"
PUSH = re.compile(r'^push_slot (\d+) ([\w.-]+\.lsf) "([^"]+)"', re.M)       # push_slot SLOT FILE "effect name"
PRESET = re.compile(r'^save_preset (\d+) "([^"]+)" (\d+) (\d+) (\w+)', re.M)   # save_preset ID "name" SLOT C1 PALETTE
PALETTES = os.path.join(ROOT, "board_setup", "devices", "palettes")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def scripts():
    return sorted(glob.glob(os.path.join(ROOT, "board_setup", "devices", "*", "apply.sh")))


def installed():
    """(script, slot, formula path, effect name) for every formula an apply.sh puts in a slot."""
    out = []
    for script in scripts():
        text = read(script)
        rel = os.path.relpath(script, ROOT)
        for slot, fname, name in PUSH.findall(text):
            out.append((rel, int(slot), os.path.join(ROOT, "examples", fname), name))
        for slot, fname, name, _ in ENTRY.findall(text):
            out.append((rel, int(slot), os.path.join(ROOT, "examples", fname), name))
    return out


def presets(script):
    """(id, name, slot, c1, palette) for every preset an apply.sh saves."""
    return [(int(i), n, int(s), int(c), p) for i, n, s, c, p in PRESET.findall(read(script))]


def palette_slots(name):
    """The 16 colours WLED builds from a custom palette file: one per 16 index values."""
    flat = json.loads(read(os.path.join(PALETTES, name + ".json")))["palette"]
    stops = [flat[i:i + 4] for i in range(0, len(flat), 4)]
    assert [s[0] for s in stops[:16]] == [16 * k for k in range(16)]
    return [tuple(s[1:]) for s in stops[:16]]


class DeviceFormulas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def test_scripts_list_formulas(self):
        self.assertTrue(installed())

    def test_formulas_exist_compile_and_fit(self):
        for script, slot, path, name in installed():
            with self.subTest("%s: %s" % (script, os.path.basename(path))):
                self.assertTrue(os.path.exists(path), "%s installs %s, which is not in examples/" % (script, path))
                self.assertLessEqual(os.path.getsize(path), 1024)
                r = subprocess.run([CLI, path, "-n", "50", "-t", "12345", "-f", "1"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stdout.split()), 50)

    def test_slots_and_presets_are_unique(self):
        for script in scripts():
            rows = [r for r in installed() if r[0] == os.path.relpath(script, ROOT)]
            self.assertTrue(rows)
            self.assertEqual(len({r[1] for r in rows}), len(rows), "duplicate slot in " + script)
            pre = presets(script)
            if not pre:    # the bar installs its presets another way
                continue
            self.assertEqual(len({p[0] for p in pre}), len(pre), "duplicate preset id in " + script)
            for pid, name, slot, c1, palette in pre:
                self.assertIn(slot, {r[1] for r in rows}, "%s: preset %d uses an empty slot" % (script, pid))
                self.assertTrue(os.path.exists(os.path.join(PALETTES, palette + ".json")), palette)
                self.assertTrue(0 <= c1 <= 255)

    def test_each_board_has_both_seasons(self):
        for script in scripts():
            if os.path.basename(os.path.dirname(script)) == "bar":
                continue
            used = {p[4] for p in presets(script)}
            self.assertEqual(used, {"christmas", "fall"}, script)


def render(formula, palette, c1, n=50, times=range(500, 40000, 1337), **kw):
    """Every non-dark pixel the formula draws over a stretch of time, as (r, g, b) tuples."""
    seen = []
    for t in times:
        args = [CLI, os.path.join(ROOT, "examples", formula), "-n", str(n), "-t", str(t), "-f", "1", "-c1", str(c1),
                "-pal", os.path.join(PALETTES, palette + ".json")]
        r = subprocess.run(args, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        seen += [((v >> 16) & 255, (v >> 8) & 255, v & 255) for v in (int(x, 16) for x in r.stdout.split())]
    return seen


class PaletteFormulas(unittest.TestCase):
    """The palette formulas show only the palette's colours, so one slot serves both seasons."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def hues(self, px):
        out = set()
        for r, g, b in px:
            if max(r, g, b) < 60:
                continue    # too dark to read a colour from
            h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            out.add("red" if (h * 360 >= 345 or h * 360 <= 15) else "green" if 100 <= h * 360 <= 140 else "other")
        return out

    def test_christmas_is_only_red_and_green(self):
        for formula, c1 in [("palette_twinkle.lsf", 0), ("palette_chase.lsf", 128), ("palette_garland.lsf", 128),
                            ("palette_fill.lsf", 128), ("palette_drops.lsf", 0)]:
            with self.subTest(formula):
                seen = self.hues(render(formula, "christmas", c1))
                self.assertTrue(seen and seen <= {"red", "green"}, seen)

    def test_two_colour_formulas_show_both_colours(self):
        for formula in ["palette_chase.lsf", "palette_garland.lsf", "palette_fill.lsf"]:
            with self.subTest(formula):
                self.assertEqual(self.hues(render(formula, "christmas", 128)), {"red", "green"})

    def test_fall_has_no_green_or_blue(self):
        for formula, c1 in [("palette_twinkle.lsf", 255), ("palette_chase.lsf", 128), ("palette_garland.lsf", 128),
                            ("palette_fill.lsf", 128)]:
            with self.subTest(formula):
                px = render(formula, "fall", c1)
                self.assertTrue(px)
                self.assertTrue(all(g <= r and b <= 12 for r, g, b in px), "%s drew a colour outside the fall palette" % formula)

    def test_fall_leaves_have_green_dots_and_orange_drops(self):
        px = render("palette_drops.lsf", "fall", 128)     # Color 2 (k1) is green in the CLI too
        self.assertTrue(any(g >= 60 and r < 30 and b < 30 for r, g, b in px), "no green dots")
        self.assertTrue(any(r > 200 and 40 < g < 210 and b < 20 for r, g, b in px), "no orange leaves")

    def test_slots_are_the_palette_colours(self):
        xmas = palette_slots("christmas")
        self.assertEqual({xmas[k] for k in range(0, 16, 2)}, {(255, 0, 0)})
        self.assertEqual({xmas[k] for k in range(1, 16, 2)}, {(0, 170, 0)})
        fall = palette_slots("fall")
        self.assertEqual((fall[0], fall[15]), ((170, 30, 0), (255, 140, 0)))      # the two-colour formulas use slots 0 and 15


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
        flat = json.loads(read(path))["palette"]
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
