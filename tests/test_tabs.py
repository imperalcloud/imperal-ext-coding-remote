"""Tests for the v1.4.0 «panel becomes the tab control center» change set
(T2, W4c spec 2026-07-20):

  - fn_status also fetches GET .../{uid}/sessions and fills CodingRemote.tabs
    (list[CodingTab]) — fail-soft: any fetch error yields an empty list
    without failing the rest of the status read.
  - send_instruction / stop_session / set_coding_mode / reply_consent all
    gain an optional `session_id` param, forwarded into the gateway body
    ONLY when non-empty — omitted is byte-identical to pre-T2 behavior.
  - reply_consent's 404 copy changed to "that approval was already
    answered — the card will refresh" (the error CODE stays the same).
  - panels.py: a new "Tabs" section (>1 tab, or any tab with its own
    pending_consent), per-tab Approve/Decline/Stop, a Select in the Send
    form once there is more than one tab, and the single-tab Approval-
    pending card now targets its resolved session_id explicitly.

v1.4.1 (W4c follow-up, same day, live feedback: "panel doesn't show tabs,
Stop is unclear") widens the gateway contract further — CodingTab gains
`status` ("running" | "parked" | "idle" | "" — idle rows, a terminal open
with no active run, NOW APPEAR in the inventory; they never did before)
and the Tabs section now renders for ANY non-empty tab list, even a lone
tab. LIVE_ROW/PARKED_ROW below carry an explicit `status` to match the new
contract; IDLE_ROW is new.

Gateway is mocked with the gw_mock fixture (httpx.MockTransport, see
tests/conftest.py) — no real network. Panel fragments are inspected via
UINode.to_dict() (recursively serializes children/props/actions), same
pattern used by test_consent_and_parked.py.
"""
from __future__ import annotations

import json

import pytest

import handlers as h
import panels as p
from models import CodingTab

UID = "imp_u_TEST"

STATUS_PATH = f"/v1/internal/coding-remote/{UID}"
SESSIONS_PATH = f"/v1/internal/coding-remote/{UID}/sessions"
STEER_PATH = f"/v1/internal/coding-remote/{UID}/steer"
STOP_PATH = f"/v1/internal/coding-remote/{UID}/stop"
MODE_PATH = f"/v1/internal/coding-remote/{UID}/mode"
CONSENT_PATH = f"/v1/internal/coding-remote/{UID}/consent"

LIVE_ROW = {
    "session_id": f"coding-{UID}-abc123", "slot": "", "kind": "coding",
    "label": "fix the auth bug", "terminal_online": True, "applied_mode": "default",
    "requested_mode": None, "pending_consent": None, "started": "2026-07-20T10:00:00Z",
    "status": "running",
}
PARKED_ROW = {
    "session_id": f"marathon-{UID}-xyz789", "slot": "2", "kind": "marathon",
    "label": None, "terminal_online": False, "applied_mode": None,
    "requested_mode": "plan", "pending_consent": None, "started": "2026-07-20T09:00:00Z",
    "status": "parked",
}
IDLE_ROW = {
    "session_id": f"coding-{UID}-idle1", "slot": "3", "kind": "coding",
    "label": "scratch terminal", "terminal_online": True, "applied_mode": None,
    "requested_mode": None, "pending_consent": None, "started": "2026-07-20T11:00:00Z",
    "status": "idle",
}
PENDING = {"req_id": "req_1", "tool": "run_shell", "summary": "rm -rf build/", "since": "2026-07-20T10:00:00Z"}


def _flat(node) -> str:
    return json.dumps(node.to_dict())


def _base_status(**overrides) -> dict:
    body = {
        "user_id": UID, "session_id": f"coding-{UID}-abc123", "active": True, "running": True,
        "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    }
    body.update(overrides)
    return body


# ─── fn_status: tabs passthrough ───────────────────────────────────────── #

@pytest.mark.asyncio
async def test_get_status_fetches_and_fills_tabs(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW]})

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert gw_mock.was_called("GET", SESSIONS_PATH)
    assert len(res.data.tabs) == 2
    live, parked = res.data.tabs
    assert isinstance(live, CodingTab)
    assert live.session_id == f"coding-{UID}-abc123"
    assert live.label == "fix the auth bug"
    assert live.terminal_online is True
    assert live.mode == "default"
    assert live.status == "running"
    assert parked.session_id == f"marathon-{UID}-xyz789"
    assert parked.slot == "2"
    assert parked.kind == "marathon"
    assert parked.label is None
    assert parked.terminal_online is False
    assert parked.requested_mode == "plan"
    assert parked.status == "parked"


