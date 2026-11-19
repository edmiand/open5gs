"""Run the gateway: python -m camara_gw [--config PATH]"""

import argparse

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="camara_gw")
    ap.add_argument("--config", help="path to config.yaml (default: $CAMARA_CONFIG or misc/camara/config.yaml)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    uvicorn.run(create_app(cfg), host=cfg.listen.host, port=cfg.listen.port, proxy_headers=False)


if __name__ == "__main__":
    main()
