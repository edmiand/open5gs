#!/usr/bin/env python3
"""Extract CAMARA test-definition scenarios: (file, tag, title, expected status, expected code).

  python3 tools/parse_features.py specs/*/test_definitions/*.feature  > scenarios.json
"""
import json
import re
import sys

TAG = re.compile(r"^\s*@(\S+)")
SCEN = re.compile(r"^\s*Scenario(?: Outline)?:\s*(.+)")
STATUS = re.compile(r'response (?:status )?code is "?(\d{3})"?')
CODE = re.compile(r'response property "\$\.code" is "([^"]+)"')


def parse(path):
    out, tags, cur = [], [], None
    for line in open(path, encoding="utf-8"):
        m = TAG.match(line)
        if m and cur is None or (m and SCEN.match(line) is None and line.strip().startswith("@")):
            tags = [t.lstrip("@") for t in line.split() if t.startswith("@")]
            if cur:
                out.append(cur); cur = None
            continue
        m = SCEN.match(line)
        if m:
            if cur:
                out.append(cur)
            cur = {"file": path.split("/")[-1], "spec": path.split("/")[1], "tag": tags[0] if tags else "",
                   "title": m.group(1).strip(), "status": None, "codes": []}
            tags = []
            continue
        if cur:
            s = STATUS.search(line)
            if s and cur["status"] is None:
                cur["status"] = int(s.group(1))
            c = CODE.search(line)
            if c:
                cur["codes"].append(c.group(1))
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    allsc = [s for p in sys.argv[1:] for s in parse(p)]
    json.dump(allsc, sys.stdout, indent=1)
