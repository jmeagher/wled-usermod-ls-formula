"""board_setup/devices/backyard/segments.py: the star and tree segments must tile their strands exactly.

Run: python3 -I -m unittest discover -s tests
"""
import importlib.util
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("backyard_segments", os.path.join(ROOT, "board_setup", "devices", "backyard", "segments.py"))
seg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seg)


def leds(segs, *names):
    """The LEDs covered by the named segments, as a sorted list; fails on a name that does not exist or an overlap."""
    by = {s["n"]: s for s in segs}
    out = []
    for n in names:
        out += range(by[n]["start"], by[n]["stop"])
    assert len(out) == len(set(out)), "overlap in %s" % (names,)
    return sorted(out)


STAR_TIPS = ["Star Top-R", "Star Lower-R", "Star Upper-L", "Star Upper-R", "Star Lower-L", "Star Top-L"]
STAR_INNER = ["Star Inner %d" % k for k in range(1, 6)]
STAR_LINES = ["Star Line %d" % k for k in range(1, 6)]
TIERS = ["Tree Tier 1 R", "Tree Tier 1 L", "Tree Tier 2 R", "Tree Tier 2 L", "Tree Tier 3"]


class Layout(unittest.TestCase):
    def both(self):
        return [(end, seg.layout(end)) for end in ("end", "start")]

    def test_ids_names_and_limits(self):
        for end, segs in self.both():
            self.assertEqual([s["id"] for s in segs], list(range(len(segs))))
            self.assertEqual(len({s["n"] for s in segs}), len(segs))
            self.assertLessEqual(len(segs), 64)               # WLED's limit on a C6
            for s in segs:
                self.assertTrue(0 <= s["start"] < s["stop"] <= 200, s)
                self.assertLessEqual(len(s["n"]), 32)
            self.assertEqual([(s["n"], s["start"], s["stop"]) for s in segs[:2]], [("Star", 0, 100), ("Tree", 100, 200)])

    def test_only_the_whole_strands_replace_pixels(self):
        for _, segs in self.both():
            self.assertEqual([s["bm"] for s in segs], [0, 0] + [seg.BLEND_ADD] * (len(segs) - 2))

    def test_star_outline_and_inner_tile_the_star(self):
        for _, segs in self.both():
            self.assertEqual(leds(segs, *STAR_TIPS, *STAR_INNER), list(range(100)))
            self.assertEqual(leds(segs, *STAR_LINES), list(range(100)))
            by = {s["n"]: s for s in segs}
            # A point is two outer legs meeting at a corner: 16 LEDs, except the top point, split in two 8s.
            self.assertEqual([by[n]["stop"] - by[n]["start"] for n in STAR_TIPS], [8, 16, 16, 16, 16, 8])
            # Each corner k sits between line k-1 and line k, i.e. at LED 20k.
            for k, n in enumerate(STAR_TIPS[1:5], start=1):
                self.assertEqual((by[n]["start"] + by[n]["stop"]) // 2, 20 * k)

    def test_tree_tiles_the_tree(self):
        for end, segs in self.both():
            tree = list(range(100, 200))
            self.assertEqual(leds(segs, "Tree Body", "Tree Ornament"), tree)
            self.assertEqual(leds(segs, *TIERS), leds(segs, "Tree Body"))
            self.assertEqual(leds(segs, "Tree Right", "Tree Base", "Tree Left"), leds(segs, "Tree Body"))
            by = {s["n"]: s for s in segs}
            self.assertEqual(by["Tree Ornament"]["stop"] - by["Tree Ornament"]["start"], 19)
            # The ornament is at one end of the strand, the body starts or ends at the plug.
            if end == "end":
                self.assertEqual((by["Tree Body"]["start"], by["Tree Ornament"]["stop"]), (100, 200))
            else:
                self.assertEqual((by["Tree Ornament"]["start"], by["Tree Body"]["stop"]), (100, 200))
            # Tier 3 includes the base.
            self.assertTrue(set(leds(segs, "Tree Base")) <= set(leds(segs, "Tree Tier 3")))

    def test_bad_ornament_position(self):
        with self.assertRaises(ValueError):
            seg.layout("middle")


class Presets(unittest.TestCase):
    def test_every_preset_names_real_segments_and_colours(self):
        names = {s["n"] for s in seg.layout()}
        ids = [p[0] for p in seg.test_presets()] + [p[0] for p in seg.playlists()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(seg.BOOT_PRESET, ids)
        self.assertTrue(all(1 < i < 251 for i in ids))
        for pid, name, lit in seg.test_presets():
            self.assertLessEqual(len(name), 32)
            self.assertTrue(set(lit) <= names, (pid, set(lit) - names))
            self.assertTrue(set(lit.values()) <= set(seg.COLORS))

    def test_playlists_use_saved_presets(self):
        saved = {p[0] for p in seg.test_presets()}
        for pid, name, members in seg.playlists():
            self.assertLessEqual(len(name), 32)
            self.assertTrue(set(members) <= saved, name)

    def test_payload_names_every_segment_and_lights_only_the_chosen(self):
        segs = seg.layout()
        pid, name, lit = seg.test_presets()[1]
        p = seg.preset_payload(pid, name, lit, segs)
        self.assertTrue(p["o"] and p["psave"] == pid)
        self.assertEqual([g["id"] for g in p["seg"]], [s["id"] for s in segs])
        on = [segs[g["id"]]["n"] for g in p["seg"] if g["on"]]
        self.assertEqual(sorted(on), sorted(lit))
        self.assertNotIn("start", p["seg"][0])             # no bounds: loading it must not touch the layout

    def test_playlist_payload(self):
        p = seg.playlist_payload(40, "x", [10, 11], 4)
        self.assertEqual(p["playlist"]["dur"], [40, 40])
        self.assertEqual(p["playlist"]["transition"], [0, 0])


if __name__ == "__main__":
    unittest.main()
