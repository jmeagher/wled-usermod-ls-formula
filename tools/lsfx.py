#!/usr/bin/env python3
"""Upload, validate and activate LS Formula effects on a WLED device.

Uses only the standard library and WLED's normal JSON API (POST /json/state).

  lsfx.py HOST info
  lsfx.py HOST validate SLOT FILE
  lsfx.py HOST push SLOT FILE [--name NAME] [--activate] [--seg N]
  lsfx.py HOST pull SLOT [FILE]
  lsfx.py HOST clear SLOT

HOST is an address or mDNS name (for example wled-1234.local or 192.168.1.50).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

TIMEOUT_S = 10


class LsfxError(Exception):
    pass


def _request(host, method, body=None):
    url = "http://%s/json/state" % host
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise LsfxError("device returned HTTP %d" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise LsfxError("cannot reach %s: %s" % (host, getattr(e, "reason", e)))
    except ValueError:
        raise LsfxError("device sent invalid JSON")


def get_state(host):
    state = _request(host, "GET")
    if "lsf" not in state:
        raise LsfxError("no 'lsf' section: the ls_formula usermod is not in this firmware")
    return state


def post_lsf(host, payload):
    """POST a command and return the 'lsf' section of the resulting state."""
    state = _request(host, "POST", {"v": True, "lsf": payload})
    if "lsf" not in state:
        raise LsfxError("no 'lsf' section in reply: the ls_formula usermod is not in this firmware")
    return state["lsf"]


def read_source(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return text


def explain(src, last):
    """Format a compile error with the offending source line."""
    lines = src.split("\n")
    line = last.get("line", 0)
    if line < 1:   # not tied to a source line (for example an over-budget rejection)
        return "error: %s" % last.get("err", "unknown")
    out = ["error: %s (line %d)" % (last.get("err", "unknown"), line)]
    if line <= len(lines):
        text = lines[line - 1]
        out.append("  " + (text if len(text) <= 100 else text[:97] + "..."))   # keep long one-line formulas readable
    return "\n".join(out)


def check_slot(slot, limits):
    n = limits.get("slots", 4)
    if not 0 <= slot < n:
        raise LsfxError("slot must be 0..%d" % (n - 1))


def cmd_info(host, args):
    lsf = get_state(host)["lsf"]
    for s in lsf["slots"]:
        status = "ok" if s["ok"] else "empty/inactive"
        extra = ""
        if s.get("ok"):
            extra = "  %d instrs, cost %d%s, frame avg %d us / worst %d us, est %d us, fits %d LEDs" % (
                s["instrs"], s["cost"], ", uses prev" if s.get("prev") else "", s.get("avgUs", 0), s.get("worstUs", 0),
                s.get("estUs", 0), s.get("maxPx", 0))
        if s.get("err"):
            extra += "  [%s]" % s["err"]
        print("slot %d  fx=%-3s %-22s %s%s" % (s["slot"], s["fx"], s["name"], status, extra))
    lim = lsf["limits"]
    print("limits: %d source bytes, %d instructions, %d variables; budget %d us per frame, strip has %d LEDs" % (
        lim["src"], lim["instrs"], lim["locals"], lim.get("budgetUs", 0), lim.get("pixels", 0)))
    return 0


def cmd_validate(host, args):
    src = read_source(args.file)
    limits = get_state(host)["lsf"]["limits"]
    check_slot(args.slot, limits)
    last = post_lsf(host, {"slot": args.slot, "src": src, "dry": True})["last"]
    if not last["ok"]:
        print(explain(src, last), file=sys.stderr)
        return 1
    print("valid: %d instructions, cost %d, est %d us per frame, fits %d LEDs" % (
        last["instrs"], last["cost"], last.get("estUs", 0), last.get("maxPx", 0)))
    return 0


def cmd_push(host, args):
    src = read_source(args.file)
    state = get_state(host)
    check_slot(args.slot, state["lsf"]["limits"])
    payload = {"slot": args.slot, "src": src}
    if args.name:
        payload["name"] = args.name
    lsf = post_lsf(host, payload)
    if not lsf["last"]["ok"]:
        print(explain(src, lsf["last"]), file=sys.stderr)
        return 1
    slot = lsf["slots"][args.slot]
    print("installed in slot %d as '%s' (effect id %s), %d instructions, est %d us per frame, fits %d LEDs" % (
        args.slot, slot["name"], slot["fx"], slot["instrs"], slot.get("estUs", 0), slot.get("maxPx", 0)))
    if args.activate:
        fx = slot["fx"]
        if fx == 255:
            raise LsfxError("effect is not registered with WLED (id 255)")
        _request(host, "POST", {"seg": [{"id": args.seg, "fx": fx}]})
        print("segment %d now runs it" % args.seg)
    return 0


def cmd_pull(host, args):
    lsf = get_state(host)["lsf"]
    check_slot(args.slot, lsf["limits"])
    src = lsf["slots"][args.slot]["src"]
    if args.file:
        with open(args.file, "w", encoding="utf-8") as f:
            f.write(src)
    else:
        sys.stdout.write(src if src.endswith("\n") or not src else src + "\n")
    return 0


def cmd_clear(host, args):
    check_slot(args.slot, get_state(host)["lsf"]["limits"])
    post_lsf(host, {"slot": args.slot, "clear": True})
    print("slot %d cleared" % args.slot)
    return 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    v = sub.add_parser("validate"); v.add_argument("slot", type=int); v.add_argument("file"); v.set_defaults(fn=cmd_validate)
    u = sub.add_parser("push"); u.add_argument("slot", type=int); u.add_argument("file")
    u.add_argument("--name"); u.add_argument("--activate", action="store_true"); u.add_argument("--seg", type=int, default=0)
    u.set_defaults(fn=cmd_push)
    g = sub.add_parser("pull"); g.add_argument("slot", type=int); g.add_argument("file", nargs="?"); g.set_defaults(fn=cmd_pull)
    c = sub.add_parser("clear"); c.add_argument("slot", type=int); c.set_defaults(fn=cmd_clear)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args.host, args)
    except LsfxError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
