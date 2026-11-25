#!/usr/bin/env python3
"""Define the backyard board's segments, save test presets that show each one, and build test playlists.

    board_setup/devices/backyard/segments.py [--host HOST] [--step-seconds N] [--ornament-at end|start] [--dry-run]

HOST defaults to wled-backyard.local. Re-runnable. Run it after `provision` (which replaces the filesystem).
Standard library only. --dry-run prints the segments and presets and touches nothing.

The board has two 100-LED strands: "Star" on GPIO 18 (LEDs 0-99) and "Tree" on GPIO 19 (LEDs 100-199). Segments 0 and 1
are those two whole strands. Every other segment is a part of one of them. Edit the numbers below to fine-tune.

STAR  Five lines of 20 LEDs, hung 1 (top) -> 2 (lower right) -> 3 (upper left) -> 4 (upper right) -> 5 (lower left) -> top.
  Each line crosses two others, so it splits into outer leg / middle / outer leg. The outer legs are the star's outline; the
  middles form the inner pentagon, the "interior crossing pieces". A point of the star is the two outer legs that meet at
  a corner, so it is one contiguous run, except the top point, which wraps round the strand's start and end (two segments).
TREE  3 tiers hung down the right side, across the base, up the left side and back to the top (26.7 ft of the strand).
  The 100 LEDs are 3.05 per foot (as on the star), so the shape takes about 81 LEDs and the other 19 are bundled at the
  top as an "ornament" (at the end of the strand; --ornament-at start if they are at the plug end).
  A tier's right and left halves are not contiguous (the strand goes round the tree), so tiers 1 and 2 are two segments each.

Why presets are written the way they are (WLED behaviour, see WLED json.cpp / presets.cpp):
  * A preset saved from the live state ("sb":true) stores the whole segment layout and loading it deletes any segment it does
    not list. Only the boot preset (1) is saved that way: it is what keeps these segments across a reboot.
  * The test presets are "API call" presets ("o":true): saved exactly as sent, they change only the segments they name and
    leave the layout alone. Every test preset names all segments, so the previous one leaves nothing lit.
  * An OFF segment still draws black over its LEDs, which would blank an overlapping segment that is on. Every segment except
    the two whole strands therefore uses blend mode Add (bm 2): black adds nothing.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

LEDS_PER_LINE = 20     # the star: 100 LEDs / 5 lines
STAR_LEG = 8           # LEDs in each outer leg of a line (a pentagram line is 38% / 24% / 38%: 7.6, 4.7, 7.6 of 20)
TREE_START = 100       # the Tree strand's first LED
TREE_LEDS = 100
# Where each corner of the tree falls along the strand, in LEDs (corner numbers as in the tree pattern, 12 = back at the top).
# Feet along the strand x 3.05 LEDs per foot: 0, 2.5, 3.1, 6.0, 7.2, 10.4, 16.4, 19.6, 20.8, 23.6, 24.2, 26.7 ft.
TREE_CORNERS = [0, 8, 9, 18, 22, 32, 50, 60, 63, 72, 74, 81]
#                1  2  3   4   5   6   7   8   9  10  11  12
#   1 top  2 right tier 1 tip  3 right tier 1 notch  4 right tier 2 tip  5 right tier 2 notch  6 right tier 3 tip
#   7 left tier 3 tip  8 left tier 2 notch  9 left tier 2 tip  10 left tier 1 notch  11 left tier 1 tip  12 top again

BLEND_ADD = 2
BOOT_PRESET = 1
EXPECTED_LEDS = 200

COLORS = {
    "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 190, 0), "magenta": (255, 0, 255), "cyan": (0, 255, 255), "orange": (255, 70, 0),
}


def layout(ornament_at="end"):
    """The segments, in id order: [{"id", "n", "start", "stop", "bm"}]. Ids are the position in the list."""
    segs = [("Star", 0, 100, 0), ("Tree", TREE_START, TREE_START + TREE_LEDS, 0)]

    def add(name, start, stop):
        segs.append((name, start, stop, BLEND_ADD))

    legs = STAR_LEG
    line = LEDS_PER_LINE
    # The star's outline: the point at corner k is the end of line k-1 and the start of line k.
    add("Star Top-R", 0, legs)                                                  # line 1 start
    for name, k in (("Star Lower-R", 1), ("Star Upper-L", 2), ("Star Upper-R", 3), ("Star Lower-L", 4)):
        add(name, k * line - legs, k * line + legs)
    add("Star Top-L", 5 * line - legs, 5 * line)                                # line 5 end
    # The inner pentagon: the middle of each line.
    for k in range(5):
        add("Star Inner %d" % (k + 1), k * line + legs, (k + 1) * line - legs)
    for k in range(5):
        add("Star Line %d" % (k + 1), k * line, (k + 1) * line)

    c = TREE_CORNERS
    shape = c[-1]                                    # LEDs the tree shape takes; the rest is the ornament
    if not 0 < shape < TREE_LEDS:
        raise ValueError("the tree shape must end inside its strand")
    if ornament_at not in ("start", "end"):
        raise ValueError("ornament_at must be start or end")
    shift = TREE_LEDS - shape if ornament_at == "start" else 0
    t = TREE_START + shift

    def tree(name, first, last):                     # first, last: corner numbers (1-based); the run between them
        add(name, t + c[first - 1], t + c[last - 1])

    tree("Tree Body", 1, 12)
    if ornament_at == "end":
        add("Tree Ornament", t + shape, TREE_START + TREE_LEDS)
    else:
        add("Tree Ornament", TREE_START, t)
    tree("Tree Right", 1, 6)                         # top to the right tier 3 tip
    tree("Tree Left", 7, 12)                         # left tier 3 tip to the top
    tree("Tree Tier 1 R", 1, 3)
    tree("Tree Tier 1 L", 10, 12)
    tree("Tree Tier 2 R", 3, 5)
    tree("Tree Tier 2 L", 8, 10)
    tree("Tree Tier 3", 5, 8)                        # right notch 2, down, across the base, up to the left notch 2
    tree("Tree Base", 6, 7)                          # the bottom edge only
    return [{"id": i, "n": n, "start": s, "stop": e, "bm": bm} for i, (n, s, e, bm) in enumerate(segs)]


def test_presets():
    """[(id, name, {segment name: colour name})]; every segment not listed is switched off."""
    star_tips = ["Star Top-R", "Star Lower-R", "Star Upper-L", "Star Upper-R", "Star Lower-L", "Star Top-L"]
    inner = ["Star Inner %d" % k for k in range(1, 6)]
    lines = ["Star Line %d" % k for k in range(1, 6)]
    order = ["red", "green", "blue", "yellow", "magenta", "cyan"]

    def each(names):                                  # a different colour for each segment
        return {n: order[i] for i, n in enumerate(names)}

    return [
        (10, "Test star: full", {"Star": "white"}),
        (11, "Test star: outline", each(star_tips)),
        (12, "Test star: inner", each(inner)),
        (13, "Test star: outline+inner", {**{n: "blue" for n in star_tips}, **{n: "orange" for n in inner}}),
        (14, "Test star: lines 1-5", each(lines)),
        (20, "Test tree: full", {"Tree": "white"}),
        (21, "Test tree: body", {"Tree Body": "green"}),
        (22, "Test tree: ornament", {"Tree Ornament": "yellow"}),
        (23, "Test tree: tier 1", {"Tree Tier 1 R": "red", "Tree Tier 1 L": "red"}),
        (24, "Test tree: tier 2", {"Tree Tier 2 R": "green", "Tree Tier 2 L": "green"}),
        (25, "Test tree: tier 3", {"Tree Tier 3": "blue"}),
        (26, "Test tree: tiers+ornament", {"Tree Tier 1 R": "red", "Tree Tier 1 L": "red", "Tree Tier 2 R": "green",
                                           "Tree Tier 2 L": "green", "Tree Tier 3": "blue", "Tree Ornament": "yellow"}),
        (27, "Test tree: right/base/left", {"Tree Right": "red", "Tree Base": "yellow", "Tree Left": "blue"}),
        (29, "Test: star + tree", {"Star": "white", "Tree": "white"}),
    ]


def playlists():
    """[(id, name, [preset ids])]"""
    star = [10, 11, 12, 13, 14]
    tree = [20, 21, 22, 23, 24, 25, 26, 27]
    return [(40, "Backyard test: star", star), (41, "Backyard test: tree", tree),
            (42, "Backyard test: all", star + tree + [29])]


def preset_payload(pid, name, lit, segs):
    """An API-call preset: every segment is named, the ones in `lit` get a solid colour, the rest are off."""
    seg = []
    for s in segs:
        if s["n"] in lit:
            r, g, b = COLORS[lit[s["n"]]]
            seg.append({"id": s["id"], "on": True, "bri": 255, "fx": 0, "col": [[r, g, b], [0, 0, 0], [0, 0, 0]]})
        else:
            seg.append({"id": s["id"], "on": False})
    return {"psave": pid, "o": True, "n": name, "on": True, "transition": 0, "seg": seg}


def playlist_payload(pid, name, members, step_seconds):
    n = len(members)
    return {"playlist": {"ps": members, "dur": [int(step_seconds * 10)] * n, "transition": [0] * n, "repeat": 0, "r": False,
                         "end": 0}, "on": True, "o": True, "psave": pid, "n": name}


class Board:
    def __init__(self, host):
        self.host = host

    def req(self, path, body=None):
        r = urllib.request.Request("http://%s%s" % (self.host, path), method="POST" if body is not None else "GET",
                                   data=None if body is None else json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as f:
            return json.loads(f.read() or b"null")

    def wait(self, what, ok, tries=20):
        for _ in range(tries):
            try:
                if ok(self.req("/presets.json")):
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(1)
        raise SystemExit("%s was not saved as expected" % what)


def apply(host, segs, tests, lists, step_seconds):
    b = Board(host)
    try:
        info = b.req("/json/info")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit("%s did not answer (%s); is the board on the network?" % (host, e))
    if info["leds"]["count"] != EXPECTED_LEDS:
        raise SystemExit("%s has %d LEDs, this layout is for %d (2 x 100)" % (host, info["leds"]["count"], EXPECTED_LEDS))

    # Refuse to overwrite a preset somebody else made.
    mine = {BOOT_PRESET: {"Boot"}}
    mine.update({pid: {n} for pid, n, _ in tests})
    mine.update({pid: {n} for pid, n, _ in lists})
    have = b.req("/presets.json")
    for pid, names in mine.items():
        got = have.get(str(pid), {}).get("n")
        if got is not None and got not in names:
            raise SystemExit('preset %d on the board is "%s", not "%s"; not overwriting it' % (pid, got, sorted(names)[0]))

    # 1. The layout. Segments are created in id order; any extra old segments are deleted.
    current = len(b.req("/json/state")["seg"])
    state = [{"id": s["id"], "n": s["n"], "start": s["start"], "stop": s["stop"], "bm": s["bm"], "on": s["bm"] == 0,
              "sel": s["id"] == 0, "grp": 1, "spc": 0, "of": 0} for s in segs]
    state += [{"id": i, "stop": 0} for i in range(len(segs), current)]
    b.req("/json/state", {"on": True, "seg": state})
    live = [g for g in b.req("/json/state")["seg"] if g.get("len", 0) > 0 or g["stop"] > g["start"]]
    got = [(g["id"], g["n"], g["start"], g["stop"], g["bm"]) for g in live]
    want = [(s["id"], s["n"], s["start"], s["stop"], s["bm"]) for s in segs]
    if got != want:
        raise SystemExit("the board's segments are not what was sent:\n  got  %s\n  want %s" % (got, want))

    # 2. The boot preset keeps the layout over a reboot (this is the only preset saved with segment bounds).
    b.req("/json/state", {"on": True, "psave": BOOT_PRESET, "n": "Boot", "ib": True, "sb": True})
    b.wait("the boot preset", lambda p: [(g.get("id"), g.get("n")) for g in p.get(str(BOOT_PRESET), {}).get("seg", [])
                                            if g.get("stop", 0) > 0] == [(s["id"], s["n"]) for s in segs])

    # 3. Test presets and playlists.
    for pid, name, lit in tests:
        b.req("/json/state", preset_payload(pid, name, lit, segs))
        b.wait("preset %d (%s)" % (pid, name), lambda p, pid=pid, name=name: p.get(str(pid), {}).get("n") == name
               and len(p[str(pid)].get("seg", [])) == len(segs))
    for pid, name, members in lists:
        b.req("/json/state", playlist_payload(pid, name, members, step_seconds))
        b.wait("playlist %d (%s)" % (pid, name), lambda p, pid=pid, name=name, members=members:
               p.get(str(pid), {}).get("n") == name and p[str(pid)].get("playlist", {}).get("ps") == members)
    return lists[-1][0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--host", default="wled-backyard.local")
    ap.add_argument("--step-seconds", type=float, default=4, help="seconds each test preset shows (default 4)")
    ap.add_argument("--ornament-at", choices=("end", "start"), default="end",
                    help="where the tree strand's spare LEDs are: at its end (default) or at the plug end")
    ap.add_argument("--dry-run", action="store_true", help="print the segments and presets; touch nothing")
    a = ap.parse_args(argv)
    if a.step_seconds < 0.5:
        ap.error("--step-seconds must be at least 0.5")
    segs, tests, lists = layout(a.ornament_at), test_presets(), playlists()
    if a.dry_run:
        for s in segs:
            print("%2d  %-16s LEDs %3d-%-3d (%d)%s" % (s["id"], s["n"], s["start"], s["stop"] - 1, s["stop"] - s["start"],
                                                       "" if s["bm"] == 0 else "  blend Add"))
        for pid, name, lit in tests:
            print("preset %2d %-28s %s" % (pid, name, ", ".join("%s=%s" % kv for kv in lit.items())))
        for pid, name, members in lists:
            print("playlist %2d %-26s presets %s" % (pid, name, members))
        return 0
    start = apply(a.host, segs, tests, lists, a.step_seconds)
    b = Board(a.host)
    b.req("/json/state", {"on": True, "ps": start})
    print("done on %s: %d segments, test presets %s, playlists %s; running playlist %d (%g s a step)" % (
        a.host, len(segs), ", ".join(str(p[0]) for p in tests), ", ".join(str(p[0]) for p in lists), start, a.step_seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
