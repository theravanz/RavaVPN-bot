"""
Клиент связи бота с сайтом (web API) с outbox-очередью.

Ключевая гарантия: недоступность сайта никогда не ломает работу бота.
Все события сначала применяются локально, затем кладутся в outbox
(bot.db) и доставляются на сайт фоновой задачей с ретраями. События
идемпотентны на сайте по eventId, поэтому повторная доставка безопасна.

Режимы бота (bot_mode в settings):
- autonomous — сайт не используется: события копятся в outbox до
  переключения режима, сетевых вызовов нет;
- online     — события отправляются сразу, при ошибке — в outbox;
- hybrid     — то же, что online.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import select

from config import settings
from database import OutboxEvent, SessionMaker, get_settings

log = logging.getLogger(__name__)

RETRIES = 2
TIMEOUT_SEC = 5
OUTBOX_INTERVAL_SEC = 30


async def _post(path: str, payload: dict) -> bool:
    """Один POST на сайт с секретом. True — доставлено (2xx)."""
    url = f"{settings.web_api_url}{path}"
    headers = {
        "x-bot-secret": settings.telegram_auth_secret,
        "Content-Type": "application/json",
    }
    for attempt in range(RETRIES):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC)) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        return True
                    # 422 — сайт осознанно отказал (плохой код, самоприглашение,
                    # недостаточно баланса): повторять бессмысленно
                    if resp.status == 422:
                        log.warning("web refused %s: %s", path, body)
                        return True
                    log.warning("web %s -> %s (attempt %d)", path, resp.status, attempt + 1)
        except Exception as e:
            log.warning("web %s unreachable (attempt %d): %s", path, attempt + 1, e)
        await asyncio.sleep(1)
    return False


def new_event_id() -> str:
    return uuid.uuid4().hex


async def enqueue_event(kind: str, payload: dict, event_id: str | None = None) -> str:
    """Сохранить событие в outbox (доставка фоновым воркером)."""
    eid = event_id or new_event_id()
    async with SessionMaker() as session:
        session.add(OutboxEvent(
            id=eid,
            kind=kind,
            payload_json=json.dumps({"eventId": eid, "kind": kind, **payload}, ensure_ascii=False),
        ))
        await session.commit()
    return eid


async def web_post(kind: str, path: str, payload: dict) -> str:
    """
    Отправить событие на сайт; если не удалось (или режим autonomous) —
    в outbox. Возвращает eventId. Никогда не бросает исключений.
    """
    eid = new_event_id()
    s = await get_settings()
    if getattr(s, "bot_mode", "hybrid") == "autonomous":
        return await enqueue_event(kind, payload, eid)

    ok = await _post(path, {"eventId": eid, "kind": kind, **payload})
    if not ok:
        await enqueue_event(kind, payload, eid)
    return eid


async def _flush_outbox() -> int:
    async with SessionMaker() as session:
        events = (await session.scalars(
            select(OutboxEvent).where(OutboxEvent.delivered_at.is_(None)).limit(50)
        )).all()

    delivered = 0
    for ev in events:
        try:
            payload = json.loads(ev.payload_json)
        except Exception:
            async with SessionMaker() as session:
                ev2 = await session.get(OutboxEvent, ev.id)
                if ev2:
                    ev2.delivered_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await session.commit()
            continue

        if await _post("/api/referrals/event", payload):
            async with SessionMaker() as session:
                ev2 = await session.get(OutboxEvent, ev.id)
                if ev2:
                    ev2.delivered_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await session.commit()
            delivered += 1
    return delivered


async def outbox_worker():
    """Фоновая задача: периодически доставляет накопившиеся события."""
    while True:
        try:
            n = await _flush_outbox()
            if n:
                log.info("outbox: delivered %d events", n)
        except Exception as e:
            log.warning("outbox worker error: %s", e)
        await asyncio.sleep(OUTBOX_INTERVAL_SEC)


async def fetch_web_stats(tg_id: int) -> dict | None:
    """GET /api/referrals/stats — best-effort, None при недоступности."""
    url = f"{settings.web_api_url}/api/referrals/stats?tgId={tg_id}"
    headers = {"x-bot-secret": settings.telegram_auth_secret}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        log.warning("web stats unavailable: %s", e)
    return None
