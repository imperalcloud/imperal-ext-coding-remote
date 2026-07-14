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


_MODES = {
    "tg": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    "panel": {"enabled": True, "mirror": ["panel"], "steer": []},
    "both": {"enabled": True, "mirror": ["telegram", "panel"], "steer": ["telegram"]},
    "off": {"enabled": False, "mirror": [], "steer": []},
}


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
    """
    try:
        uid = _user_id(ctx)
        res, err = await gw_post(f"/v1/internal/coding-remote/{uid}/steer", {"text": params.text})
        if err:
            return ActionResult.error(f"Not sent: {err}")
        return ActionResult.success(data=CodingRemote(active=True, session_id=(res or {}).get("session_id")),
                                    summary="instruction sent to your coding session")
    except Exception as e:
        return ActionResult.error(f"Failed to send instruction: {_safe_err(e)}")


__all__ = ["fn_status", "fn_set", "fn_send"]
