#!/usr/bin/env python3
"""Lab provisioning actions for the SIM/Device swap demo (equivalent to WebUI edits).

  lab_swap.py show
  lab_swap.py add-spare --from 999700000000001 --imsi 999700000000002
        Clone a subscriber (profile + SIM keys) under a new IMSI with no MSISDN — the "new SIM".
  lab_swap.py move-msisdn 99970000001 --to 999700000000002
        SIM swap: move the number to another IMSI.
  lab_swap.py set-imeisv 999700000000001 3528820000000101
        Device swap shortcut: overwrite the stored IMEISV. The real path is changing `imeiSv` in the
        UERANSIM UE config and re-registering; the UDR rewrites `imeisv` on every registration.

Writes Open5GS `subscribers` — lab use only. Fails loudly.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys

from pymongo import MongoClient

IMSI_RE = re.compile(r"^\d{10,15}$")


def die(msg: str) -> None:
    print(f"lab_swap: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mongo", default="mongodb://localhost:27017")
    ap.add_argument("--db", default="open5gs")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("show")
    a = sp.add_parser("add-spare"); a.add_argument("--from", dest="src", required=True); a.add_argument("--imsi", required=True)
    m = sp.add_parser("move-msisdn"); m.add_argument("msisdn"); m.add_argument("--to", required=True)
    d = sp.add_parser("set-imeisv"); d.add_argument("imsi"); d.add_argument("imeisv")
    args = ap.parse_args()

    col = MongoClient(args.mongo, serverSelectionTimeoutMS=3000)[args.db]["subscribers"]

    if args.cmd == "show":
        for s in col.find({}, {"_id": 0, "imsi": 1, "msisdn": 1, "imeisv": 1}).sort("imsi"):
            print(f"{s['imsi']}  msisdn={s.get('msisdn') or []}  imeisv={s.get('imeisv') or '-'}")
        return

    if args.cmd == "add-spare":
        if not IMSI_RE.match(args.imsi):
            die(f"bad IMSI {args.imsi}")
        if col.find_one({"imsi": args.imsi}):
            die(f"IMSI {args.imsi} already exists")
        src = col.find_one({"imsi": args.src})
        if not src:
            die(f"source IMSI {args.src} not found")
        doc = copy.deepcopy(src)
        doc.pop("_id", None)
        for sl in doc.get("slice", []):          # fresh sub-document ids
            sl.pop("_id", None)
            for se in sl.get("session", []):
                se.pop("_id", None)
        doc.update({"imsi": args.imsi, "msisdn": [], "imeisv": [], "purge_flag": []})
        doc.get("security", {}).update({"sqn": 0})
        col.insert_one(doc)
        print(f"created spare SIM {args.imsi} (cloned from {args.src}, no MSISDN)")
        return

    if args.cmd == "move-msisdn":
        holders = list(col.find({"msisdn": args.msisdn}, {"imsi": 1}))
        if len(holders) > 1:
            die(f"MSISDN {args.msisdn} is on several IMSIs: {[h['imsi'] for h in holders]}")
        if not col.find_one({"imsi": args.to}):
            die(f"target IMSI {args.to} not found")
        if holders and holders[0]["imsi"] == args.to:
            die(f"MSISDN {args.msisdn} is already on {args.to}")
        if holders:
            col.update_one({"_id": holders[0]["_id"]}, {"$pull": {"msisdn": args.msisdn}})
        col.update_one({"imsi": args.to}, {"$addToSet": {"msisdn": args.msisdn}})
        print(f"moved {args.msisdn}: {holders[0]['imsi'] if holders else '(unassigned)'} -> {args.to}")
        return

    if args.cmd == "set-imeisv":
        if not re.fullmatch(r"\d{16}", args.imeisv):
            die("IMEISV must be 16 digits")
        r = col.update_one({"imsi": args.imsi}, {"$set": {"imeisv": args.imeisv}})
        if r.matched_count != 1:
            die(f"IMSI {args.imsi} not found")
        print(f"{args.imsi} imeisv -> {args.imeisv}")


if __name__ == "__main__":
    main()
