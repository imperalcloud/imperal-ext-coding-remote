"""Tests for the v1.3.0 «panel becomes truthful and actionable» change set:

  - CodingRemote gains running/mode/last_seen/pending_consent (gateway ACK
    of the applied consent mode + park-reachability truth, T1-T3 upstream).
  - fn_status surfaces all four; a NEW reply_consent tool relays a raw
    approve/decline (or any free-form reply) into the pending approval.
  - panels.py: the coding-mode row highlights the REAL applied mode, a new
    "Approval pending" section renders Approve/Decline when pending_consent
    is set, and the Session card tells Live from Parked (terminal offline,
    still reachable) from Idle — steer/mode/send controls stay enabled
    whenever the session is running, live or parked.
  - handlers._MODES: panel/both now steer via the panel too (T3 widened the
    gateway's steer allowlist), not mirror-only.

Gateway is mocked with the gw_mock fixture (httpx.MockTransport, see
tests/conftest.py) — no real network. Panel fragments are inspected via
UINode.to_dict() (recursively serializes children/props/actions), same
pattern used by imperal-ext-billing's panel tests.
"""
from __future__ import annotations

import json

import httpx
import pytest

import handlers as h
import panels as p

UID = "imp_u_TEST"

STATUS_PATH = f"/v1/internal/coding-remote/{UID}"
CONSENT_PATH = f"/v1/internal/coding-remote/{UID}/consent"

PENDING = {"req_id": "req_1", "tool": "run_shell", "summary": "rm -rf build/", "since": "2026-07-20T10:00:00Z"}


def _flat(node) -> str:
    """The full rendered panel tree as one JSON string (for substring
    assertions) — json.dumps (double-quoted keys/values), not repr, so
    '"key": "value"'-style substring checks are exact."""
    return json.dumps(node.to_dict())


# ─── fn_status: running/mode/last_seen/pending_consent passthrough ────── #

@pytest.mark.asyncio
async def test_get_status_passes_through_running_mode_last_seen_pending(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": "plan", "last_seen": "2026-07-20T10:05:00Z", "pending_consent": PENDING,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.running is True
    assert res.data.mode == "plan"
    assert res.data.last_seen == "2026-07-20T10:05:00Z"
    assert res.data.pending_consent == PENDING


@pytest.mark.asyncio
async def test_get_status_parked_session_running_true_active_false(make_ctx, gw_mock):
    """Parked: a workflow exists (running) but the terminal pointer is dead
    (active=False) — the WIDER truth this version introduces."""
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": False, "running": True,
        "applied_mode": None, "last_seen": "2026-07-20T09:00:00Z", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    })

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.active is False
    assert res.data.running is True
    assert res.data.mode is None
    assert "parked" in res.summary


@pytest.mark.asyncio
async def test_get_status_idle_has_no_running_session_and_no_mode(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": None, "active": False, "running": False,
        "applied_mode": None, "last_seen": None, "pending_consent": None,
        "state": {"enabled": False, "mirror": [], "steer": []},
    })

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.running is False
    assert res.data.mode is None
    assert res.data.pending_consent is None
    assert "idle" in res.summary


@pytest.mark.asyncio
async def test_get_status_defaults_running_when_gateway_omits_the_field(make_ctx, gw_mock):
    """Back-compat: a gateway response with no `running` key falls back to
    `active` — never fabricates a parked state the gateway didn't report."""
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.data.running is True
    assert "live" in res.summary


# ─── reply_consent ──────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_reply_consent_success_posts_text_and_reports_relayed(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"ok": True, "session_id": f"coding-{UID}-abc123"})

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "success"
    assert gw_mock.was_called("POST", CONSENT_PATH)
    body = json.loads(gw_mock.last_request("POST", CONSENT_PATH).content)
    assert body == {"text": "approve"}
    assert res.summary == "reply relayed to your coding session"
    assert res.data.session_id == f"coding-{UID}-abc123"


