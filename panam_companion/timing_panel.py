"""Serve only the manual timing panel on loopback; no credentials or audio APIs."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ASSETS = {
    "/": ("timing-panel.html", "text/html; charset=utf-8"),
    "/timing-panel.js": ("timing-panel.js", "text/javascript; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected or self.headers.get("Origin") not in (None, f"http://{expected}"):
            self.send_error(403)
            return
        asset = ASSETS.get(self.path)
        if asset is None:
            self.send_error(404)
            return
        content = Path(__file__).with_name(asset[0]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", asset[1])
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "microphone=(), camera=(), display-capture=()")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged port between 1024 and 65535.")
    try:
        with HTTPServer(("127.0.0.1", args.port), Handler) as server:
            print(f"Měřicí panel: http://127.0.0.1:{args.port}/ — bez mikrofonu a bez API. Ctrl+C ukončí pouze panel.", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError:
        print("Panel nelze spustit; zvolte jiný volný port. Žádný cizí proces neukončujte.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
