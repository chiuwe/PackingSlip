#!/usr/bin/env python3
"""
slip_ui.py - local browser UI for packing_slip.py.

Starts a small web server on this machine and opens it in the browser. Drop an
invoice PDF on the page to fill the packing slip table, edit anything (ship-to,
items, quantities, qty packed, sign-off), then export the PDF. Settings edits the
line items to ignore (config.json). Standard library only.

  python3 slip_ui.py
  python3 slip_ui.py --port 8766 --no-browser

Invoices are read locally and never leave this machine.
"""

import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote, urlparse

import packing_slip

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "slip_ui.html")
MAX_UPLOAD = 50 * 1024 * 1024


def number(v):
    """A quantity from the page: int when whole, else float; raises ValueError on junk."""
    n = float(str(v).replace(",", "").strip())
    return int(n) if n == int(n) else n


def slip_from_page(body):
    """The page's state -> packing_slip_pdf() input, checking what the page can get wrong."""
    items = []
    for i, it in enumerate(body.get("items") or [], 1):
        name = str(it.get("item") or "").strip()
        if not name:
            continue
        try:
            qty = number(it.get("qty"))
        except ValueError:
            raise ValueError("line %d (%s): quantity ordered %r is not a number" % (i, name, it.get("qty")))
        items.append((name, qty, str(it.get("packed") or "").strip()))
    if not items:
        raise ValueError("the slip has no items")
    lines = lambda k: [s.strip() for s in str(body.get(k) or "").splitlines() if s.strip()]
    values = {k: str(v).strip() for k, v in (body.get("values") or {}).items()}
    values.update({"Qty Packed %d" % i: p for i, (_, _, p) in enumerate(items, 1)})
    text = lambda k: str(body.get(k) or "").strip()
    return {"number": text("number"), "po": text("po"), "date": text("date"),
            "seller": lines("seller"), "ship_to": lines("ship_to"), "items": [(n, q) for n, q, _ in items], "values": values}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file is larger than %d MB" % (MAX_UPLOAD // 2 ** 20))
        return self.rfile.read(n)

    def do_GET(self):
        if urlparse(self.path).path == "/api/config":
            try:
                return self.send_json({"config": packing_slip.load_config()})
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if urlparse(self.path).path != "/":
            return self.send(404, "not found", "text/plain")
        with open(PAGE, "rb") as f:
            self.send(200, f.read(), "text/html; charset=utf-8")

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/parse":
                inv = packing_slip.parse_invoice(self.body())
                self.send_json({"invoice": dict(inv, items=[{"item": s, "qty": q} for s, q in inv["items"]],
                                                ignored=[{"item": s, "qty": q} for s, q in inv["ignored"]])})
            elif url.path == "/api/config":
                self.send_json({"config": packing_slip.save_config(json.loads(self.body()))})
            elif url.path == "/api/pdf":
                inv = slip_from_page(json.loads(self.body()))
                name = "Packing Slip %s.pdf" % (inv["number"] or "draft")
                self.send(200, packing_slip.packing_slip_pdf(inv), "application/pdf",
                          {"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name)})
            else:
                self.send(404, "not found", "text/plain")
        except Exception as e:  # shown on the page, server keeps going
            self.send_json({"error": str(e) or e.__class__.__name__}, 400)

    def log_message(self, fmt, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="Browser UI for packing_slip.")
    ap.add_argument("--port", type=int, default=8766, help="port to listen on (default 8766)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)  # localhost only
    url = "http://127.0.0.1:%d/" % server.server_port
    print("Packing Slip UI at %s  (Ctrl+C to stop)" % url)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
