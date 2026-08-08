"""Coding Remote · panel size invariant — I7.

The control panel would DISAPPEAR once a user had enough open tabs, and
nothing would look wrong: no exception, no error state, no empty state.
The panel built perfectly -- it was simply too big to deliver.

The kernel caps a fast-RPC reply at 256KB (REPLY_PAYLOAD_MAX_BYTES,
imperal_kernel/rpc/stream_consumer.py:92). An oversize reply is NOT
trimmed: it is REPLACED by a typed error carrying no result data, so the
panel call returns no ``ui`` at all. The panel host then treats the slot
as missing and renders nothing for it -- not even a spinner. Same class
of bug as the automations sidebar at ~70 rules (v1.10.3) and File Reader
at ~500 files (v0.3.5).

A tab card costs ~2.2KB on the wire, so the unbounded card-per-tab list
crossed the cap at ~126 tabs -- reachable in normal use, since long-lived
terminals accumulate idle tabs.

Losing THIS panel is worse than losing a list: it is the only place a
pending approval can be answered from the panel. So the tests below pin
not just the size bound but WHAT SURVIVES it:

  * a tab awaiting approval is never dropped,
  * a running tab is never dropped,
  * the omission is disclosed rather than silently swallowed.
"""
from __future__ import annotations

import json

import pytest

import handlers as h
import panels as p

UID = "imp_u_TEST"
STATUS_PATH = f"/v1/internal/coding-remote/{UID}"
SESSIONS_PATH = f"/v1/internal/coding-remote/{UID}/sessions"

REPLY_CAP_BYTES = 256 * 1024


def _row(i: int, status: str = "idle", consent: bool = False) -> dict:
    """A session row shaped the way the gateway really returns one."""
    return {
        "session_id": f"coding-{UID}-{i:08d}",
        "slot": str(i % 4), "kind": "coding",
        "label": f"refactor the billing module part {i}",
        "terminal_online": status == "running",
        "applied_mode": "default", "requested_mode": None,
        "pending_consent": ({"tool": "conn-ssh__run_command",
                             "summary": "rm -rf /tmp/build-cache"} if consent else None),
        "started": "2026-07-20T10:00:00Z",
        "status": status,
    }


def _base_status(**overrides) -> dict:
    body = {
        "user_id": UID, "session_id": f"coding-{UID}-00000000", "active": True,
        "running": True, "applied_mode": "default", "pending_consent": None,
        "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]},
    }
    body.update(overrides)
    return body


async def _render(make_ctx, gw_mock, rows: list[dict]):
    """Render the real panel against a mocked gateway and return (node, wire)."""
    gw_mock.get(STATUS_PATH, json=_base_status())
    gw_mock.get(SESSIONS_PATH, json={"user_id": UID, "sessions": rows})
    node = await p.coding_remote_control_panel(make_ctx(UID))
    wire = json.dumps(node.to_dict(), ensure_ascii=False)
    return node, wire


# ── the bound itself ──────────────────────────────────────────────────── #

@pytest.mark.asyncio
@pytest.mark.parametrize("count", [126, 300, 1000])
async def test_panel_stays_under_the_kernel_reply_cap(make_ctx, gw_mock, count):
    """126 is where the unbounded panel first breached the cap."""
    _, wire = await _render(make_ctx, gw_mock, [_row(i) for i in range(count)])
    size = len(wire.encode("utf-8"))
    assert size < REPLY_CAP_BYTES, (
        f"{count} tabs render to {size/1024:.1f}KB, over the "
        f"{REPLY_CAP_BYTES//1024}KB cap -- the panel would disappear entirely")


@pytest.mark.asyncio
async def test_a_normal_session_count_is_never_truncated(make_ctx, gw_mock):
    """The bound must not touch realistic use -- no false economy."""
    rows = [_row(i) for i in range(12)]
    _, wire = await _render(make_ctx, gw_mock, rows)
    for r in rows:
        assert r["session_id"] in wire, "a normal tab count must render in full"
    assert "not shown" not in wire


# ── what survives the bound ───────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_a_tab_awaiting_approval_is_never_dropped(make_ctx, gw_mock):
    """This panel is the ONLY place an approval can be answered from the
    panel -- dropping that card would strand the session."""
    rows = [_row(i) for i in range(400)]              # 400 idle tabs first
    rows.append(_row(9999, "idle", consent=True))     # the one that needs the user, LAST
    _, wire = await _render(make_ctx, gw_mock, rows)

    assert f"coding-{UID}-00009999" in wire, (
        "the tab awaiting approval was truncated away -- it can no longer be answered")
    assert len(wire.encode("utf-8")) < REPLY_CAP_BYTES


@pytest.mark.asyncio
async def test_a_running_tab_is_never_dropped(make_ctx, gw_mock):
    """A live turn is what a user most needs to see (and be able to stop)."""
    rows = [_row(i) for i in range(400)]
    rows.append(_row(8888, "running"))                # live one, LAST in input
    _, wire = await _render(make_ctx, gw_mock, rows)

    assert f"coding-{UID}-00008888" in wire, "the running tab was truncated away"


@pytest.mark.asyncio
async def test_truncation_is_disclosed_not_silent(make_ctx, gw_mock):
    """Silently dropping sessions would be a lie; the panel must admit it."""
    _, wire = await _render(make_ctx, gw_mock, [_row(i) for i in range(400)])
    assert "not shown" in wire, "hidden sessions are not disclosed in the panel"


@pytest.mark.asyncio
async def test_target_dropdown_stays_usable(make_ctx, gw_mock):
    """The composer's target Select rides in the same reply -- and a dropdown
    with hundreds of entries is unusable long before it is expensive."""
    _, wire = await _render(make_ctx, gw_mock, [_row(i) for i in range(400)])
    node = json.loads(wire)

    selects = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "Select":
                selects.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(node)

    for s in selects:
        opts = s.get("props", {}).get("options") or []
        assert len(opts) <= p._TARGET_OPTIONS_MAX, (
            f"target dropdown has {len(opts)} options -- unusable")


# ── ordering, on its own ──────────────────────────────────────────────── #

def test_ordering_puts_what_needs_the_user_first():
    """Ordering is what makes the byte budget SAFE rather than merely small."""
    from models import CodingTab

    def tab(i, status="idle", consent=False):
        return CodingTab(session_id=f"cs_{i:04d}", status=status,
                         terminal_online=(status == "running"),
                         pending_consent=({"tool": "x", "summary": "y"} if consent else None))

    tabs = [tab(1), tab(2), tab(3, "parked"), tab(4, "running"), tab(5, consent=True)]
    ordered = p._most_important_first(tabs)

    assert ordered[0].session_id == "cs_0005", "pending approval must come first"
    assert ordered[1].session_id == "cs_0004", "running must come before parked/idle"
    assert ordered[2].session_id == "cs_0003", "parked must come before idle"


def test_ordering_is_stable_between_refreshes():
    """Cards must not jump around while the user is reading them."""
    from models import CodingTab

    tabs = [CodingTab(session_id=f"cs_{i:04d}", status="idle") for i in range(20)]
    once = [t.session_id for t in p._most_important_first(tabs)]
    twice = [t.session_id for t in p._most_important_first(list(reversed(tabs)))]
    assert once == twice, "ordering must be deterministic, not input-order dependent"
