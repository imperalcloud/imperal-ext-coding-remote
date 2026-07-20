"""Coding Remote · SDL return model."""
from __future__ import annotations

from imperal_sdk import sdl
from pydantic import model_validator


class CodingRemote(sdl.Entity):
    """Remote-control status of the terminal Webbee Code session.

    active/session_id describe whether a coding session is currently live;
    enabled/mirror/steer are the effective routing state (mirror = where
    session output is echoed, steer = where replies can drive it back).

    ``running`` (v1.3.0+) is a WIDER truth than ``active``: it is set
    whenever a coding/marathon workflow exists for the user at all — either
    with a live terminal pointer (``active`` also true, "Live") or PARKED
    with the terminal offline (``active`` false, "Parked"). ``active`` alone
    stays the narrower "terminal process is online right now" signal it
    always was; never conflate the two — a parked session is still steerable,
    it just isn't attached to a live terminal.

    ``mode`` (v1.3.0+) is the gateway's ACK of the coding session's applied
    consent mode (``default``/``plan``/``autopilot``) — ``None`` when the
    terminal hasn't reported one yet (never guessed/defaulted).

    ``pending_consent`` (v1.3.0+) is ``{req_id, tool, summary, since}`` when
    the session is waiting on an approval, else ``None`` — answer it with
    the ``reply_consent`` tool.

    ``last_seen`` (v1.3.1: epoch SECONDS int — v1.3.0 mistyped it str and
    every ONLINE terminal failed model validation) is the gateway's own
    last-seen marker for the terminal pointer; the panel renders it as an
    honest relative time derived from this real timestamp.

    ``checked_at`` is the UTC ISO-8601 timestamp of the moment THIS answer
    was fetched from the gateway (set by fn_status only) — the gateway's
    ``GET /v1/internal/coding-remote/{uid}`` has no session last-activity
    timestamp of its own for this field, so this is an honest "as-of"
    freshness marker, never a fabricated "last seen" for the terminal
    itself. write-tools (fn_set/fn_send/fn_stop/fn_coding_mode/
    fn_reply_consent) leave it unset — their echoed ``active`` is a
    point-in-time write acknowledgement, not a fresh status read; the panel
    always re-fetches via fn_status after any write (see panels.py)."""
    active: bool | None = None
    session_id: str | None = None
    enabled: bool | None = None
    mirror: list[str] | None = None
    steer: list[str] | None = None
    checked_at: str | None = None
    running: bool = False
    mode: str | None = None
    last_seen: int | None = None  # epoch seconds (gateway pointer TTL truth) — v1.3.1: was mistyped str, ValidationError whenever the terminal was ONLINE
    pending_consent: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", "coding-remote")
            data.setdefault("title", "Coding remote")
        return data
