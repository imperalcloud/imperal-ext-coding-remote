"""Coding Remote · chat tools — FACTS out, narrator phrases (ICNLI)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app import ActionResult, chat, gw_get, gw_put, gw_post, _user_id, _safe_err
from models import CodingRemote, CodingTab

log = logging.getLogger("coding-remote")

# Shared Field description for the optional per-tab targeting param every
# write tool gains (T2, W4c 2026-07-20) — one wording everywhere, so the
# brain and the panel see the exact same contract regardless of which tool
# it is calling.
_SESSION_ID_DESC = ("target a specific tab — see get_status's tabs list; "
                     "omitted = the most recently active tab")


def _utc_now_iso() -> str:
    """UTC ISO-8601 timestamp, second precision (e.g. 2026-07-17T02:45:47Z).

    Used ONLY as ``CodingRemote.checked_at`` — the moment THIS status read
    was fetched, never a fabricated terminal-session last-activity time (the
    gateway doesn't expose one today; see CodingRemote docstring)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EmptyParams(BaseModel):
    """No parameters needed."""
    pass


class SetParams(BaseModel):
    """Route the terminal coding session. mode: tg | panel | both | off."""
    mode: str = Field(description="tg | panel | both | off")


class SendParams(BaseModel):
    """Send an instruction to the live coding session."""
    text: str = Field(description="the instruction to run in the coding session")
    session_id: str = Field(default="", description=_SESSION_ID_DESC)


class StopParams(BaseModel):
    """Stop the running terminal coding session (like pressing Esc)."""
    session_id: str = Field(default="", description=_SESSION_ID_DESC)


class CodingModeParams(BaseModel):
    """Set the terminal coding session's mode. mode: default | plan | autopilot."""
    mode: str = Field(description="default | plan | autopilot")
    session_id: str = Field(default="", description=_SESSION_ID_DESC)


class ConsentParams(BaseModel):
    """Reply to a pending approval waiting on the terminal coding session."""
    text: str = Field(description="the reply to relay for the pending approval, e.g. 'approve' or 'decline'")
    session_id: str = Field(default="", description=_SESSION_ID_DESC)


_MODES = {
    "tg": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    "panel": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    "both": {"enabled": True, "mirror": ["telegram", "panel"], "steer": ["telegram", "panel"]},
    "off": {"enabled": False, "mirror": [], "steer": []},
}

# Coding-session CONSENT modes (set_coding_mode) — a DIFFERENT axis from the
# _MODES routing vocabulary above (set_mode = mirror/steer routing).
_CODING_MODES = ("default", "plan", "autopilot")

# Origin-honest steer (v2): core surface vocab the gateway validates against.
# "panel" is the ext/panel-side alias for the core "web-panel" surface.
_SURFACES = {"telegram", "web-panel", "discord", "api"}
_SURFACE_ALIASES = {"panel": "web-panel"}


def _turn_surface(ctx) -> str | None:
    """Best-effort read of the acting turn's surface, normalized to core vocab.

    The SDK Context (5.9.x) does NOT expose the dispatch surface — the kernel
    threads it per-turn (kctx.surface) but stops short of the extension
    Context (``_metadata`` carries only history/skeleton_data/connected_emails/
    _context). So today this returns ``None`` and the field is OMITTED from
    the steer body (the gateway then applies its back-compat default,
    web-panel). The reads are tolerant on purpose: the moment the kernel
    starts threading the surface (``ctx.surface`` or
    ``ctx._metadata["surface"]``), origin becomes honest with zero ext
    changes. Unknown/non-string values are omitted, never guessed — the
    endpoint 422s junk instead of silently mislabeling an origin."""
    raw = getattr(ctx, "surface", None)
    if not isinstance(raw, str) or not raw.strip():
        meta = getattr(ctx, "_metadata", None) or {}
        raw = meta.get("surface") if isinstance(meta, dict) else None
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    s = _SURFACE_ALIASES.get(s, s)
    return s if s in _SURFACES else None


def _row_to_tab(row: dict) -> CodingTab:
    """One gateway inventory row -> CodingTab. The gateway's key is
    ``applied_mode`` (matching CodingRemote's own gateway-facing read); the
    row-to-model mapping renames it to ``mode`` to match CodingRemote.mode —
    the freshest-session twin of this same fact — so both fields share one
    name across the ext regardless of which one a caller is looking at."""
    return CodingTab(
        session_id=row.get("session_id"), slot=row.get("slot", ""), kind=row.get("kind", ""),
        label=row.get("label"), terminal_online=bool(row.get("terminal_online", False)),
        mode=row.get("applied_mode"), requested_mode=row.get("requested_mode"),
        pending_consent=row.get("pending_consent"), started=row.get("started"),
    )


async def _fetch_tabs(uid: str) -> list[CodingTab]:
    """Best-effort per-tab inventory (T2, W4c 2026-07-20) — GET
    .../{uid}/sessions. Fail-soft BY DESIGN: any error (network hiccup, an
    older gateway without the route, a malformed row) yields an empty list
    rather than failing the whole get_status read — the Tabs section is
    additive, never load-bearing for the rest of the status card. Never
    raises."""
    try:
        d = await gw_get(f"/v1/internal/coding-remote/{uid}/sessions")
        return [_row_to_tab(row) for row in d.get("sessions", [])]
    except Exception as e:
        log.warning("coding-remote tabs fetch failed for %s: %s", uid, _safe_err(e))
        return []


@chat.function("get_status", action_type="read",
    description="Show remote-control status of the terminal coding session (active? mirror/steer routing).",
    data_model=CodingRemote)
async def fn_status(ctx, params: EmptyParams) -> ActionResult:
    """Show the acting user's coding-remote status.

    No params — always reads the caller's own session (``ctx.user.imperal_id``).
    Returns FACTS: whether a terminal Webbee Code session is currently live
    (``active``, ``session_id``), whether one exists at all even parked with
    the terminal offline (``running`` — WIDER than ``active``, see
    :class:`CodingRemote`), the effective routing (``enabled``, ``mirror`` —
    where session output is echoed, ``steer`` — where replies can drive it
    back), the applied consent ``mode`` (``None`` until the terminal reports
    one), any ``pending_consent`` waiting on a reply, ``last_seen`` for the
    terminal pointer, and ``tabs`` (T2, W4c 2026-07-20) — every RUNNING
    session the user owns (see :class:`CodingTab`), so the brain/panel can
    name a specific tab to the write tools below via their optional
    ``session_id`` param instead of always hitting the freshest one.
    """
    try:
        uid = _user_id(ctx)
        d = await gw_get(f"/v1/internal/coding-remote/{uid}")
        st = d.get("state", {})
        active = bool(d.get("active", False))
        running = bool(d.get("running", active))
        state_word = "live" if active else ("parked — terminal offline" if running else "idle")
        tabs = await _fetch_tabs(uid)
        return ActionResult.success(
            data=CodingRemote(active=active, session_id=d.get("session_id"),
                              enabled=st.get("enabled", False), mirror=st.get("mirror", []), steer=st.get("steer", []),
                              checked_at=_utc_now_iso(), running=running, mode=d.get("applied_mode"),
                              last_seen=d.get("last_seen"), pending_consent=d.get("pending_consent"),
                              requested_mode=d.get("requested_mode"), tabs=tabs),
            summary=f"coding session {state_word}; remote {'on' if st.get('enabled') else 'off'}")
    except Exception as e:
        return ActionResult.error(f"Failed to read coding-remote status: {_safe_err(e)}", code="CODING_REMOTE_STATUS_FAILED")


@chat.function("set_mode", action_type="write", event="coding-remote.route_changed",
    description="Route the terminal coding session: tg (Telegram), panel, both, or off.",
    data_model=CodingRemote)
async def fn_set(ctx, params: SetParams) -> ActionResult:
    """Change the acting user's coding-remote routing.

    Always writes for ``ctx.user.imperal_id`` — never a caller-supplied user.
    ``mode`` must be one of ``tg`` (mirror+steer via Telegram), ``panel``
    (mirror+steer via the panel — the panel can drive the session back, not
    just watch it), ``both`` (mirror to both, steer via Telegram AND the
    panel), or ``off`` (turn remote control off). Returns the post-write
    FACTS in the same shape as get_status.
    """
    try:
        uid = _user_id(ctx)
        body = _MODES.get(params.mode.strip().lower())
        if body is None:
            return ActionResult.error("mode must be one of: tg, panel, both, off", code="CODING_REMOTE_BAD_MODE")
        res, err = await gw_put(f"/v1/internal/coding-remote/{uid}", body)
        if err:
            return ActionResult.error(f"Not saved: {err}", code="CODING_REMOTE_ROUTE_WRITE_FAILED")
        st = (res or {}).get("state", {})
        return ActionResult.success(
            data=CodingRemote(active=(res or {}).get("active", False), session_id=(res or {}).get("session_id"),
                              enabled=st.get("enabled", False), mirror=st.get("mirror", []), steer=st.get("steer", [])),
            summary=f"coding remote set: {params.mode}")
    except Exception as e:
        return ActionResult.error(f"Failed to set coding-remote: {_safe_err(e)}", code="CODING_REMOTE_ROUTE_WRITE_FAILED")


@chat.function("send_instruction", action_type="write", event="coding-remote.instruction_sent",
    description="Send an instruction to the live terminal coding session (it keeps running on your machine).",
    data_model=CodingRemote)
async def fn_send(ctx, params: SendParams) -> ActionResult:
    """Send an instruction into the acting user's live coding session.

    Always targets ``ctx.user.imperal_id`` — never a caller-supplied user.
    Requires an active session; when none is live the gateway refuses with a
    clean reason (e.g. "no active coding session") that is surfaced as-is,
    with no internal URL/host ever leaked into the message.

    Origin-honest (v2): the acting turn's surface (normalized to the core
    vocab, see :func:`_turn_surface`) rides along in the steer body so the
    terminal labels the instruction with its TRUE origin (e.g. ``[telegram]``).
    When the surface is not readable from ctx the field is omitted and the
    gateway applies its default.

    Targeted (T2, W4c 2026-07-20): ``params.session_id`` — when non-empty —
    rides along so the gateway addresses that ONE tab instead of its own
    freshest-session pick; omitted (the default) is byte-identical to pre-
    T2 behavior.
    """
    try:
        uid = _user_id(ctx)
        body: dict = {"text": params.text}
        surface = _turn_surface(ctx)
        if surface:
            body["surface"] = surface
        if params.session_id:
            body["session_id"] = params.session_id
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/steer", body)
        if err:
            return ActionResult.error(f"Not sent: {err}", code="CODING_REMOTE_INSTRUCTION_FAILED")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="instruction sent to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to send instruction: {_safe_err(e)}", code="CODING_REMOTE_INSTRUCTION_FAILED")


