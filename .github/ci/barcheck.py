"""Checks `window.py --selftest-bar` reports: did the window dock on each edge, and did the page become the notch?
    python3 barcheck.py REPORTS.txt LAYER(True|False) EDGE...      Prints PASS/FAIL lines; exits with the failure count."""
import re
import sys

path, want_layer, edges = sys.argv[1], sys.argv[2], sys.argv[3:]
lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
fails = 0
for edge in edges:
    m = [re.search(r"selftest-bar: (\w+) x=(-?\d+) y=(-?\d+) w=(\d+) h=(\d+) area=(-?\d+),(-?\d+),(\d+),(\d+) "
                   r"layer=(\w+) page=(.*)", ln) for ln in lines if ln.startswith(f"selftest-bar: {edge} ")]
    m = m[-1] if m else None
    ok = False
    if m:
        x, y, w, h, ax, ay, aw, ah = map(int, m.groups()[1:9])
        cls = m.group(11).split()
        if m.group(10) == "True":                # layer-shell: x, y are margins inside the monitor
            ax = ay = 0
        where = {"top": abs(x - (ax + (aw - w) / 2)) <= 2 and y == ay,
                 "bottom": abs(x - (ax + (aw - w) / 2)) <= 2 and abs(y + h - (ay + ah)) <= 1,
                 "left": x == ax and abs(y - (ay + (ah - h) / 2)) <= 2,
                 "right": abs(x + w - (ax + aw)) <= 1 and abs(y - (ay + (ah - h) / 2)) <= 2}[edge]
        ok = where and m.group(10) == want_layer and "bar" in cls and f"e-{edge}" in cls
    kind = "layer-shell" if want_layer == "True" else "X11"
    print(("PASS" if ok else "FAIL") + f"  top bar docks on the {edge} edge ({kind})  {m.group(0) if m else 'no report'}")
    fails += not ok
sys.exit(fails)
