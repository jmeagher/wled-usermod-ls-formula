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


STAR_TIPS = ["Star Top-Left", "Star Lower-Left", "Star Upper-Right", "Star Upper-Left", "Star Lower-Right", "Star Top-Right"]
STAR_INNER = ["Star Inner %d" % k for k in range(1, 6)]
STAR_LINES = ["Star Line %d" % k for k in range(1, 6)]
TIERS = ["Tree Tier 1 L", "Tree Tier 1 R", "Tree Tier 2 L", "Tree Tier 2 R", "Tree Tier 3"]


class Layout(unittest.TestCase):
    def both(self):
        return [("live", seg.layout())]

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

    def test_star_parts_stay_on_the_star(self):
        by = {s["n"]: s for s in seg.layout()}
        for n in STAR_TIPS + STAR_INNER + STAR_LINES:
            self.assertTrue(0 <= by[n]["start"] and by[n]["stop"] <= 100, n)

    def test_tree_parts_stay_on_the_tree(self):
        # The live bounds are hand-tuned, so tiers may leave gaps or touch the ornament by an LED; they must stay on the strand.
        by = {s["n"]: s for s in seg.layout()}
        for n in ["Tree Body", "Tree Ornament", "Tree Left", "Tree Right", "Tree Base"] + TIERS:
            self.assertTrue(100 <= by[n]["start"] and by[n]["stop"] <= 200, n)
        self.assertEqual(leds(seg.layout(), "Tree Body", "Tree Ornament"), list(range(100, 200)))
        self.assertEqual(by["Tree Body"]["start"], 100)             # the body starts at the plug end, the ornament is last
        self.assertEqual(by["Tree Ornament"]["stop"], 200)

    def test_tree_sides(self):
        # The strand's first side (low LEDs, from the plug) is the tree's left; the right side comes after the base.
        by = {s["n"]: s for s in seg.layout()}
        self.assertLess(by["Tree Left"]["stop"], by["Tree Base"]["start"] + 1)
        self.assertLess(by["Tree Base"]["start"], by["Tree Right"]["start"])
        self.assertLess(by["Tree Tier 1 L"]["start"], by["Tree Tier 1 R"]["start"])
        self.assertLess(by["Tree Tier 2 L"]["start"], by["Tree Tier 2 R"]["start"])


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


# WLED built-in effect ids: 2 Breathe, 12 Fade, 38 Aurora, 46 Gradient, 51 Fairytwinkle, 65 Palette, 67 Colorwaves,
# 70-73 Noise 1-4, 74 Colortwinkles, 80 Twinklefox, 88 Candle, 104 Sunrise, 106 Twinkleup.
SOFT_EFFECTS = {2, 12, 38, 46, 51, 65, 67, 70, 71, 72, 73, 74, 80, 88, 104, 106}


