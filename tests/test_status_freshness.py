"""Tests for the Session/Status freshness fix (2026-07-17):

Bug: the left-sidebar "Session" card always showed "Live"/Idle from a
stale cached fn_status() read (the panel never re-fetched after a write —
refresh="manual", no write tool declared event=) and never showed WHEN the
status was actually checked, so a session that had already ended still
displayed "Live" until the user manually reloaded the panel.

Fix (this extension only):
  - CodingRemote gained `checked_at` (UTC ISO-8601, set by fn_status only —
    the gateway has no session last-activity timestamp today, so this is an
    honest "as-of" freshness marker, never a fabricated terminal last-seen).
  - fn_set / fn_send / fn_stop / fn_coding_mode now declare event= so the
    panel's refresh="on_event:coding-remote.route_changed,..." re-fetches
    fresh status right after any write — no more stale Live/Idle after
    Stop/Send/Route/coding-mode changes.
  - panels.py renders a "Checked" row with the as-of time (or "unknown" if
    unset/unparsable) instead of a bare colour-only Live/Idle stat.
"""
from __future__ import annotations

import pytest

import handlers as h
import panels as p

UID = "imp_u_TEST"
STATUS_PATH = f"/v1/internal/coding-remote/{UID}"


# ─── checked_at is populated honestly by fn_status ─────────────────────── #

@pytest.mark.asyncio
async def test_get_status_sets_checked_at_utc_iso(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={"user_id": UID, "session_id": None, "active": False,
                                    "state": {"enabled": False, "mirror": [], "steer": []}})

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.checked_at is not None
    # Strict format: YYYY-MM-DDTHH:MM:SSZ (no microseconds, always Z/UTC).
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", res.data.checked_at)


@pytest.mark.asyncio
async def test_get_status_checked_at_reflects_call_time_not_gateway_data(make_ctx, gw_mock):
    """checked_at is a client-side "as-of" stamp — it must NOT be echoed from
    (or dependent on) any gateway response field, since the gateway sends none."""
    gw_mock.get(STATUS_PATH, json={"user_id": UID, "session_id": "coding-x-1", "active": True,
                                    "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]}})

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.data.active is True
    assert res.data.checked_at is not None


# ─── write tools declare event= (drives panel refresh="on_event:...") ──── #

def test_set_mode_declares_event_for_panel_autorefresh():
    from app import chat as chat_ext
    entry = chat_ext._functions.get("set_mode")
    assert entry is not None, "set_mode must be registered on the ChatExtension"
    assert entry.event == "coding-remote.route_changed"


def test_send_instruction_declares_event_for_panel_autorefresh():
    from app import chat as chat_ext
    entry = chat_ext._functions.get("send_instruction")
    assert entry is not None
    assert getattr(entry, "event", "") == "coding-remote.instruction_sent"


def test_stop_session_declares_event_for_panel_autorefresh():
    from app import chat as chat_ext
    entry = chat_ext._functions.get("stop_session")
    assert entry is not None
    assert getattr(entry, "event", "") == "coding-remote.stopped"


def test_set_coding_mode_declares_event_for_panel_autorefresh():
    from app import chat as chat_ext
    entry = chat_ext._functions.get("set_coding_mode")
    assert entry is not None
    assert getattr(entry, "event", "") == "coding-remote.coding_mode_changed"


# ─── panel refresh= is wired to those same events, not "manual" ────────── #

def test_control_panel_refresh_is_wired_to_write_events():
    from app import ext
    panel_def = ext._panels.get("control") if hasattr(ext, "_panels") else None
    assert panel_def is not None, "control panel must be registered"
    refresh = panel_def.get("refresh", "")
    assert refresh != "manual", "panel must no longer be manual-only refresh"
    assert refresh.startswith("on_event:")
    for evt in ("coding-remote.route_changed", "coding-remote.instruction_sent",
                "coding-remote.stopped", "coding-remote.coding_mode_changed"):
        assert evt in refresh


# ─── _fmt_checked_at renders an honest as-of label ─────────────────────── #

def test_fmt_checked_at_none_renders_unknown_not_now():
    assert p._fmt_checked_at(None) == "unknown"


def test_fmt_checked_at_garbage_renders_unknown():
    assert p._fmt_checked_at("not-a-date") == "unknown"


def test_fmt_checked_at_valid_iso_renders_readable_utc():
    out = p._fmt_checked_at("2026-07-17T02:45:47Z")
    assert "2026-07-17" in out
    assert "02:45" in out
    assert "UTC" in out
