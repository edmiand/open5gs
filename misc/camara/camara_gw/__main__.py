"""Run the gateway: python -m camara_gw [--config PATH]

Served by Hypercorn so one port accepts both HTTP/1.1 (API consumers, curl) and
h2c prior-knowledge (Open5GS PCF/SCP callbacks). Uvicorn has no h2c support.
"""

import argparse
import asyncio

from hypercorn.asyncio import serve
from hypercorn.config import Config as HcConfig

from .app import create_app
from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="camara_gw")
    ap.add_argument("--config", help="path to config.yaml (default: $CAMARA_CONFIG or misc/camara/config.yaml)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    hc = HcConfig()
    hc.bind = [f"{cfg.listen.host}:{cfg.listen.port}"]
    hc.accesslog = "-"
    hc.errorlog = "-"
    asyncio.run(serve(create_app(cfg), hc))


if __name__ == "__main__":
    main()
