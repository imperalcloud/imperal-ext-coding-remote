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
    "coding-remote", version="1.2.1",
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
        "it is live, route it to Telegram/panel/both/off, switch its mode "
        "(default/plan/autopilot), and send it an instruction while it keeps "
        "running on your machine."
    ),
    system_prompt=(
        "Coding Remote module — remote control for the user's terminal "
        "Webbee Code (coding agent) session.\n\n"
        "get_status shows whether a coding session is currently live and how "
        "it is routed (mirror = where output is echoed, steer = where replies "
        "can drive it back). set_mode changes routing: 'tg' mirrors+steers via "
        "Telegram, 'panel' mirrors to the panel only (no remote steer), 'both' "
        "does both with Telegram steer, 'off' turns remote control off. "
        "send_instruction pushes a new instruction into the live session — it "
        "only works while a session is active; if there is none, tell the user "
        "to start one from their terminal. stop_session stops the running "
        "turn (like pressing Esc in the terminal): the session and its "
        "conversation survive, only the current run is cancelled — use it "
        "when the user asks to stop/cancel/interrupt what the coding session "
        "is doing right now; if nothing is running it reports an honest "
        "no-op. set_coding_mode switches the LIVE session's consent mode — "
        "'default' asks before risky actions, 'plan' is read-only planning, "
        "'autopilot' auto-approves (the terminal asks its local user to "
        "confirm an autopilot switch; downgrades apply right away). It is a "
        "different thing from set_mode (routing) — never mix the two — and "
        "it only works while a session is active."
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