@pytest.mark.asyncio
async def test_get_status_tabs_includes_idle_rows(make_ctx, gw_mock):
    """v1.4.1 (W4c follow-up): idle tabs (terminal open, no active run) now
    appear in the inventory at all — they never did before this gateway
    contract widened."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [IDLE_ROW]})

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert len(res.data.tabs) == 1
    idle = res.data.tabs[0]
    assert idle.status == "idle"
    assert idle.terminal_online is True


def test_coding_tab_status_defaults_empty_when_gateway_omits_it():
    """Back-compat: an older gateway that hasn't shipped `status` yet must
    not crash CodingTab construction, and must never be guessed."""
    row = dict(LIVE_ROW)
    row.pop("status")
    tab = h._row_to_tab(row)
    assert tab.status == ""


@pytest.mark.asyncio
async def test_get_status_tabs_can_both_report_terminal_online(make_ctx, gw_mock):
    """T1 gateway report, contract note: terminal_online credits registry
    freshness in addition to the single-value pointer, so a genuine
    multi-tab session can legitimately show more than one row online at
    once — the ext must pass this through verbatim, never collapse it to
    "at most one"."""
    both_online = dict(PARKED_ROW, terminal_online=True)
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, both_online]})

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert all(t.terminal_online for t in res.data.tabs)


@pytest.mark.asyncio
async def test_get_status_tabs_empty_when_sessions_route_unavailable(make_ctx, gw_mock):
    """Fail-soft: no /sessions route registered (older gateway / hiccup) —
    the rest of get_status still succeeds, tabs is just an empty list."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    # SESSIONS_PATH deliberately NOT registered.

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.tabs == []


@pytest.mark.asyncio
async def test_get_status_tabs_empty_when_sessions_key_missing(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID})  # no "sessions" key

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.tabs == []


@pytest.mark.asyncio
async def test_get_status_tabs_uses_ctx_user_id_only(make_ctx, gw_mock):
    other_uid = "imp_u_OTHER"
    gw_mock.get(f"/v1/internal/coding-remote/{other_uid}",
                json={"user_id": other_uid, "session_id": None, "active": False,
                      "state": {"enabled": False, "mirror": [], "steer": []}})
    gw_mock.get(f"/v1/internal/coding-remote/{other_uid}/sessions",
                json={"user_id": other_uid, "sessions": [LIVE_ROW]})

    res = await h.fn_status(make_ctx(imperal_id=other_uid), h.EmptyParams())

    assert res.status == "success"
    assert len(res.data.tabs) == 1
    assert not gw_mock.was_called("GET", SESSIONS_PATH)


# ─── targeted session_id: reaches the gateway body only when set ──────── #

