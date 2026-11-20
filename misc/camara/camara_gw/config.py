"""Gateway configuration (YAML -> pydantic)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class Listen(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8090


class Mongo(BaseModel):
    uri: str = "mongodb://localhost:27017"
    db: str = "open5gs"


class Sbi(BaseModel):
    nrf: str = "http://127.0.0.10:7777"
    bsf: str = "http://127.0.0.15:7777"
    pcf: str = "http://127.0.0.13:7777"
    udm: str = "http://127.0.0.12:7777"
    timeout: float = 3.0


class Info(BaseModel):
    smf: str = "http://127.0.0.4:9090"
    amf: str = "http://127.0.0.5:9090"
    upf: str = "http://127.0.0.7:9090"
    timeout: float = 3.0


class Client(BaseModel):
    client_id: str
    client_secret: str
    scopes: list[str] = Field(default_factory=list)
    redirect_uris: list[str] = Field(default_factory=list)   # for the authorization-code flow


class Auth(BaseModel):
    issuer: str = "open5gs-camara"
    signing_key: str
    token_ttl: int = 3600
    clients: list[Client] = Field(default_factory=list)
    # Number Verification: access tokens <= 300 s, single use, no refresh (NV 2.1.1)
    nv_token_ttl: int = Field(300, ge=1, le=300)
    code_ttl: int = 60


class Notify(BaseModel):
    # CAMARA requires https sinks; the lab demo sink is plain http on the LAN/loopback.
    allow_http_sink: bool = False
    timeout: float = 5.0
    retries: int = 3


class Qod(BaseModel):
    # Base URL the PCF (via SCP) uses for app-session callbacks (h2c). Must be loopback-reachable.
    callback_base: str = "http://127.0.0.1:8090"
    # How long to wait for the dedicated QoS flow to show up in SMF /pdu-info
    verify_timeout: float = 10.0
    expiry_interval: float = 1.0
    af_app_id: str = "camara-qod"


class Swap(BaseModel):
    enabled: bool = True
    poll_interval: float = 2.0


class Config(BaseModel):
    listen: Listen = Field(default_factory=Listen)
    home_plmn: str = "99970"
    mongo: Mongo = Field(default_factory=Mongo)
    sbi: Sbi = Field(default_factory=Sbi)
    info: Info = Field(default_factory=Info)
    auth: Auth
    lab_endpoints: bool = False
    notify: Notify = Field(default_factory=Notify)
    qod: Qod = Field(default_factory=Qod)
    swap: Swap = Field(default_factory=Swap)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from `path`, $CAMARA_CONFIG, or misc/camara/config.yaml. Fails loudly."""
    p = Path(path or os.environ.get("CAMARA_CONFIG") or DEFAULT_PATH)
    if not p.is_file():
        raise FileNotFoundError(f"gateway config not found: {p} (run setup.sh or copy config.example.yaml)")
    cfg = Config.model_validate(yaml.safe_load(p.read_text()) or {})
    if cfg.auth.signing_key == "CHANGE_ME" or any(c.client_secret == "CHANGE_ME" for c in cfg.auth.clients):
        raise ValueError(f"{p}: replace CHANGE_ME secrets (setup.sh generates them)")
    return cfg
