#!/usr/bin/env python3
"""Ensure flowless PCC-rule templates for every QoD profile 5QI on lab subscribers.

The Open5GS PCF only authorises an app-session (Npcf_PolicyAuthorization) whose
qosReference maps to a 5QI that the subscriber's session already has a PCC rule
for (npcf-handler.c "No PCC Rule in DB"). Rules without flows are skipped at PDU
session setup (pcf/sbi-path.c), so they act purely as 5QI/ARP templates; the
flows and bitrates come from the QoD request.

Idempotent. Only adds missing rules; never removes or edits existing ones.

  .venv/bin/python tools/seed_pcc_rules.py --imsi 999700000000001 [--imsi ...] [--dnn internet] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent.parent
PROFILES = HERE / "camara_gw" / "profiles.yaml"


def wanted_rules() -> dict[int, dict]:
    """5QI -> PCC rule template (highest-priority ARP among profiles sharing the 5QI)."""
    out: dict[int, dict] = {}
    for p in yaml.safe_load(PROFILES.read_text())["profiles"]:
        q, prio = int(p["fiveqi"]), int(p.get("arp_priority", 8))
        if q in out and out[q]["qos"]["arp"]["priority_level"] <= prio:
            continue
        out[q] = {
            "flow": [],
            "qos": {
                "index": q,
                "arp": {"priority_level": prio, "pre_emption_capability": 1, "pre_emption_vulnerability": 1},
            },
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--imsi", action="append", required=True)
    ap.add_argument("--dnn", default="internet")
    ap.add_argument("--mongo", default="mongodb://localhost:27017")
    ap.add_argument("--db", default="open5gs")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    col = MongoClient(a.mongo, serverSelectionTimeoutMS=3000)[a.db]["subscribers"]
    rules = wanted_rules()
    rc = 0
    for imsi in a.imsi:
        doc = col.find_one({"imsi": imsi})
        if not doc:
            print(f"ERROR: subscriber {imsi} not found", file=sys.stderr)
            rc = 1
            continue
        changed = False
        found_dnn = False
        for sl in doc.get("slice", []):
            for sess in sl.get("session", []):
                if sess.get("name") != a.dnn:
                    continue
                found_dnn = True
                have = {int(r.get("qos", {}).get("index", 0)) for r in sess.get("pcc_rule", [])}
                for q, rule in sorted(rules.items()):
                    if q in have:
                        print(f"{imsi}/{a.dnn}: 5QI {q} PCC rule present")
                        continue
                    sess.setdefault("pcc_rule", []).append(rule)
                    changed = True
                    print(f"{imsi}/{a.dnn}: + 5QI {q} PCC rule (ARP {rule['qos']['arp']['priority_level']})")
        if not found_dnn:
            print(f"ERROR: {imsi} has no session named '{a.dnn}'", file=sys.stderr)
            rc = 1
            continue
        if changed and not a.dry_run:
            res = col.update_one({"_id": doc["_id"]}, {"$set": {"slice": doc["slice"]}})
            if res.modified_count != 1:
                print(f"ERROR: update of {imsi} did not apply", file=sys.stderr)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