@chat.function("stop_session", action_type="write", event="coding-remote.stopped",
    description="Stop the running terminal coding session (like pressing Esc in the terminal).",
    data_model=CodingRemote)
async def fn_stop(ctx, params: StopParams) -> ActionResult:
    """Stop the acting user's running coding turn — remote Esc.

    Always targets ``ctx.user.imperal_id`` — never a caller-supplied user.
    Cancels the CURRENT run only: the session and its thread survive, exactly
    like pressing Esc in the terminal, so the next instruction continues the
    same conversation. It never needs to be approved twice — one call, one
    cancel. When nothing is running the gateway refuses with an honest no-op
    reason (surfaced as-is, no internal URL/host ever leaked).

    Targeted (T2, W4c 2026-07-20): ``params.session_id`` — when non-empty —
    stops that ONE tab instead of the gateway's own freshest-session pick;
    omitted (the default) is byte-identical to pre-T2 behavior (empty body).
    """
    try:
        uid = _user_id(ctx)
        body: dict = {"session_id": params.session_id} if params.session_id else {}
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/stop", body)
        if err:
            return ActionResult.error(f"Not stopped: {err}", code="CODING_REMOTE_STOP_FAILED")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="stop sent to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to stop the coding session: {_safe_err(e)}", code="CODING_REMOTE_STOP_FAILED")