@pytest.mark.asyncio
async def test_send_instruction_passes_session_id_when_provided(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "coding-x-1"})

    res = await h.fn_send(make_ctx(), h.SendParams(text="run tests", session_id="coding-x-1"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body["session_id"] == "coding-x-1"


@pytest.mark.asyncio
async def test_send_instruction_omits_session_id_when_blank(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(), h.SendParams(text="run tests"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert "session_id" not in body


@pytest.mark.asyncio
async def test_stop_session_passes_session_id_when_provided(make_ctx, gw_mock):
    gw_mock.post(STOP_PATH, json={"ok": True, "session_id": "coding-x-1"})

    res = await h.fn_stop(make_ctx(), h.StopParams(session_id="coding-x-1"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STOP_PATH).content)
    assert body == {"session_id": "coding-x-1"}


@pytest.mark.asyncio
async def test_stop_session_omits_session_id_when_blank(make_ctx, gw_mock):
    gw_mock.post(STOP_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_stop(make_ctx(), h.StopParams())

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STOP_PATH).content)
    assert body == {}


@pytest.mark.asyncio
async def test_set_coding_mode_passes_session_id_when_provided(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "mode": "plan"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan", session_id="coding-x-1"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert body == {"mode": "plan", "session_id": "coding-x-1"}


@pytest.mark.asyncio
async def test_set_coding_mode_omits_session_id_when_blank(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "mode": "plan"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert "session_id" not in body


@pytest.mark.asyncio
async def test_reply_consent_passes_session_id_when_provided(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"ok": True, "session_id": "coding-x-1"})

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve", session_id="coding-x-1"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", CONSENT_PATH).content)
    assert body == {"text": "approve", "session_id": "coding-x-1"}


@pytest.mark.asyncio
async def test_reply_consent_omits_session_id_when_blank(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", CONSENT_PATH).content)
    assert body == {"text": "approve"}


# ─── reply_consent 404: new copy, same error code ──────────────────────── #

@pytest.mark.asyncio
async def test_reply_consent_404_new_copy_same_code(make_ctx, gw_mock):
    gw_mock.post(CONSENT_PATH, json={"detail": "no pending approval"}, status=404)

    res = await h.fn_reply_consent(make_ctx(), h.ConsentParams(text="approve"))

    assert res.status == "error"
    assert res.error == "that approval was already answered — the card will refresh"
    assert res.error_code == "CODING_REMOTE_NO_PENDING_CONSENT"


# ─── params models expose session_id with the documented description ─── #

def test_write_params_all_expose_session_id_field():
    for model in (h.SendParams, h.StopParams, h.CodingModeParams, h.ConsentParams):
        assert "session_id" in model.model_fields, model
        field = model.model_fields["session_id"]
        assert field.default == ""
        assert "target a specific tab" in field.description
        assert "get_status" in field.description


# ─── panel refresh= covers every write event this ext emits ───────────── #

def test_control_panel_refresh_contains_every_declared_write_event():
    """Walk the actual registered @chat.function events (not a hardcoded
    subset) and assert every one of them is in the panel's refresh= list —
    the consent-staleness fix's real acceptance bar."""
    from app import chat as chat_ext, ext
    events = {entry.event for entry in chat_ext._functions.values() if getattr(entry, "event", "")}
    assert events, "expected at least one write tool to declare event="
    panel_def = ext._panels.get("control")
    refresh = panel_def.get("refresh", "")
    for evt in events:
        assert evt in refresh, evt


# ─── panels.py: _tab_label fallback ────────────────────────────────────── #

def test_tab_label_uses_gateway_label_when_present():
    tab = CodingTab(**LIVE_ROW)
    assert p._tab_label(tab) == "fix the auth bug"


def test_tab_label_falls_back_to_kind_and_slot_when_none():
    tab = CodingTab(**PARKED_ROW)
    assert p._tab_label(tab) == "marathon 2"


def test_tab_label_falls_back_to_main_when_no_slot():
    row = dict(LIVE_ROW, label=None, slot="")
    tab = CodingTab(**row)
    assert p._tab_label(tab) == "coding main"


# ─── panels.py: Tabs section rendering ─────────────────────────────────── #

@pytest.mark.asyncio
async def test_panel_renders_tabs_section_for_multiple_sessions(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert "Tabs" in flat
    assert "fix the auth bug" in flat
    assert "marathon 2" in flat  # PARKED_ROW's label fallback


@pytest.mark.asyncio
async def test_panel_renders_tabs_section_for_single_tab_no_pending(make_ctx, gw_mock):
    """v1.4.1 (W4c follow-up): the Tabs section now renders for a single
    tab too — visibility of what is actually open is the point of the fix
    (Valentin's live feedback: "panel doesn't show tabs"). This REPLACES
    the v1.4.0 behavior of hiding the section for a lone tab with nothing
    pending."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert '"title": "Tabs"' in flat
    assert "fix the auth bug" in flat


@pytest.mark.asyncio
async def test_panel_tabs_section_renders_for_single_tab_with_pending_consent(make_ctx, gw_mock):
    """A lone tab with its OWN pending_consent still gets a Tabs section
    (per-tab Approve/Decline reachable), even though the top-level Approval
    card also renders for it — both coexist by design."""
    pending_row = dict(LIVE_ROW, pending_consent=PENDING)
    gw_mock.get(STATUS_PATH, json=_base_status(pending_consent=PENDING))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [pending_row]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert '"title": "Tabs"' in flat
    assert "approval pending" in flat


@pytest.mark.asyncio
async def test_panel_per_tab_approve_decline_target_that_tabs_session_id(make_ctx, gw_mock):
    live_pending = dict(LIVE_ROW, pending_consent=PENDING)
    gw_mock.get(STATUS_PATH, json=_base_status(pending_consent=PENDING))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [live_pending, PARKED_ROW]})

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

    buttons = find_buttons(tree)
    tab_approves = [b for b in buttons if b["label"] == "Approve"]
    # One from the top-level Approval-pending card, one from the Tabs row —
    # both must target the SAME (only pending) session.
    for b in tab_approves:
        call_params = b["on_click"]["params"]
        assert call_params["session_id"] == live_pending["session_id"]

    stop_buttons = [b for b in buttons if b["label"] == "Stop"]
    # Session-card Stop (no session_id) + one per tab (WITH session_id).
    per_tab_stops = [b for b in stop_buttons if b["on_click"]["params"].get("session_id")]
    assert {b["on_click"]["params"]["session_id"] for b in per_tab_stops} == {
        live_pending["session_id"], PARKED_ROW["session_id"],
    }


@pytest.mark.asyncio
async def test_panel_approval_section_targets_resolved_session_id(make_ctx, gw_mock):
    """Consent-staleness fix (c): the single-tab Approval-pending card now
    passes the TOP-LEVEL resolved session_id explicitly on Approve/Decline,
    not relying on the gateway's freshest-session resolution."""
    gw_mock.get(STATUS_PATH, json=_base_status(pending_consent=PENDING))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW]})

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

    approve = [b for b in find_buttons(tree) if b["label"] == "Approve"][0]
    assert approve["on_click"]["params"]["session_id"] == f"coding-{UID}-abc123"


# ─── panels.py: Send form Select for multi-tab ─────────────────────────── #

@pytest.mark.asyncio
async def test_panel_send_form_gains_select_for_multiple_tabs(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    def find_selects(n):
        out = []
        if isinstance(n, dict):
            if n.get("type") == "Select":
                out.append(n["props"])
            for v in n.values():
                out.extend(find_selects(v))
        elif isinstance(n, list):
            for v in n:
                out.extend(find_selects(v))
        return out

    selects = find_selects(tree)
    assert len(selects) == 1
    assert selects[0]["param_name"] == "session_id"
    values = {o["value"] for o in selects[0]["options"]}
    assert values == {LIVE_ROW["session_id"], PARKED_ROW["session_id"]}


@pytest.mark.asyncio
async def test_panel_send_form_has_no_select_for_single_tab(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert '"type": "Select"' not in flat


# ─── single-tab v1.3.2 behavior stays unchanged when tabs fetch is empty ─ #

@pytest.mark.asyncio
async def test_panel_single_tab_behavior_unchanged_when_no_sessions_route(make_ctx, gw_mock):
    """No /sessions route mocked at all (fail-soft) — tabs is [], so neither
    the Tabs section nor the Select appears; the panel renders exactly like
    v1.3.2 (Session card, Route, Coding mode, plain Send box). The v1.4.1
    empty-state line must NOT appear here either — the top-level read says
    Live (running=True), so claiming "no open tabs" would contradict the
    Session card right above it (see the `not tabs and not running` gate)."""
    gw_mock.get(STATUS_PATH, json=_base_status())

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert '"title": "Tabs"' not in flat
    assert '"type": "Select"' not in flat
    assert '"value": "Live"' in flat
    assert "no open tabs" not in flat


# ─── v1.4.1: status field, row shape, Stop gating, empty state, caption ── #

def _collect_strings(n) -> list[str]:
    """Recursively collect every string leaf from a to_dict() tree — used
    instead of json.dumps()-then-substring for glyph assertions, since
    json.dumps defaults to ensure_ascii=True and would mangle ●/○/▶/⏸ into
    \\uXXXX escapes (the existing tests in this file avoid that trap by
    never putting a glyph in a `_flat()` substring check — see the plain
    "approval pending" checks with no leading ⚠)."""
    out: list[str] = []
    if isinstance(n, dict):
        for v in n.values():
            out.extend(_collect_strings(v))
    elif isinstance(n, list):
        for v in n:
            out.extend(_collect_strings(v))
    elif isinstance(n, str):
        out.append(n)
    return out


@pytest.mark.asyncio
async def test_panel_tab_row_shows_status_glyph_and_word(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW, IDLE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    joined = "\n".join(_collect_strings(node.to_dict()))

    assert "▶ running" in joined
    assert "⏸ parked" in joined
    assert "○ idle" in joined


def test_tab_row_line_shape_is_glyph_label_status_mode():
    # _row_to_tab (not a bare CodingTab(**LIVE_ROW)) — it is the one that
    # renames the gateway's `applied_mode` key to `mode`; constructing the
    # model directly from the raw row dict would silently drop that field.
    tab = h._row_to_tab(LIVE_ROW)
    row = p._tab_row(tab)
    line = row.to_dict()["props"]["children"][0]["props"]["content"]
    assert line == "● fix the auth bug · ▶ running · default"


def test_tab_status_text_falls_back_honestly_when_unknown():
    tab = CodingTab(**dict(LIVE_ROW, status=""))
    assert p._tab_status_text(tab) == "status?"


@pytest.mark.asyncio
async def test_panel_idle_tab_has_no_stop_button(make_ctx, gw_mock):
    """An idle tab (terminal open, nothing running) has nothing to stop —
    no per-tab Stop button renders for it, unlike running/parked tabs."""
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [IDLE_ROW]})

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

    stop_buttons = [b for b in find_buttons(tree)
                    if b["label"] == "Stop" and b["on_click"]["params"].get("session_id")]
    assert stop_buttons == []


@pytest.mark.asyncio
async def test_panel_running_and_parked_tabs_keep_stop_button(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW, IDLE_ROW]})

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

    per_tab_stops = [b for b in find_buttons(tree)
                     if b["label"] == "Stop" and b["on_click"]["params"].get("session_id")]
    assert {b["on_click"]["params"]["session_id"] for b in per_tab_stops} == {
        LIVE_ROW["session_id"], PARKED_ROW["session_id"],
    }


