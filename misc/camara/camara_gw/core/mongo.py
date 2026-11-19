"""MongoDB access for the gateway.

`normalize_supi` / `normalize_msisdn` / `serialize` adapted from
~/open5gs-mcp src/tools/_subscriber_util.py (same author).

Open5GS-owned collections (`subscribers`) are read-only here. Gateway state lives
in `camara_*` collections in the same DB.
"""

from __future__ import annotations

import re
from typing import Any

import bson
from pymongo import MongoClient
from pymongo.database import Database

from ..config import Mongo

_IMSI_RE = re.compile(r"^\d{10,15}$")
E164_RE = re.compile(r"^\+[1-9][0-9]{4,14}$")   # CAMARA PhoneNumber pattern


def normalize_supi(raw: str) -> tuple[str, str]:
    """Return (full_supi 'imsi-<digits>', bare_digits). Raises ValueError."""
    s = raw.strip()
    digits = re.sub(r"(?i)^imsi[-:]", "", s).strip()
    if not _IMSI_RE.match(digits):
        raise ValueError(f"Invalid SUPI/IMSI '{raw}': expected 10-15 digits after prefix")
    return f"imsi-{digits}", digits


def msisdn_from_e164(phone: str) -> str:
    """'+99970000001' -> '99970000001' (Open5GS stores MSISDN digits without '+')."""
    return phone[1:] if phone.startswith("+") else phone


def e164_from_msisdn(msisdn: str) -> str:
    return msisdn if msisdn.startswith("+") else f"+{msisdn}"


def serialize(doc: Any) -> Any:
    """Recursively convert BSON types to JSON-safe primitives."""
    if isinstance(doc, dict):
        return {k: serialize(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [serialize(i) for i in doc]
    if isinstance(doc, bson.ObjectId):
        return str(doc)
    if isinstance(doc, bson.Int64):
        return int(doc)
    return doc


class Store:
    """Thin wrapper so tests can inject a mongomock Database."""

    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def connect(cls, cfg: Mongo) -> "Store":
        return cls(MongoClient(cfg.uri, serverSelectionTimeoutMS=3000, tz_aware=True)[cfg.db])

    @property
    def subscribers(self):
        return self.db["subscribers"]

    def ping(self) -> None:
        self.db.command("ping")

    # ── subscriber lookups (read-only) ──────────────────────────────────────
    _PROJ = {"_id": 0, "imsi": 1, "msisdn": 1, "imeisv": 1}

    def subscriber_by_msisdn(self, msisdn: str) -> dict | None:
        return self.subscribers.find_one({"msisdn": msisdn}, self._PROJ)

    def subscriber_by_imsi(self, imsi: str) -> dict | None:
        return self.subscribers.find_one({"imsi": imsi}, self._PROJ)

    def count_by_msisdn(self, msisdn: str) -> int:
        return self.subscribers.count_documents({"msisdn": msisdn})