@chat.function("set_coding_mode", action_type="write", event="coding-remote.coding_mode_changed",
    description="Set the terminal coding session's mode: default (ask before risky actions), plan (read-only planning), or autopilot (auto-approve — the terminal will ask you to confirm the switch).",
    data_model=CodingRemote)
async def fn_coding_mode(ctx, params: CodingModeParams) -> ActionResult:
    """Switch the acting user's live coding session between consent modes.

    Always targets ``ctx.user.imperal_id`` — never a caller-supplied user.
    ``mode`` must be one of ``default`` (the terminal asks before risky
    actions), ``plan`` (read-only planning — consent-gated actions are
    refused), or ``autopilot`` (auto-approve every consent). This is the
    session's CONSENT policy — a different axis from :func:`fn_set`
    (``set_mode``), which routes mirror/steer surfaces; never conflate the two.

    The gateway gates ownership + remote-control enabled server-side;
    ``autopilot`` additionally requires the origin surface in the steer
    allowlist, and the terminal asks its LOCAL user to confirm before
    autopilot takes effect — a remote surface can never silently disarm the
    consent prompt (downgrades to default/plan apply without a confirm).
    Refusals (e.g. no active session, surface not allowed) are surfaced
    as-is, with no internal URL/host ever leaked.

    Origin-honest (v2): the acting turn's surface (normalized, see
    :func:`_turn_surface`) rides along when readable so the gateway can
    check the autopilot allowlist and tag the flip's true origin; when not
    readable the field is omitted and the gateway applies its default.

    Targeted (T2, W4c 2026-07-20): ``params.session_id`` — when non-empty —
    rides along so the gateway flips that ONE tab instead of its own
    freshest-session pick; omitted (the default) is byte-identical to pre-
    T2 behavior.
    """
    try:
        uid = _user_id(ctx)
        mode = params.mode.strip().lower()
        if mode not in _CODING_MODES:
            return ActionResult.error("mode must be one of: default, plan, autopilot", code="CODING_REMOTE_BAD_CODING_MODE")
        body: dict = {"mode": mode}
        surface = _turn_surface(ctx)
        if surface:
            body["surface"] = surface
        if params.session_id:
            body["session_id"] = params.session_id
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/mode", body)
        if err:
            return ActionResult.error(f"Not set: {err}", code="CODING_REMOTE_CODING_MODE_FAILED")
        summary = f"coding mode → {mode}"
        if mode == "autopilot":
            summary += " (autopilot asks the terminal to confirm)"
        return ActionResult.success(
            data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
            summary=summary)
    except Exception as e:
        return ActionResult.error(f"Failed to set the coding mode: {_safe_err(e)}", code="CODING_REMOTE_CODING_MODE_FAILED")