@pytest.mark.asyncio
async def test_reply_consent_relays_raw_text_never_normalized(make_ctx, gw_mock):
    """ICNLI: the kernel interprets the words — any free-form reply goes
    straight through, not just approve/decline."""
    gw_mock.post(CONSENT_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="only the tests, not the build step"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", CONSENT_PATH).content)
    assert body == {"text": "only the tests, not the build step"}


@pytest.mark.asyncio
async def test_reply_consent_no_pending_404_is_honest_not_stale(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"detail": "no pending approval"}, status=404)

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "error"
    assert res.error == "no approval is waiting right now"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_reply_consent_no_session_409_surfaces_gateway_reason(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"detail": "no session"}, status=409)

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "error"
    assert "409" in res.error
    assert "no session" in res.error
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_reply_consent_gateway_unreachable_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("POST", CONSENT_PATH, httpx.ConnectError("boom"))

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_reply_consent_uses_ctx_user_id_only_never_a_param(make_ctx, gw_mock):
    other_uid = "imp_u_OTHER"
    gw_mock.post(f"/v1/internal/coding-remote/{other_uid}/consent",
                 json={"ok": True, "session_id": f"coding-{other_uid}-xyz"})

    res = await h.fn_reply_consent(make_ctx(imperal_id=other_uid), h.ConsentParams(text="approve"))
    assert res.status == "success"
    assert gw_mock.was_called("POST", f"/v1/internal/coding-remote/{other_uid}/consent")
    assert not gw_mock.was_called("POST", CONSENT_PATH)


def test_reply_consent_declares_event_for_panel_autorefresh():
    from app import chat as chat_ext
    entry = chat_ext._functions.get("reply_consent")
    assert entry is not None, "reply_consent must be registered on the ChatExtension"
    assert entry.event == "coding-remote.consent_replied"


def test_consent_params_model_has_no_user_id_field():
    assert "user_id" not in h.ConsentParams.model_fields


# ─── handlers._MODES: panel/both now steer via the panel too ─────────── #

def test_modes_panel_preset_steers_via_panel():
    assert h._MODES["panel"]["steer"] == ["panel"]


def test_modes_both_preset_steers_via_telegram_and_panel():
    assert sorted(h._MODES["both"]["steer"]) == sorted(["telegram", "panel"])


def test_modes_tg_and_off_presets_unchanged():
    assert h._MODES["tg"]["steer"] == ["telegram"]
    assert h._MODES["off"]["steer"] == []


# ─── panels.py: coding-mode row highlights the REAL applied mode ─────── #

