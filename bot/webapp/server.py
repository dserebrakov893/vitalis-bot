"""Веб-сервер Mini App чекина: страница и JSON API.

Каждый запрос несёт initData из Telegram (заголовок Authorization: tma <initData>). Сервер проверяет подпись
токеном бота и свежесть, затем роль: админ из ADMIN_IDS или действующий проверяющий. Без этого — 401/403.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from aiohttp import web
from aiogram.utils.web_app import safe_parse_webapp_init_data
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.formatting import event_end, fmt_datetime, fmt_time
from bot.services import checkin

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
INIT_DATA_MAX_AGE = 24 * 3600

SM = web.AppKey("sm", async_sessionmaker)
TOKEN = web.AppKey("token", str)
ADMINS = web.AppKey("admins", Callable)


def _user_id(request: web.Request) -> int:
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        raise web.HTTPUnauthorized(text='{"error":"no_init_data"}', content_type="application/json")
    try:
        data = safe_parse_webapp_init_data(request.app[TOKEN], header[4:])
    except ValueError:
        raise web.HTTPUnauthorized(text='{"error":"bad_signature"}', content_type="application/json") from None
    if data.user is None or time.time() - data.auth_date.timestamp() > INIT_DATA_MAX_AGE:
        raise web.HTTPUnauthorized(text='{"error":"expired"}', content_type="application/json")
    return data.user.id


async def _context(request: web.Request):
    uid = _user_id(request)
    event = await checkin.access_event(request.app[SM], uid, request.app[ADMINS]())
    if event is None:
        log.info("checkin access denied for user %s", uid)
        raise web.HTTPForbidden(text='{"error":"no_access"}', content_type="application/json")
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    return uid, event, body if isinstance(body, dict) else {}


def _json(data: dict, status: int = 200) -> web.Response:
    resp = web.json_response(data, status=status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def _event_info(sm, event) -> dict:
    came, total = await checkin.counts(sm, event.id)
    return {
        "event": {"title": event.title, "when": f"{fmt_datetime(event.starts_at)}–{fmt_time(event_end(event))}"},
        "came": came,
        "total": total,
    }


async def me(request: web.Request) -> web.Response:
    _, event, _ = await _context(request)
    return _json(await _event_info(request.app[SM], event))


async def scan(request: web.Request) -> web.Response:
    uid, event, body = await _context(request)
    result = await checkin.scan(request.app[SM], event, str(body.get("payload", ""))[:200], uid)
    log.info("checkin scan by %s: %s", uid, result["status"])
    return _json({**result, **await _event_info(request.app[SM], event)})


async def search(request: web.Request) -> web.Response:
    _, event, body = await _context(request)
    return _json({"items": await checkin.search(request.app[SM], event, str(body.get("q", ""))[:100])})


def _reg_id(body: dict) -> int:
    try:
        return int(body.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text='{"error":"bad_id"}', content_type="application/json") from None


async def mark(request: web.Request) -> web.Response:
    uid, event, body = await _context(request)
    result = await checkin.mark(request.app[SM], event, _reg_id(body), uid)
    log.info("checkin manual mark by %s: %s", uid, result["status"])
    return _json({**result, **await _event_info(request.app[SM], event)})


async def unmark(request: web.Request) -> web.Response:
    uid, event, body = await _context(request)
    result = await checkin.unmark(request.app[SM], event, _reg_id(body))
    log.info("checkin unmark by %s", uid)
    return _json({**result, **await _event_info(request.app[SM], event)})


async def page(request: web.Request) -> web.Response:
    resp = web.FileResponse(STATIC / "checkin.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_webapp(sm: async_sessionmaker, bot_token: str, admin_ids: Callable[[], set[int]]) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app[SM] = sm
    app[TOKEN] = bot_token
    app[ADMINS] = admin_ids
    app.router.add_get("/", health)
    app.router.add_get("/checkin", page)
    app.router.add_get("/checkin/", page)
    app.router.add_post("/api/checkin/me", me)
    app.router.add_post("/api/checkin/scan", scan)
    app.router.add_post("/api/checkin/search", search)
    app.router.add_post("/api/checkin/mark", mark)
    app.router.add_post("/api/checkin/unmark", unmark)
    return app


async def start_webapp(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)  # в access-логах был бы initData
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("checkin web app listening on port %s", port)
    return runner
