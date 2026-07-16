"""Coding Remote · SDL return model."""
from __future__ import annotations

from imperal_sdk import sdl
from pydantic import model_validator


class CodingRemote(sdl.Entity):
    """Remote-control status of the terminal Webbee Code session.

    active/session_id describe whether a coding session is currently live;
    enabled/mirror/steer are the effective routing state (mirror = where
    session output is echoed, steer = where replies can drive it back).

    ``checked_at`` is the UTC ISO-8601 timestamp of the moment THIS answer
    was fetched from the gateway (set by fn_status only) — the gateway's
    ``GET /v1/internal/coding-remote/{uid}`` has no session last-activity
    timestamp today, so this is an honest "as-of" freshness marker, never a
    fabricated "last seen" for the terminal itself. write-tools (fn_set/
    fn_send/fn_stop/fn_coding_mode) leave it unset — their echoed ``active``
    is a point-in-time write acknowledgement, not a fresh status read; the
    panel always re-fetches via fn_status after any write (see panels.py)."""
    active: bool | None = None
    session_id: str | None = None
    enabled: bool | None = None
    mirror: list[str] | None = None
    steer: list[str] | None = None
    checked_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", "coding-remote")
            data.setdefault("title", "Coding remote")
        return data