@pytest.mark.asyncio
async def test_panel_highlights_matching_applied_mode_as_primary(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": "plan", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    def find_buttons(n):
        out = []
        if isinstance(n, dict):
            if n.get("type") == "Button":
                out.append(n["props"])
            for v in n.values():
                out.extend(find_buttons(v))
        elif isinstance(n, list):
            for v in n:
                out.extend(find_buttons(v))
        return out

    coding_buttons = {b["label"]: b["variant"] for b in find_buttons(tree)
                       if b["label"] in ("Default", "Plan", "Autopilot")}
    assert coding_buttons == {"Default": "secondary", "Plan": "primary", "Autopilot": "secondary"}


@pytest.mark.asyncio
async def test_panel_mode_unknown_keeps_all_secondary_and_shows_hint(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": None, "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    def find_buttons(n):
        out = []
        if isinstance(n, dict):
            if n.get("type") == "Button":
                out.append(n["props"])
            for v in n.values():
                out.extend(find_buttons(v))
        elif isinstance(n, list):
            for v in n:
                out.extend(find_buttons(v))
        return out

    coding_buttons = {b["label"]: b["variant"] for b in find_buttons(tree)
                       if b["label"] in ("Default", "Plan", "Autopilot")}
    assert coding_buttons == {"Default": "secondary", "Plan": "secondary", "Autopilot": "secondary"}
    assert "mode unknown" in _flat(node)


@pytest.mark.asyncio
async def test_panel_mode_known_has_no_unknown_hint(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert "mode unknown" not in flat


# ─── panels.py: Approval pending section ──────────────────────────────── #

@pytest.mark.asyncio
async def test_panel_shows_approval_section_with_buttons_when_pending(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": False, "running": True,
        "applied_mode": None, "pending_consent": PENDING,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert "Approval pending" in flat
    assert "run_shell" in flat and "rm -rf build/" in flat
    assert "reply_consent" in flat
    assert '"text": "approve"' in flat
    assert '"text": "decline"' in flat
    assert "decline this approval" in flat


@pytest.mark.asyncio
async def test_panel_no_approval_section_when_nothing_pending(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert "Approval pending" not in flat


# ─── panels.py: Session card — Live / Parked / Idle + controls enabled ─ #

@pytest.mark.asyncio
async def test_panel_live_session_stat_and_controls_enabled(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": True, "running": True,
        "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert '"value": "Live"' in flat
    assert "Parked" not in flat


@pytest.mark.asyncio
async def test_panel_parked_session_shows_offline_wording_and_last_seen(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": False, "running": True,
        "applied_mode": None, "last_seen": "2026-07-20T09:00:00Z", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert "Parked" in flat and "terminal offline" in flat
    assert "last seen" in flat
    assert "2026-07-20T09:00:00Z" in flat


@pytest.mark.asyncio
async def test_panel_idle_session_stat_says_idle(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": None, "active": False, "running": False,
        "applied_mode": None, "pending_consent": None,
        "state": {"enabled": False, "mirror": [], "steer": []},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)
    assert '"value": "Idle"' in flat
    assert "No coding session is live right now" in flat


@pytest.mark.asyncio
async def test_panel_parked_session_keeps_stop_and_mode_buttons_enabled(make_ctx, gw_mock):
    """The gateway now reaches parked sessions (T2) — Stop and the
    coding-mode row must stay clickable, not disabled, while parked."""
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": f"coding-{UID}-abc", "active": False, "running": True,
        "applied_mode": None, "pending_consent": None,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    def find_by_label(n, label):
        out = []
        if isinstance(n, dict):
            if n.get("type") == "Button" and n.get("props", {}).get("label") == label:
                out.append(n["props"])
            for v in n.values():
                out.extend(find_by_label(v, label))
        elif isinstance(n, list):
            for v in n:
                out.extend(find_by_label(v, label))
        return out

    stop_buttons = find_by_label(tree, "Stop")
    assert len(stop_buttons) == 1
    assert stop_buttons[0]["disabled"] is False

    for label in ("Default", "Plan", "Autopilot"):
        buttons = find_by_label(tree, label)
        assert len(buttons) == 1
        assert buttons[0]["disabled"] is False


@pytest.mark.asyncio
async def test_panel_idle_disables_stop_and_mode_buttons(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json={
        "user_id": UID, "session_id": None, "active": False, "running": False,
        "applied_mode": None, "pending_consent": None,
        "state": {"enabled": False, "mirror": [], "steer": []},
    })

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    def find_by_label(n, label):
        out = []
        if isinstance(n, dict):
            if n.get("type") == "Button" and n.get("props", {}).get("label") == label:
                out.append(n["props"])
            for v in n.values():
                out.extend(find_by_label(v, label))
        elif isinstance(n, list):
            for v in n:
                out.extend(find_by_label(v, label))
        return out

    assert find_by_label(tree, "Stop")[0]["disabled"] is True
    assert find_by_label(tree, "Plan")[0]["disabled"] is True


# ─── panel refresh= includes the new consent-reply event ─────────────── #

def test_control_panel_refresh_includes_consent_replied_event():
    from app import ext
    panel_def = ext._panels.get("control") if hasattr(ext, "_panels") else None
    assert panel_def is not None
    refresh = panel_def.get("refresh", "")
    assert "coding-remote.consent_replied" in refresh
