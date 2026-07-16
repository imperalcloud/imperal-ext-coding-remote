"""Coding Remote · chat tools — FACTS out, narrator phrases (ICNLI)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import ActionResult, chat, gw_get, gw_put, gw_post, _user_id, _safe_err
from models import CodingRemote


class EmptyParams(BaseModel):
    """No parameters needed."""
    pass


class SetParams(BaseModel):
    """Route the terminal coding session. mode: tg | panel | both | off."""
    mode: str = Field(description="tg | panel | both | off")


class SendParams(BaseModel):
    """Send an instruction to the live coding session."""
    text: str = Field(description="the instruction to run in the coding session")


class CodingModeParams(BaseModel):
    """Set the terminal coding session's mode. mode: default | plan | autopilot."""
    mode: str = Field(description="default | plan | autopilot")


_MODES = {
    "tg": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    "panel": {"enabled": True, "mirror": ["panel"], "steer": []},
    "both": {"enabled": True, "mirror": ["telegram", "panel"], "steer": ["telegram"]},
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


@chat.function("get_status", action_type="read",
    description="Show remote-control status of the terminal coding session (active? mirror/steer routing).",
    data_model=CodingRemote)
async def fn_status(ctx, params: EmptyParams) -> ActionResult:
    """Show the acting user's coding-remote status.

    No params — always reads the caller's own session (``ctx.user.imperal_id``).
    Returns FACTS: whether a terminal Webbee Code session is currently live
    (``active``, ``session_id``) and the effective routing (``enabled``,
    ``mirror`` — where session output is echoed, ``steer`` — where replies
    can drive it back).
    """
    try:
        uid = _user_id(ctx)
        d = await gw_get(f"/v1/internal/coding-remote/{uid}")
        st = d.get("state", {})
        return ActionResult.success(
            data=CodingRemote(active=d.get("active", False), session_id=d.get("session_id"),
                              enabled=st.get("enabled", False), mirror=st.get("mirror", []), steer=st.get("steer", [])),
            summary=f"coding session {'live' if d.get('active') else 'idle'}; remote {'on' if st.get('enabled') else 'off'}")
    except Exception as e:
        return ActionResult.error(f"Failed to read coding-remote status: {_safe_err(e)}")


@chat.function("set_mode", action_type="write",
    description="Route the terminal coding session: tg (Telegram), panel, both, or off.",
    data_model=CodingRemote)
async def fn_set(ctx, params: SetParams) -> ActionResult:
    """Change the acting user's coding-remote routing.

    Always writes for ``ctx.user.imperal_id`` — never a caller-supplied user.
    ``mode`` must be one of ``tg`` (mirror+steer via Telegram), ``panel``
    (mirror to the panel only, no remote steer), ``both`` (mirror to both,
    steer via Telegram), or ``off`` (turn remote control off). Returns the
    post-write FACTS in the same shape as get_status.
    """
    try:
        uid = _user_id(ctx)
        body = _MODES.get(params.mode.strip().lower())
        if body is None:
            return ActionResult.error("mode must be one of: tg, panel, both, off")
        res, err = await gw_put(f"/v1/internal/coding-remote/{uid}", body)
        if err:
            return ActionResult.error(f"Not saved: {err}")
        st = (res or {}).get("state", {})
        return ActionResult.success(
            data=CodingRemote(active=(res or {}).get("active", False), session_id=(res or {}).get("session_id"),
                              enabled=st.get("enabled", False), mirror=st.get("mirror", []), steer=st.get("steer", [])),
            summary=f"coding remote set: {params.mode}")
    except Exception as e:
        return ActionResult.error(f"Failed to set coding-remote: {_safe_err(e)}")


@chat.function("send_instruction", action_type="write",
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
    """
    try:
        uid = _user_id(ctx)
        body: dict = {"text": params.text}
        surface = _turn_surface(ctx)
        if surface:
            body["surface"] = surface
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/steer", body)
        if err:
            return ActionResult.error(f"Not sent: {err}")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="instruction sent to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to send instruction: {_safe_err(e)}")


@chat.function("stop_session", action_type="write",
    description="Stop the running terminal coding session (like pressing Esc in the terminal).",
    data_model=CodingRemote)
async def fn_stop(ctx, params: EmptyParams) -> ActionResult:
    """Stop the acting user's running coding turn — remote Esc.

    Always targets ``ctx.user.imperal_id`` — never a caller-supplied user.
    Cancels the CURRENT run only: the session and its thread survive, exactly
    like pressing Esc in the terminal, so the next instruction continues the
    same conversation. It never needs to be approved twice — one call, one
    cancel. When nothing is running the gateway refuses with an honest no-op
    reason (surfaced as-is, no internal URL/host ever leaked).
    """
    try:
        uid = _user_id(ctx)
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/stop", {})
        if err:
            return ActionResult.error(f"Not stopped: {err}")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="stop sent to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to stop the coding session: {_safe_err(e)}")


@chat.function("set_coding_mode", action_type="write",
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
    """
    try:
        uid = _user_id(ctx)
        mode = params.mode.strip().lower()
        if mode not in _CODING_MODES:
            return ActionResult.error("mode must be one of: default, plan, autopilot")
        body: dict = {"mode": mode}
        surface = _turn_surface(ctx)
        if surface:
            body["surface"] = surface
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/mode", body)
        if err:
            return ActionResult.error(f"Not set: {err}")
        summary = f"coding mode → {mode}"
        if mode == "autopilot":
            summary += " (autopilot asks the terminal to confirm)"
        return ActionResult.success(
            data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
            summary=summary)
    except Exception as e:
        return ActionResult.error(f"Failed to set the coding mode: {_safe_err(e)}")


__all__ = ["fn_status", "fn_set", "fn_send", "fn_stop", "fn_coding_mode"]
