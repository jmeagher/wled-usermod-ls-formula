#!/usr/bin/env python3
"""Define the backyard board's segments, save test presets that show each one, and build test playlists.

    board_setup/devices/backyard/segments.py [--host HOST] [--step-seconds N] [--dry-run]

HOST defaults to wled-backyard.local. Re-runnable. Run it after `provision` (which replaces the filesystem).
Standard library only. --dry-run prints the segments and presets and touches nothing.

The board has two 100-LED strands: "Star" on GPIO 18 (LEDs 0-99) and "Tree" on GPIO 19 (LEDs 100-199). Segments 0 and 1
are those two whole strands. Every other segment is a part of one of them. Edit the numbers below to fine-tune.

STAR  Five lines of about 20 LEDs, hung 1 (top) -> 2 (lower left) -> 3 (upper right) -> 4 (upper left) -> 5 (lower right) -> top.
  Each line crosses two others, so it splits into outer leg / middle / outer leg. The outer legs are the star's outline; the
  middles form the inner pentagon, the "interior crossing pieces". A point of the star is the two outer legs that meet at
  a corner, so it is one contiguous run, except the top point, which wraps round the strand's start and end (two segments).
TREE  3 tiers hung down the strand's first side (the tree's left, seen from the front), across the base, up the right side and
  back to the top. The shape takes 82 LEDs and the other 18 are bundled at the top as an "ornament" (end of the strand).
  A tier's left and right halves are not contiguous (the strand goes round the tree), so tiers 1 and 2 are two segments each.
  The bounds are hand-tuned on the live board and may leave small gaps or a 1 LED overlap; the tests only check what must hold.

Why presets are written the way they are (WLED behaviour, see WLED json.cpp / presets.cpp):
  * A preset saved from the live state ("sb":true) stores the whole segment layout and loading it deletes any segment it does
    not list. Only the boot preset (1) and the boot layout preset (2) are saved that way. The board starts the holiday
    playlist at power-up, whose first step is preset 2, so the segments come back with it.
  * The test presets are "API call" presets ("o":true): saved exactly as sent, they change only the segments they name and
    leave the layout alone. Every test preset names all segments, so the previous one leaves nothing lit.
  * An OFF segment still draws black over its LEDs, which would blank an overlapping segment that is on. Every segment except
    the two whole strands therefore uses blend mode Add (bm 2): black adds nothing.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TREE_START = 100       # the Tree strand's first LED
TREE_LEDS = 100
BLEND_ADD = 2
BOOT_PRESET = 1
BOOT_LAYOUT_PRESET = 2   # the first step of the boot playlist: saved from the live state, so it also restores the segments
EXPECTED_LEDS = 200

COLORS = {
    "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 190, 0), "magenta": (255, 0, 255), "cyan": (0, 255, 255), "orange": (255, 70, 0),
}


# The segments as tuned on the live board (fetched 2026-11-26), in id order: (name, first LED, one past the last LED).
# Segments 0 and 1 are the whole strands; every later segment is a part of one of them and blends with Add.
STAR_SEGMENTS = [
    # The outline: the outer legs of the five lines. Hung 1 (top) -> 2 (lower left) -> 3 (upper right) -> 4 (upper left) ->
    # 5 (lower right) -> top. The top point wraps round the strand's ends, so it is two segments.
    ("Star Top-Left", 0, 8), ("Star Lower-Left", 13, 29), ("Star Upper-Right", 33, 49),
    ("Star Upper-Left", 53, 69), ("Star Lower-Right", 74, 88), ("Star Top-Right", 94, 100),
    # The inner pentagon: the middle of each line.
    ("Star Inner 1", 8, 13), ("Star Inner 2", 29, 33), ("Star Inner 3", 49, 53), ("Star Inner 4", 69, 74),
    ("Star Inner 5", 88, 94),
    # The five lines, in hanging order.
    ("Star Line 1", 0, 21), ("Star Line 2", 21, 41), ("Star Line 3", 41, 61), ("Star Line 4", 61, 81),
    ("Star Line 5", 81, 100),
]
TREE_SEGMENTS = [
    ("Tree Body", 100, 182), ("Tree Ornament", 182, 200),
    # Hung down the strand's first side (the tree's LEFT, seen from the front), across the base, up the other side.
    ("Tree Left", 100, 133), ("Tree Right", 151, 182),
    ("Tree Tier 1 L", 100, 108), ("Tree Tier 1 R", 173, 183),
    ("Tree Tier 2 L", 110, 123), ("Tree Tier 2 R", 161, 173),
    ("Tree Tier 3", 123, 161), ("Tree Base", 133, 150),
]


def layout():
    """The segments, in id order: [{"id", "n", "start", "stop", "bm"}]. Ids are the position in the list."""
    segs = [("Star", 0, 100, 0), ("Tree", TREE_START, TREE_START + TREE_LEDS, 0)]
    segs += [(n, a, b, BLEND_ADD) for n, a, b in STAR_SEGMENTS + TREE_SEGMENTS]
    return [{"id": i, "n": n, "start": s, "stop": e, "bm": bm} for i, (n, s, e, bm) in enumerate(segs)]


def test_presets():
    """[(id, name, {segment name: colour name})]; every segment not listed is switched off."""
    star_tips = ["Star Top-Left", "Star Lower-Left", "Star Upper-Right", "Star Upper-Left", "Star Lower-Right", "Star Top-Right"]
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


HOLIDAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holiday.json")
HOLIDAY_STAR_BASE = 50      # star holiday presets are 50.., tree ones 70..
HOLIDAY_TREE_BASE = 70
HOLIDAY_PLAYLISTS = [          # (id, name, theme or None for both)
    (43, "Backyard holiday", None),
    (44, "Backyard christmas", "christmas"),
    (45, "Backyard thanksgiving", "thanksgiving"),
]
HOLIDAY_SECONDS = 30        # how long each pattern shows
HOLIDAY_FADE = 2            # seconds the crossfade between two patterns takes


def holiday_presets(path=HOLIDAY_FILE):
    """[(id, name, {segment name: {fx, sx, ix, pal, col}})] for the star (ids 50..) and the tree (ids 70..), from holiday.json.

    The file is {"star": [...], "tree": [...]}; each entry is {"name", "theme", "seg": {...}} and uses only WLED's built-in
    effects and palettes. A preset names every segment (the ones it does not list are off), so only one strand is lit."""
    with open(path) as f:
        data = json.load(f)
    out = []
    for key, base in (("star", HOLIDAY_STAR_BASE), ("tree", HOLIDAY_TREE_BASE)):
        for i, p in enumerate(data[key]):
            out.append((base + i, p["name"], p["seg"]))
    return out


def holiday_themes(path=HOLIDAY_FILE):
    """{preset id: "christmas" | "thanksgiving"}, in the same order and numbering as holiday_presets()."""
    with open(path) as f:
        data = json.load(f)
    return {base + i: p["theme"] for key, base in (("star", HOLIDAY_STAR_BASE), ("tree", HOLIDAY_TREE_BASE))
            for i, p in enumerate(data[key])}


def holiday_playlists(presets, themes):
    """[(id, name, [preset ids])]: the mixed playlist, then Christmas only, then Thanksgiving only.

    In each one the star and tree presets alternate, so one strand is lit at a time and the display swaps strands at every
    step. If one strand has more presets than the other, the extras are played after the pairs run out."""
    out = []
    for pid, name, theme in HOLIDAY_PLAYLISTS:
        ids = [p[0] for p in presets if theme is None or themes[p[0]] == theme]
        star = [i for i in ids if i < HOLIDAY_TREE_BASE]
        tree = [i for i in ids if i >= HOLIDAY_TREE_BASE]
        members = []
        for i in range(max(len(star), len(tree))):
            members += star[i:i + 1] + tree[i:i + 1]
        out.append((pid, name, members))
    return out


def boot_members(members):
    """The mixed playlist as the board plays it: its first pattern is played through BOOT_LAYOUT_PRESET instead.

    WLED will not store segments and a playlist in one preset (a preset with a "playlist" key keeps only the playlist), and a
    preset cannot start a playlist either. But a preset saved from the live state does store the whole segment layout, and
    loading it re-creates the segments. So BOOT_LAYOUT_PRESET looks exactly like the first pattern and carries the layout;
    after a reboot the board starts this playlist and its first step brings the segments back."""
    return [BOOT_LAYOUT_PRESET] + list(members[1:])


def holiday_payload(pid, name, spec, segs):
    """An API-call preset like preset_payload, but each named segment runs a built-in effect; the rest are off."""
    unknown = set(spec) - {s["n"] for s in segs}
    if unknown:
        raise ValueError("preset %d (%s) names unknown segments %s" % (pid, name, sorted(unknown)))
    seg = []
    for s in segs:
        if s["n"] in spec:
            g = spec[s["n"]]
            seg.append({"id": s["id"], "on": True, "bri": 255, "fx": g["fx"], "sx": g["sx"], "ix": g["ix"],
                        "pal": g.get("pal", 0), "col": g["col"]})
        else:
            seg.append({"id": s["id"], "on": False})
    return {"psave": pid, "o": True, "n": name, "on": True, "transition": HOLIDAY_FADE * 10, "seg": seg}


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


def playlist_payload(pid, name, members, step_seconds, fade_seconds=0):
    n = len(members)
    return {"playlist": {"ps": members, "dur": [int(step_seconds * 10)] * n, "transition": [int(fade_seconds * 10)] * n,
                         "repeat": 0, "r": False,
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


def apply(host, segs, tests, lists, step_seconds, holiday=(), holiday_lists=()):
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
    mine.update({pid: {n} for pid, n, _ in holiday})
    mine.update({pid: {n} for pid, n, _ in holiday_lists})
    if holiday_lists:
        mine[BOOT_LAYOUT_PRESET] = {"Boot layout"}
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
    # 4. The holiday presets and their playlist: 30 s a pattern, strands alternating, one lit at a time.
    for pid, name, spec in holiday:
        b.req("/json/state", holiday_payload(pid, name, spec, segs))
        b.wait("preset %d (%s)" % (pid, name), lambda p, pid=pid, name=name: p.get(str(pid), {}).get("n") == name
               and len(p[str(pid)].get("seg", [])) == len(segs))
    for pid, name, members in holiday_lists[1:]:
        b.req("/json/state", playlist_payload(pid, name, members, HOLIDAY_SECONDS, HOLIDAY_FADE))
        b.wait("playlist %d (%s)" % (pid, name), lambda p, pid=pid, name=name, members=members:
               p.get(str(pid), {}).get("n") == name and p[str(pid)].get("playlist", {}).get("ps") == members)
    if holiday_lists:
        # 5. The boot playlist. Show the first pattern, save the live state (layout included) as BOOT_LAYOUT_PRESET, then
        #    the playlist that starts with it, and make that playlist what the board starts at power-up.
        pid, name, members = holiday_lists[0]
        b.req("/json/state", {"on": True, "ps": members[0], "transition": 0})
        for _ in range(10):
            time.sleep(1)
            if b.req("/json/state").get("ps") == members[0]:
                break
        else:
            raise SystemExit("preset %d did not load" % members[0])
        b.req("/json/state", {"on": True, "psave": BOOT_LAYOUT_PRESET, "n": "Boot layout", "ib": True, "sb": True})
        b.wait("the boot layout preset", lambda p: [(g.get("id"), g.get("n"), g.get("start"), g.get("stop")) for g in
                                                     p.get(str(BOOT_LAYOUT_PRESET), {}).get("seg", []) if g.get("stop", 0) > 0]
               == [(s["id"], s["n"], s["start"], s["stop"]) for s in segs])
        b.req("/json/state", playlist_payload(pid, name, boot_members(members), HOLIDAY_SECONDS, HOLIDAY_FADE))
        b.wait("playlist %d (%s)" % (pid, name), lambda p: p.get(str(pid), {}).get("playlist", {}).get("ps") == boot_members(members))
        b.req("/json/cfg", {"def": {"ps": pid, "on": True}})
        if b.req("/json/cfg")["def"]["ps"] != pid:
            raise SystemExit("the board did not take playlist %d as its boot preset" % pid)
        return pid
    return lists[-1][0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--host", default="wled-backyard.local")
    ap.add_argument("--step-seconds", type=float, default=4, help="seconds each test preset shows (default 4)")
    ap.add_argument("--dry-run", action="store_true", help="print the segments and presets; touch nothing")
    a = ap.parse_args(argv)
    if a.step_seconds < 0.5:
        ap.error("--step-seconds must be at least 0.5")
    segs, tests, lists = layout(), test_presets(), playlists()
    holiday = holiday_presets()
    hlists = holiday_playlists(holiday, holiday_themes())
    if a.dry_run:
        for s in segs:
            print("%2d  %-16s LEDs %3d-%-3d (%d)%s" % (s["id"], s["n"], s["start"], s["stop"] - 1, s["stop"] - s["start"],
                                                       "" if s["bm"] == 0 else "  blend Add"))
        for pid, name, lit in tests:
            print("preset %2d %-28s %s" % (pid, name, ", ".join("%s=%s" % kv for kv in lit.items())))
        for pid, name, members in lists:
            print("playlist %2d %-26s presets %s" % (pid, name, members))
        for pid, name, spec in holiday:
            print("holiday %2d %-28s %s" % (pid, name, ", ".join("%s=fx%d" % (n, g["fx"]) for n, g in spec.items())))
        for pid, name, members in hlists:
            print("playlist %2d %-26s presets %s (%d s each, %d s fade)" % (pid, name, members, HOLIDAY_SECONDS, HOLIDAY_FADE))
        print("boot: playlist %d, starting with preset %d (the layout) in place of %d" % (hlists[0][0], BOOT_LAYOUT_PRESET,
                                                                                   hlists[0][2][0]))
        return 0
    start = apply(a.host, segs, tests, lists, a.step_seconds, holiday, hlists)
    b = Board(a.host)
    b.req("/json/state", {"on": True, "ps": start})
    print("done on %s: %d segments, test presets %s, playlists %s plus holiday presets %s and playlists %s; running playlist %d (the boot playlist)" % (
        a.host, len(segs), ", ".join(str(p[0]) for p in tests), ", ".join(str(p[0]) for p in lists),
        ", ".join(str(p[0]) for p in holiday), ", ".join(str(p[0]) for p in hlists), start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
