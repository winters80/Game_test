"""
Browser play (``python main.py --web``) with real pictures.

textual-serve streams the game to xterm.js in the browser, which can only draw
pictures out of coloured text cells (about 38 x 22 pixels in the sidebar). So
the browser page gets its own picture panel beside the terminal instead:

* every page load gets an art id; its websocket is ``/ws/<id>``, and the game
  process started for that tab gets ``SB_WEB_ART=<id>`` in its environment;
* the game (``ui/tui_app.GameApp.set_art``) writes the current picture to
  ``saves/web_art/<id>.json`` instead of drawing it in the sidebar;
* the page polls ``/art/<id>`` and shows ``/art-file/<id>/<version>`` at full
  resolution. Only files under ``assets/`` are ever served.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "saves" / "web_art"
TEMPLATES = Path(__file__).resolve().parent / "web"
ENV_KEY = "SB_WEB_ART"
_ID = re.compile(r"^[0-9a-f]{32}$")

logger = logging.getLogger(__name__)


def state_file(art_id: str) -> Path | None:
    return ART_DIR / f"{art_id}.json" if _ID.match(art_id or "") else None


def publish(art_id: str, path: str, caption: str = "") -> bool:
    """Called by the game: record the picture this tab should show."""
    f = state_file(art_id)
    if f is None:
        return False
    try:
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        old = {}
    data = {"path": str(path or ""), "caption": caption or "", "v": int(old.get("v", 0)) + 1}
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(f)
        return True
    except OSError:
        logger.info("Could not publish web art", exc_info=True)
        return False


def read_state(art_id: str) -> dict:
    f = state_file(art_id)
    if f is None or not f.exists():
        return {"path": "", "caption": "", "v": 0}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"path": "", "caption": "", "v": 0}


def safe_asset(path: str) -> Path | None:
    """The picture's real path, only if it's an image file under assets/ or assets_local/."""
    from ui import media
    if not path:
        return None
    try:
        p = Path(path).resolve()
        bases = [Path(d).resolve() for d in media.asset_dirs()]
    except OSError:
        return None
    if not any(b in p.parents for b in bases) or p.suffix.lower() not in media.IMAGE_EXT or not p.is_file():
        return None
    return p


def make_server(command: str, host: str = "localhost", port: int = 8000, title: str = "SYSTEM BREAKER"):
    import aiohttp_jinja2
    from aiohttp import web
    from textual_serve.app_service import AppService
    from textual_serve.server import Server

    class ArtAppService(AppService):
        art_id = ""

        def _build_environment(self, width: int = 80, height: int = 24) -> dict[str, str]:
            env = super()._build_environment(width=width, height=height)
            if self.art_id:
                env[ENV_KEY] = self.art_id
            return env

    class ArtServer(Server):
        async def _make_app(self) -> web.Application:
            app = await super()._make_app()
            app.router.add_get("/ws/{art_id}", self.handle_art_websocket, name="art_websocket")
            app.router.add_get("/art/{art_id}", self.handle_art_state, name="art_state")
            app.router.add_get("/art-file/{art_id}/{v}", self.handle_art_file, name="art_file")
            return app

        @aiohttp_jinja2.template("app_index.html")
        async def handle_index(self, request: web.Request) -> dict[str, Any]:
            # Server.handle_index is wrapped by its own template decorator; take the context.
            context = await Server.handle_index.__wrapped__(self, request)
            art_id = uuid.uuid4().hex
            ws = context["app_websocket_url"]
            context["app_websocket_url"] = f"{ws.rstrip('/')}/{art_id}"
            context["art_url"] = f"{self.public_url}/art/{art_id}"
            return context

        async def handle_art_state(self, request: web.Request) -> web.Response:
            art_id = request.match_info["art_id"]
            if state_file(art_id) is None:
                raise web.HTTPNotFound()
            st = read_state(art_id)
            src = f"{self.public_url}/art-file/{art_id}/{st.get('v', 0)}" if safe_asset(st.get("path", "")) else ""
            return web.json_response({"v": st.get("v", 0), "src": src, "caption": st.get("caption", "")},
                                     headers={"Cache-Control": "no-store"})

        async def handle_art_file(self, request: web.Request) -> web.StreamResponse:
            art_id = request.match_info["art_id"]
            if state_file(art_id) is None:
                raise web.HTTPNotFound()
            p = safe_asset(read_state(art_id).get("path", ""))
            if p is None:
                raise web.HTTPNotFound()
            return web.FileResponse(p, headers={"Cache-Control": "max-age=3600"})

        async def handle_art_websocket(self, request: web.Request) -> web.WebSocketResponse:
            """Server.handle_websocket, with this tab's art id passed to the game."""
            from textual_serve.server import log, to_int
            art_id = request.match_info["art_id"]
            if state_file(art_id) is None:
                raise web.HTTPNotFound()
            websocket = web.WebSocketResponse(heartbeat=15)
            width = to_int(request.query.get("width", "80"), 80)
            height = to_int(request.query.get("height", "24"), 24)
            app_service = None
            try:
                await websocket.prepare(request)
                app_service = ArtAppService(
                    self.command,
                    write_bytes=websocket.send_bytes,
                    write_str=websocket.send_str,
                    close=websocket.close,
                    download_manager=self.download_manager,
                    debug=self.debug,
                )
                app_service.art_id = art_id
                await app_service.start(width, height)
                try:
                    await self._process_messages(websocket, app_service)
                finally:
                    await app_service.stop()
            except asyncio.CancelledError:
                await websocket.close()
            except Exception as error:                     # noqa: BLE001 - mirrors textual-serve
                log.exception(error)
            finally:
                if app_service is not None:
                    await app_service.stop()
                f = state_file(art_id)
                if f is not None:
                    f.unlink(missing_ok=True)
            return websocket

    return ArtServer(command, host=host, port=port, title=title, templates_path=str(TEMPLATES))
