"""Allowlisted loopback HTTP presentation/control; no controller construction."""
import json
from pathlib import Path

from aiohttp import web

from .web_runtime import CommandRejected

HOST = "127.0.0.1"
PORT = 8767
ORIGIN = f"http://{HOST}:{PORT}"
MAX_REQUEST = 1024
ASSETS = {"/": ("index.html", "text/html"),
          "/admin": ("admin.html", "text/html"),
          "/static/panam-selfie.png": ("static/panam-selfie.png", "image/png"),
          "/voice-deck.css": ("voice-deck.css", "text/css"),
          "/voice-deck.js": ("voice-deck.js", "text/javascript")}
HEADERS = {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(), camera=(), display-capture=(), autoplay=()",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def error(code, status):
    return web.json_response({"error_code": code}, status=status)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def create_app(runtime):
    # Load only known local assets, once, before audio work can start.
    directory = Path(__file__).with_name("web")
    assets = {route: (directory.joinpath(name).read_bytes(), mime) for route, (name, mime) in ASSETS.items()}

    @web.middleware
    async def boundary(request, handler):
        try:
            if request.headers.getall("Host", []) != [f"{HOST}:{PORT}"]:
                response = error("HOST_REJECTED", 403)
            elif request.method not in {"GET", "HEAD", "POST"}:
                response = error("METHOD_REJECTED", 405)
            elif request.headers.getall("Origin", []) not in ([], [ORIGIN]):
                response = error("ORIGIN_REJECTED", 403)
            elif request.method == "POST" and request.headers.getall("Origin", []) != [ORIGIN]:
                response = error("ORIGIN_REQUIRED", 403)
            elif request.method == "POST" and request.headers.getall("X-Panam-Command", []) != ["1"]:
                response = error("COMMAND_HEADER_REQUIRED", 403)
            elif request.method == "POST" and request.content_type != "application/json":
                response = error("JSON_REQUIRED", 415)
            elif request.content_length is not None and request.content_length > MAX_REQUEST:
                response = error("REQUEST_TOO_LARGE", 413)
            else:
                response = await handler(request)
        except CommandRejected as rejected:
            response = error(rejected.code, rejected.status)
        except web.HTTPRequestEntityTooLarge:
            response = error("REQUEST_TOO_LARGE", 413)
        except web.HTTPException as rejected:
            response = error("METHOD_REJECTED" if rejected.status == 405 else "NOT_FOUND", rejected.status)
        except Exception:
            response = error("REQUEST_FAILED", 500)
        response.headers.update(HEADERS)
        return response

    app = web.Application(middlewares=[boundary], client_max_size=MAX_REQUEST)

    async def asset(request):
        data, mime = assets[request.path]
        return web.Response(body=data, content_type=mime, charset="utf-8" if mime.startswith("text/") else None)

    async def status(_request):
        return web.json_response(runtime.snapshot())

    async def command(request):
        try:
            payload = json.loads(await request.read(), object_pairs_hook=unique_object)
        except web.HTTPRequestEntityTooLarge:
            raise
        except Exception:
            return error("INVALID_JSON", 400)
        return web.json_response(runtime.admit(payload), status=202)

    for route in assets:
        app.router.add_get(route, asset)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/commands", command)
    return app
