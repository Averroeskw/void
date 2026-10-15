"""After a package install + `void setup` + starting the server: is it the right version, and does it know how to update
itself? python3 pkgcheck.py deb|rpm|arch      Prints PASS/FAIL lines; exits with the failure count."""
import json
import os
import sys
import time
import urllib.request

want = sys.argv[1]
ver = open("/usr/share/void/app/VERSION").read().strip()
s = {}
for _ in range(90):                              # (the server may still be starting)
    try:
        tok = json.load(open(os.path.expanduser("~/.local/share/void/runtime.json")))["token"]
        s = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:47777/api/state",
                                                                    headers={"X-Void-Token": tok}), timeout=5))
        break
    except (OSError, ValueError, KeyError):
        time.sleep(1)
u = s.get("update") or {}
checks = [("server answers with the packaged version", s.get("version") == ver and ver != "0.0.0", s.get("version")),
          ("knows it's a package install", u.get("how") == "package", u.get("how")),
          (f"knows its package manager ({want})", u.get("pkg") == want, u.get("pkg"))]
fails = 0
for name, ok, got in checks:
    print(("PASS" if ok else "FAIL") + f"  {name}  ({got})")
    fails += not ok
sys.exit(fails)
