"""v1.8.0 — Front C follow-ups (gateway mirror-out default-on, 2026-07-21).

The gateway now ships `CODING_REMOTE_DEFAULTS = {enabled: true,
mirror: [telegram, panel], steer: []}` and gates EVERY inbound control
(steer/stop/mode) on the per-surface steer allowlist. Four ext-side
consequences covered here:

  1. stop_session passes the acting turn's TRUE origin surface (the gateway's
     `_StopIn.surface` gate is per-surface now) — same _turn_surface plumbing
     send_instruction/set_coding_mode already use; omitted when unreadable.
  2. A tab's Autopilot button gates on THAT tab's own steer allowlist
     (route-per-tab truth), not the account default — with the account
     default now steer-empty, the old account-level gate would grey
     Autopilot on a tab whose owner explicitly granted Panel.
  3. A tab's controls gate on THAT tab's own `enabled` (per-tab Off must
     disable that tab's card even while the account stays on).
  4. The header renders an honest caption for the new default shape
     (enabled + mirror on + steer EMPTY) — mirror-only, no preset matches,
     so without the caption the route row would show no highlighted state
     and no explanation.

Gateway mocked via gw_mock (httpx.MockTransport) — same harness as
tests/test_tabs.py; panel fragments inspected via UINode.to_dict().
"""
from __future__ import annotations

import json

import httpx
import pytest

import handlers as h
import panels as p

UID = "imp_u_TEST"

STATUS_PATH = f"/v1/internal/coding-remote/{UID}"
SESSIONS_PATH = f"/v1/internal/coding-remote/{UID}/sessions"
STOP_PATH = f"/v1/internal/coding-remote/{UID}/stop"


def _flat(node) -> str:
    return json.dumps(node.to_dict())


def _status_front_c_default(**overrides) -> dict:
    """Account state in the Front C DEFAULT shape: mirror-out on, steer empty."""
    body = {
        "user_id": UID, "session_id": f"marathon-{UID}-rdef", "active": True, "running": True,
        "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram", "panel"], "steer": []},
    }
    body.update(overrides)
    return body


def _row(**overrides) -> dict:
    row = {
        "session_id": f"marathon-{UID}-rdef", "slot": "", "kind": "marathon",
        "label": "front-c tab", "terminal_online": True, "applied_mode": "default",
        "requested_mode": None, "pending_consent": None, "started": "2026-07-21T10:00:00Z",
        "status": "running",
        "enabled": True, "mirror": ["telegram", "panel"], "steer": [],
    }
    row.update(overrides)
    return row


def _buttons(node) -> list[dict]:
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "Button":
                out.append(n.get("props") or {})
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node.to_dict())
    return out


# ─── 1. stop_session passes the TRUE origin surface ─────────────────────── #

@pytest.mark.asyncio
async def test_stop_passes_true_origin_surface(make_ctx, gw_mock):
    gw_mock.routes[("POST", STOP_PATH)] = httpx.Response(
        200, json={"ok": True, "session_id": f"marathon-{UID}-rdef"})
    from handlers import StopParams
    res = await h.fn_stop(make_ctx(surface="telegram"), StopParams())
    assert res.status == "success"
    sent = json.loads(gw_mock.calls[-1].content)
    assert sent.get("surface") == "telegram"


@pytest.mark.asyncio
async def test_stop_omits_surface_when_unreadable(make_ctx, gw_mock):
    """No readable ctx surface -> field OMITTED (gateway applies its own
    back-compat default), byte-identical to the pre-v1.8 body."""
    gw_mock.routes[("POST", STOP_PATH)] = httpx.Response(
        200, json={"ok": True, "session_id": f"marathon-{UID}-rdef"})
    from handlers import StopParams
    res = await h.fn_stop(make_ctx(), StopParams())
    assert res.status == "success"
    sent = json.loads(gw_mock.calls[-1].content)
    assert "surface" not in sent


# ─── 2. per-tab Autopilot gate ───────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_tab_autopilot_gates_on_tab_own_steer(make_ctx, gw_mock):
    """Account default steer is EMPTY (Front C), but THIS tab's owner granted
    Panel via route-per-tab -> its Autopilot button must NOT be disabled."""
    gw_mock.get(STATUS_PATH, json=_status_front_c_default())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [
        _row(steer=["panel"], label="panel-armed tab")]})
    node = await p.coding_remote_control_panel(make_ctx())
    autopilots = [b for b in _buttons(node) if str(b.get("label", "")).startswith("Autopilot")]
    assert autopilots and not any(b.get("disabled") for b in autopilots)


@pytest.mark.asyncio
async def test_tab_autopilot_disabled_when_tab_steer_empty(make_ctx, gw_mock):
    """A default-armed tab (steer []) keeps Autopilot greyed even if the
    ACCOUNT allowlist would allow it — per-tab truth wins."""
    gw_mock.get(STATUS_PATH, json=_status_front_c_default(
        state={"enabled": True, "mirror": ["panel"], "steer": ["panel"]}))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [_row(steer=[])]})
    node = await p.coding_remote_control_panel(make_ctx())
    autopilots = [b for b in _buttons(node) if str(b.get("label", "")).startswith("Autopilot")]
    assert autopilots and all(b.get("disabled") for b in autopilots)


# ─── 3. per-tab enabled gate ─────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_tab_controls_gate_on_tab_own_enabled(make_ctx, gw_mock):
    """Per-tab Off (enabled false on THIS tab) disables the tab's mode
    buttons and hides Stop, even while the account stays enabled."""
    gw_mock.get(STATUS_PATH, json=_status_front_c_default())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [
        _row(enabled=False, mirror=[], steer=[])]})
    node = await p.coding_remote_control_panel(make_ctx())
    mode_labels = ("Default", "Plan", "Autopilot")
    mode_buttons = [b for b in _buttons(node)
                    if any(str(b.get("label", "")).startswith(m) for m in mode_labels)]
    assert mode_buttons and all(b.get("disabled") for b in mode_buttons)
    assert not any(b.get("label") == "Stop" for b in _buttons(node))


# ─── 4. honest default-shape caption ─────────────────────────────────────── #

@pytest.mark.asyncio
async def test_header_renders_mirror_only_caption_for_front_c_default(make_ctx, gw_mock):
    """The Front C default (enabled, mirror on, steer EMPTY) matches no route
    preset -> the header must SAY what state the account is in instead of
    showing an unhighlighted row with no explanation."""
    gw_mock.get(STATUS_PATH, json=_status_front_c_default())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [_row()]})
    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert "Mirror-only" in flat
    assert "Remote control is off" not in flat


@pytest.mark.asyncio
async def test_header_off_caption_unchanged_for_explicit_off(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_status_front_c_default(
        state={"enabled": False, "mirror": [], "steer": []}))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": []})
    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert "Remote control is off" in flat
    assert "Mirror-only" not in flat