@chat.function("reply_consent", action_type="write", event="coding-remote.consent_replied",
    description="Reply to a pending approval waiting on the terminal coding session (e.g. approve or decline).",
    data_model=CodingRemote)
async def fn_reply_consent(ctx, params: ConsentParams) -> ActionResult:
    """Reply to the acting user's pending consent approval.

    Always targets ``ctx.user.imperal_id`` — never a caller-supplied user.
    ``text`` is relayed RAW as the consent reply (ICNLI: the kernel
    interprets the words, this tool never normalizes/validates "approve" /
    "decline" itself — any free-form reply the user types goes straight
    through).

    When no approval is waiting the gateway answers 404 and this surfaces a
    clean, honest "that approval was already answered — the card will
    refresh" rather than a stale tool/summary carried over from an old read
    (T2, W4c 2026-07-20: the most common way to hit this is the panel's own
    card racing a reply the user already sent from elsewhere — the copy
    tells them the refresh, not a dead end, is coming). Any other refusal
    (e.g. no session to relay into) is surfaced as the gateway's own reason,
    with no internal URL/host ever leaked.

    Targeted (T2, W4c 2026-07-20): ``params.session_id`` — when non-empty —
    answers THAT tab's approval instead of the gateway's own freshest-
    session pick (ownership-checked server-side, 403 on foreign); omitted
    (the default) is byte-identical to pre-T2 behavior.
    """
    try:
        uid = _user_id(ctx)
        body: dict = {"text": params.text}
        if params.session_id:
            body["session_id"] = params.session_id
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/consent", body)
        if err:
            if err.startswith("HTTP 404"):
                return ActionResult.error("that approval was already answered — the card will refresh", code="CODING_REMOTE_NO_PENDING_CONSENT")
            return ActionResult.error(f"Not sent: {err}", code="CODING_REMOTE_CONSENT_FAILED")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="reply relayed to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to reply to the approval: {_safe_err(e)}", code="CODING_REMOTE_CONSENT_FAILED")


__all__ = ["fn_status", "fn_set", "fn_send", "fn_stop", "fn_coding_mode", "fn_reply_consent"]
