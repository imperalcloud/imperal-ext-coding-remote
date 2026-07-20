"""Coding Remote · SDL return model."""
from __future__ import annotations

from imperal_sdk import sdl
from pydantic import BaseModel, Field, model_validator


class CodingTab(BaseModel):
    """One row of ``GET /v1/internal/coding-remote/{uid}/sessions`` (T2, W4c
    2026-07-20) — a tab in the user's coding-session inventory, everything
    the panel's Tabs section needs to render a row and target it with a
    session-scoped write. Nested list field, plain BaseModel (not
    ``sdl.Entity``) — same convention as other exts' list-of-rows fields
    (e.g. ``imperal-ext-automations`` ``CatalogEntry``/``CapabilityEntry``);
    the top-level ``CodingRemote`` already carries the ``x-sdl: entity``
    marker, a row doesn't need its own.

    ``label`` is ``None`` until the terminal has polled with ``&label=`` at
    least once (T3/0.3.25 client work); render the gateway T1 report's own
    fallback — ``kind + (slot or 'main')`` — instead of a blank row (see
    ``panels._tab_label``).

    ``terminal_online`` is a WIDER truth than ``CodingRemote.active``'s
    single-value liveness pointer: it also credits live-session-registry
    freshness, so a genuine multi-tab session can legitimately show more
    than one row with ``terminal_online: true`` at once — never assume "at
    most one online" here (T1 gateway report, contract note).

    ``mode`` mirrors the gateway's ``applied_mode`` for THIS session,
    named to match ``CodingRemote.mode`` (the freshest-session twin of this
    same fact) rather than the gateway's own ``applied_mode`` key.

    ``pending_consent`` mirrors ``CodingRemote.pending_consent`` but scoped
    to THIS tab — a multi-tab user can have more than one approval waiting
    at once, each answerable independently via
    ``reply_consent(session_id=...)``."""
    session_id: str
    slot: str = ""
    kind: str = ""
    label: str | None = None
    terminal_online: bool = False
    mode: str | None = None
    requested_mode: str | None = None
    pending_consent: dict | None = None
    started: str | None = None


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
    requested_mode: str | None = None  # v1.3.2: gateway's non-destructive peek of a NOT-YET-APPLIED remote mode request — the panel renders «(applying…)» until the terminal's next check-in pops it
    tabs: list[CodingTab] = Field(default_factory=list)  # v1.4.0 (T2, W4c): per-tab inventory from GET .../sessions — every RUNNING session the user owns, enriched for the panel's Tabs section (see CodingTab). Empty when the inventory fetch fails or the user has no tabs at all — fail-soft, never blocks the rest of get_status (fn_status fetches it best-effort).

    @model_validator(mode="before")
    @classmethod
    def _sdl_canon(cls, data):
        if isinstance(data, dict):
            data.setdefault("id", "coding-remote")
            data.setdefault("title", "Coding remote")
        return data
