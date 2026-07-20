"""Coding Remote · Shared state — remote-control panel + steer for the
terminal Webbee Code session (mirror it to Telegram or the panel, steer it
back)."""
from __future__ import annotations

import logging
import os

import httpx

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension, ActionResult  # noqa: F401 (re-exported)

log = logging.getLogger("coding-remote")

AUTH_GW = os.getenv("IMPERAL_GATEWAY_URL", "http://104.224.88.155:8085")
AUTH_SERVICE_TOKEN = os.getenv("AUTH_SERVICE_TOKEN", "")

ext = Extension(
    "coding-remote", version="1.3.1",
    # Federal-rigor scope surface (I-SCOPES-DECLARED-NOT-WILDCARD): this app
    # reads its own remote-control state and writes routing/mode/instructions
    # through the gateway control plane — declare that surface so the kernel
    # enforces tool.required_scopes ⊆ declared instead of a wildcard fallback
    # (a system app auto-installed for everyone must never ship wildcard).
    capabilities=["coding_remote:read", "coding_remote:write"],
    display_name="Coding Remote",
    description=(
        "Control your terminal Webbee Code session remotely — mirror it to "
        "Telegram or the panel and steer it back."
    ),
    icon="icon.svg",
    actions_explicit=True,
    system=True,  # Imperal-owned platform app — always accessible, no explicit install.
)
# This extension IS the control panel — it stays visible in the sidebar
# (no hidden_in_sidebar), unlike notifications/web-search which are
# informational-only system apps hidden from the tile grid.

chat = ChatExtension(
    ext,
    "tool_coding_remote_chat",
    description=(
        "Remote control for the terminal Webbee Code session: check whether "
        "it is live or parked, route it to Telegram/panel/both/off, switch "
        "its mode (default/plan/autopilot), send it an instruction while it "
        "keeps running on your machine, and reply to a pending approval. "
        "(This module only remote-controls the terminal session — server "
        "operations over SSH are also available directly in this chat via "
        "Connections, no terminal needed.)"
    ),
    system_prompt=(
        "Coding Remote module — remote control for the user's terminal "
        "Webbee Code (coding agent) session.\n\n"
        "get_status shows whether a coding session is currently live "
        "(terminal online), parked (a session/marathon exists but the "
        "terminal is offline — 'running' true, 'active' false), or idle (no "
        "session at all); how it is routed (mirror = where output is "
        "echoed, steer = where replies can drive it back); the applied "
        "consent mode; and any pending_consent waiting on a reply. "
        "set_mode changes routing: 'tg' mirrors+steers via Telegram, "
        "'panel' mirrors+steers via the panel, 'both' does both via "
        "Telegram AND the panel, 'off' turns remote control off — steer "
        "reaches the session whether it is live or parked. "
        "send_instruction pushes a new instruction into the session — it "
        "works whenever one is running (live or parked); if none exists at "
        "all, tell the user to start one from their terminal (server work "
        "over SSH does NOT need a coding session: with Connections it is "
        "available directly in this chat). stop_session stops the running "
        "turn (like pressing Esc in the terminal): the session and its "
        "conversation survive, only the current run is cancelled — use it "
        "when the user asks to stop/cancel/interrupt what the coding session "
        "is doing right now; if nothing is running it reports an honest "
        "no-op. set_coding_mode switches the session's consent mode — "
        "'default' asks before risky actions, 'plan' is read-only planning, "
        "'autopilot' auto-approves (the terminal asks its local user to "
        "confirm an autopilot switch; downgrades apply right away). It is a "
        "different thing from set_mode (routing) — never mix the two — and "
        "it only works while a session is running. reply_consent answers a "
        "pending_consent from get_status (e.g. 'approve'/'decline', or any "
        "free-form reply) — the text is relayed as-is to the session, never "
        "normalized here; if none is waiting it reports an honest no-op, "
        "and sending a fresh instruction instead will decline it."
    ),
)


def _user_id(ctx) -> str:
    # ALWAYS the acting user — these tools never accept a foreign user_id.
    return ctx.user.imperal_id


def _headers() -> dict:
    return {"X-Service-Token": AUTH_SERVICE_TOKEN}


async def gw_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.get(f"{AUTH_GW}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


async def gw_put(path: str, body: dict) -> tuple[dict | None, str | None]:
    """Returns (json, None) on success, (None, readable_error) on 4xx/5xx."""
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.put(f"{AUTH_GW}{path}", json=body, headers=_headers())
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or r.text[:300]
            except Exception:
                detail = r.text[:300] or "(empty body)"
            return None, f"HTTP {r.status_code}: {detail}"
        return r.json(), None


async def gw_post(path: str, body: dict) -> tuple[dict | None, str | None]:
    """Returns (json, None) on success, (None, readable_error) on 4xx/5xx."""
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(f"{AUTH_GW}{path}", json=body, headers=_headers())
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or r.text[:300]
            except Exception:
                detail = r.text[:300] or "(empty body)"
            return None, f"HTTP {r.status_code}: {detail}"
        return r.json(), None


@ext.health_check
async def health(ctx) -> dict:
    return {"status": "ok", "version": ext.version}


def _safe_err(e: Exception) -> str:
    """Never let an internal gateway URL/IP leak into a chat-facing error.

    httpx exceptions (and anything else that happens to embed a URL) get
    collapsed to a generic label; everything else is passed through as-is."""
    s = str(e)
    return "internal error" if "http" in s.lower() and "://" in s else s
