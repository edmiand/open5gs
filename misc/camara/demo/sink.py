#!/usr/bin/env python3
"""Tiny CloudEvents sink for demos: prints every event it receives.

  python3 demo/sink.py [--port 9099] [--token SECRET]
Point CAMARA `sink` at http://<host>:9099/events (lab: gateway config notify.allow_http_sink=true).
"""
import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--token", help="require 'Authorization: Bearer <token>' (sinkCredential ACCESSTOKEN)")
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            if a.token and self.headers.get("authorization") != f"Bearer {a.token}":
                self.send_response(401); self.end_headers(); return
            try:
                ev = json.loads(body)
                d = ev.get("data", {})
                print(f"\n[{datetime.now():%H:%M:%S}] 🔔 {ev.get('type')}\n{json.dumps(d, indent=2)}", flush=True)
            except ValueError:
                print(f"\n[{datetime.now():%H:%M:%S}] non-JSON body: {body[:200]!r}", flush=True)
            self.send_response(204); self.end_headers()

        def log_message(self, *args):
            pass

    print(f"CloudEvents sink listening on http://{a.bind}:{a.port}/events", flush=True)
    ThreadingHTTPServer((a.bind, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