class Holiday(unittest.TestCase):
    """The Christmas / Thanksgiving presets and the playlist that rotates through them."""

    def setUp(self):
        self.segs = seg.layout()
        self.names = {s["n"] for s in self.segs}
        self.presets = seg.holiday_presets()
        self.themes = seg.holiday_themes()
        self.lists = seg.holiday_playlists(self.presets, self.themes)
        self.pid, self.pname, self.members = self.lists[0]

    def test_ids_do_not_collide_with_the_other_presets(self):
        ids = [p[0] for p in self.presets] + [p[0] for p in self.lists]
        other = [p[0] for p in seg.test_presets()] + [p[0] for p in seg.playlists()] + [seg.BOOT_PRESET]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & set(other))
        self.assertTrue(all(1 < i < 251 for i in ids))
        for _, name, _ in self.presets:
            self.assertLessEqual(len(name), 32)

    def test_each_preset_lights_exactly_one_strand(self):
        by = {s["n"]: s for s in self.segs}
        for pid, name, spec in self.presets:
            self.assertTrue(set(spec) <= self.names, (pid, set(spec) - self.names))
            strands = {"Star" if by[n]["stop"] <= 100 else "Tree" for n in spec}
            self.assertEqual(strands, {"Star"} if pid < seg.HOLIDAY_TREE_BASE else {"Tree"}, name)
            p = seg.holiday_payload(pid, name, spec, self.segs)
            lit = [g["id"] for g in p["seg"] if g["on"]]
            self.assertEqual(sorted(self.segs[i]["n"] for i in lit), sorted(spec))
            self.assertEqual([g["id"] for g in p["seg"]], [s["id"] for s in self.segs])
            self.assertTrue(p["o"] and p["psave"] == pid)

    def test_values_are_in_range_and_use_built_in_effects(self):
        for pid, name, spec in self.presets:
            for n, g in spec.items():
                # Only soft built-in effects (no custom LS Formula, no blinking, strobing, heartbeat or fast chases), slowly.
                self.assertIn(g["fx"], SOFT_EFFECTS, (name, n, g["fx"]))
                self.assertLessEqual(g["sx"], 120, (name, n))
                self.assertTrue(0 <= g["sx"] <= 255 and 0 <= g["ix"] <= 255 and 0 <= g.get("pal", 0) < 255)
                self.assertEqual(len(g["col"]), 3)
                self.assertTrue(all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in g["col"]))

    def test_both_themes_on_both_strands(self):
        with open(seg.HOLIDAY_FILE) as f:
            import json
            data = json.load(f)
        for key in ("star", "tree"):
            self.assertEqual({p["theme"] for p in data[key]}, {"christmas", "thanksgiving"}, key)

    def test_every_segment_option_is_used(self):
        used = {n for _, _, spec in self.presets for n in spec}
        for n in self.names:
            self.assertIn(n, used, "no holiday preset uses %s" % n)

    def test_playlist_alternates_strands_30s_each(self):
        self.assertEqual(sorted(self.members), sorted(p[0] for p in self.presets))
        self.assertEqual(self.members[:2], [50, 70])                                # star, then tree
        p = seg.playlist_payload(self.pid, self.pname, self.members, seg.HOLIDAY_SECONDS, seg.HOLIDAY_FADE)
        self.assertEqual(p["playlist"]["dur"], [300] * len(self.members))
        self.assertEqual(p["playlist"]["transition"], [seg.HOLIDAY_FADE * 10] * len(self.members))
        self.assertEqual(p["playlist"]["repeat"], 0)                                # loops forever

    def test_theme_playlists(self):
        self.assertEqual([p[1] for p in self.lists], ["Backyard holiday", "Backyard christmas", "Backyard thanksgiving",
                                                      "Backyard UCLA colors"])
        for (pid, name, members), theme in zip(self.lists[1:3], ("christmas", "thanksgiving")):
            self.assertLessEqual(len(name), 32)
            self.assertTrue(members and all(self.themes[i] == theme for i in members), name)
            # every preset of the theme is in it, and the strands alternate while both have patterns left
            self.assertEqual(sorted(members), sorted(i for i in self.themes if self.themes[i] == theme))
            self.assertEqual([m >= seg.HOLIDAY_TREE_BASE for m in members[:6]], [False, True] * 3)

    def test_ucla_presets(self):
        ucla = [p for p in self.presets if self.themes[p[0]] == "ucla"]
        self.assertTrue(3 <= len(ucla) <= 5)
        by = {s["n"]: s for s in self.segs}
        allowed = {(39, 116, 174), (255, 209, 0), (14, 40, 66)}      # UCLA Blue and UCLA Gold (brand.ucla.edu), a dark blue
        for pid, name, spec in ucla:
            self.assertIn("UCLA", name)
            self.assertTrue(all(by[n]["stop"] <= 100 for n in spec), name)               # the star only
            used = {tuple(c) for g in spec.values() for c in g["col"]}
            self.assertTrue(used <= allowed, (name, used - allowed))
            self.assertTrue({(39, 116, 174), (255, 209, 0)} <= used, name)               # both blue and gold
        ucla_list = self.lists[3]
        self.assertEqual(ucla_list[2], sorted(p[0] for p in ucla))
        self.assertTrue(set(ucla_list[2]) <= set(self.members))                          # and they are in the boot playlist
        # spread out, not bunched at the end of the boot playlist
        pos = [self.members.index(p[0]) for p in ucla]
        self.assertLess(pos[0], len(self.members) // 2)
        self.assertGreater(min(b - a for a, b in zip(pos, pos[1:])), 1)

    def test_boot_playlist_starts_with_the_layout_preset(self):
        m = seg.boot_members(self.members)
        self.assertEqual(m[0], seg.BOOT_LAYOUT_PRESET)
        self.assertEqual(m[1:], self.members[1:])                # only the first step is swapped, for a look-alike
        self.assertEqual(len(m), len(self.members))
        self.assertNotIn(seg.BOOT_LAYOUT_PRESET, [p[0] for p in self.presets] + [p[0] for p in self.lists])
        self.assertNotEqual(seg.BOOT_LAYOUT_PRESET, seg.BOOT_PRESET)
        self.assertEqual(self.pid, 43)                           # the mixed playlist is the first one, and the boot one

    def test_unknown_segment_is_rejected(self):
        with self.assertRaises(ValueError):
            seg.holiday_payload(99, "x", {"Star Nope": {"fx": 1, "sx": 1, "ix": 1, "col": []}}, self.segs)


if __name__ == "__main__":
    unittest.main()