@pytest.mark.asyncio
async def test_panel_session_card_stop_enabled_when_a_non_freshest_tab_is_parked(make_ctx, gw_mock):
    """Session-card Stop must be enabled whenever ANY tab is running/parked
    — not just when the top-level (freshest-session) `running` flag says
    so. Here the top-level read itself is idle, but a tab is parked."""
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [PARKED_ROW]})

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

    # Session-card Stop has no session_id param — filter it out from the
    # per-tab one, which also matches label "Stop".
    session_card_stop = [b for b in find_by_label(tree, "Stop")
                          if not b["on_click"]["params"].get("session_id")][0]
    assert session_card_stop["disabled"] is False


@pytest.mark.asyncio
async def test_panel_session_card_stop_disabled_when_truly_nothing_running(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [IDLE_ROW]})

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

    session_card_stop = [b for b in find_by_label(tree, "Stop")
                          if not b["on_click"]["params"].get("session_id")][0]
    assert session_card_stop["disabled"] is True


@pytest.mark.asyncio
async def test_panel_stop_caption_present_under_session_card(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert "like pressing Esc in the terminal" in flat
    assert "the session and its history survive" in flat


@pytest.mark.asyncio
async def test_panel_empty_state_line_when_truly_no_tabs_and_not_running(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": []})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert "no open tabs" in flat
    assert "open Webbee Code in a terminal" in flat
    assert "send an instruction to start one" in flat
    assert '"title": "Tabs"' not in flat


@pytest.mark.asyncio
async def test_panel_no_empty_state_line_when_an_idle_tab_is_open(make_ctx, gw_mock):
    """An idle tab IS an open tab — the empty-state line must not appear
    alongside it, even though nothing is "running" at the top level."""
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [IDLE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    flat = _flat(node)

    assert "no open tabs" not in flat
    assert '"title": "Tabs"' in flat


# ─── v1.5.0: per-tab coding-mode segment + global section as fallback ──── #
# Valentin's live feedback: "I want to change coding mode PER TAB, each tab
# its own full control, so the user sees EVERYTHING properly." Every
# running/parked tab row now carries its OWN Default/Plan/Autopilot segment
# (targeted by session_id via the same set_coding_mode path); the global
# Coding-mode section becomes a fail-soft fallback shown only when the
# per-tab inventory is unavailable.

def _all_buttons(tree) -> list[dict]:
    out = []
    if isinstance(tree, dict):
        if tree.get("type") == "Button":
            out.append(tree["props"])
        for v in tree.values():
            out.extend(_all_buttons(v))
    elif isinstance(tree, list):
        for v in tree:
            out.extend(_all_buttons(v))
    return out


def _coding_mode_buttons_of(tree) -> list[dict]:
    """Every set_coding_mode Button in the tree (function == 'set_coding_mode'
    in the serialized Call) — distinguishes them from the route buttons,
    which also carry a `mode` param but call set_mode."""
    return [b for b in _all_buttons(tree)
            if b.get("on_click", {}).get("function") == "set_coding_mode"]


@pytest.mark.asyncio
async def test_panel_running_tab_has_own_coding_mode_segment(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    per_tab = [b for b in _coding_mode_buttons_of(tree)
               if b["on_click"]["params"].get("session_id") == LIVE_ROW["session_id"]]
    assert {b["label"] for b in per_tab} == {"Default", "Plan", "Autopilot"}
    assert {b["on_click"]["params"]["mode"] for b in per_tab} == {"default", "plan", "autopilot"}


@pytest.mark.asyncio
async def test_panel_parked_tab_has_own_coding_mode_segment(make_ctx, gw_mock):
    """A parked tab (terminal offline) still gets the segment — a mode flip
    reaches a parked session, same as steer/stop."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW, PARKED_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    parked = [b for b in _coding_mode_buttons_of(tree)
              if b["on_click"]["params"].get("session_id") == PARKED_ROW["session_id"]]
    # Assert by the mode param, not the label — PARKED_ROW's requested_mode
    # 'plan' decorates that one button's label with «(applying…)».
    assert {b["on_click"]["params"]["mode"] for b in parked} == {"default", "plan", "autopilot"}


@pytest.mark.asyncio
async def test_panel_idle_tab_has_no_coding_mode_segment(make_ctx, gw_mock):
    """An idle tab has no commandable session — no per-tab mode segment,
    same gate as the per-tab Stop button."""
    gw_mock.get(STATUS_PATH, json=_base_status(active=False, running=False))
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [IDLE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    idle_mode_btns = [b for b in _coding_mode_buttons_of(tree)
                      if b["on_click"]["params"].get("session_id") == IDLE_ROW["session_id"]]
    assert idle_mode_btns == []


@pytest.mark.asyncio
async def test_panel_per_tab_mode_highlights_requested_with_applying(make_ctx, gw_mock):
    """PARKED_ROW has applied_mode None + requested_mode 'plan' — its own
    segment highlights Plan primary with the «(applying…)» suffix, exactly
    like the global card's requested-mode rendering; exactly one primary."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [PARKED_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    parked = [b for b in _coding_mode_buttons_of(tree)
              if b["on_click"]["params"].get("session_id") == PARKED_ROW["session_id"]]
    plan = [b for b in parked if b["on_click"]["params"]["mode"] == "plan"][0]
    assert plan["variant"] == "primary"
    assert "applying" in plan["label"]
    assert sum(1 for b in parked if b["variant"] == "primary") == 1


@pytest.mark.asyncio
async def test_panel_global_coding_mode_suppressed_when_tabs_present(make_ctx, gw_mock):
    """With the per-tab inventory available, the global Coding-mode section
    is suppressed — every set_coding_mode button is a per-tab target
    (carries a session_id), none is the session_id-less global one."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": [LIVE_ROW]})

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    assert '"title": "Coding mode"' not in _flat(node)
    global_btns = [b for b in _coding_mode_buttons_of(tree)
                   if not b["on_click"]["params"].get("session_id")]
    assert global_btns == []


@pytest.mark.asyncio
async def test_panel_global_coding_mode_is_fallback_when_no_tabs(make_ctx, gw_mock):
    """Fail-soft fallback: no /sessions route (tabs empty) while the freshest
    read says running — the global Coding-mode section renders so a mode
    control is always reachable, with session_id-less (freshest-target)
    buttons."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    # SESSIONS_PATH deliberately NOT registered -> tabs == []

    node = await p.coding_remote_control_panel(make_ctx())
    tree = node.to_dict()

    assert '"title": "Coding mode"' in _flat(node)
    global_btns = [b for b in _coding_mode_buttons_of(tree)
                   if not b["on_click"]["params"].get("session_id")]
    assert {b["label"] for b in global_btns} == {"Default", "Plan", "Autopilot"}
