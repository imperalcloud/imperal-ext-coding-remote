"""Coding Remote · Panel — remote control for the terminal Webbee Code
session(s). Tab-first and LIVE (v1.6.0 redesign; v1.7.0 per-tab route).

`refresh="interval:5s"` (Auth-GW caches panel responses 5s) → terminal-side
changes (a tab starts, a consent appears, mode changed in the terminal) show
without a manual reload. Header: an honest account summary + the account-level
route, replacing the old fake single-"Session: Live" card. Then one card per
open tab (running/parked/idle) with FULL control ALWAYS visible: its own
Default/Plan/Autopilot mode + its own Telegram/Panel/Both/Off route (both
session_id-targeted and reaching idle tabs), Stop while running/parked,
Approve/Decline on a pending approval. Every action calls the same
get_status/set_mode/set_coding_mode/send_instruction/stop_session/
reply_consent handlers the chat tools use — one code path per write; no local
state computation. Design: superpowers/specs 2026-07-20 redesign + 2026-07-21
route-per-tab.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext, _user_id
from handlers import _MODES, EmptyParams, fn_status

log = logging.getLogger("coding-remote")

# Account-level routing (set_mode): where sessions mirror + where replies can
# steer them back. "off" turns remote control off entirely.
_MODE_LABELS = {"tg": "Telegram", "panel": "Panel", "both": "Both", "off": "Off"}
# Per-session CONSENT modes (set_coding_mode) — a DIFFERENT axis from routing.
_CODING_MODE_LABELS = {"default": "Default", "plan": "Plan", "autopilot": "Autopilot"}
_STATUS_GLYPH_WORD = {"running": "running", "parked": "parked", "idle": "idle"}
_STATUS_COLOR = {"running": "green", "parked": "yellow", "idle": "gray"}


def _route_buttons(current_mode: str, session_id: str | None = None) -> ui.Stack:
    # ``session_id`` (v1.7.0) targets ONE tab's routing (gateway writes only
    # that tab's state); None = the account-level header control.
    call_kwargs = {"session_id": session_id} if session_id else {}
    return ui.Stack(direction="h", gap=1, children=[
        ui.Button(
            label=label,
            variant="primary" if mode == current_mode else "secondary",
            size="sm",
            on_click=ui.Call("set_mode", mode=mode, **call_kwargs),
        )
        for mode, label in _MODE_LABELS.items()
    ])


def _coding_mode_buttons(enabled: bool, applied_mode: str | None,
                         requested_mode: str | None = None,
                         session_id: str | None = None,
                         allow_autopilot: bool = True,
                         compact: bool = False) -> ui.Stack:
    """Segmented Default/Plan/Autopilot control (one builder, every tab + any
    fallback). Each button calls set_coding_mode via the same single code path
    the chat tool uses. ``session_id`` targets ONE tab (never the gateway's
    freshest guess). ``enabled`` = account remote-control is on (a mode flip is
    a command that requires remote control); works on an idle tab too — the
    gateway queues a one-shot slot the terminal drains on its next poll.
    ``allow_autopilot`` False disables ONLY the Autopilot button — the gateway
    refuses panel-origin autopilot unless Panel is in the steer allowlist, so
    we never offer a click that will 403. The button matching the applied (or
    not-yet-applied requested) mode is highlighted primary; a requested-but-
    not-yet-ACKed mode shows "(applying…)"."""
    call_kwargs = {"session_id": session_id} if session_id else {}
    btn_kwargs = {"size": "sm"} if compact else {}
    children = []
    for code, label in _CODING_MODE_LABELS.items():
        disabled = (not enabled) or (code == "autopilot" and not allow_autopilot)
        applying = bool(requested_mode) and code == requested_mode and requested_mode != applied_mode
        children.append(ui.Button(
            label=f"{label} (applying…)" if applying else label,
            variant="primary" if code == (requested_mode or applied_mode) else "secondary",
            disabled=disabled,
            on_click=ui.Call("set_coding_mode", mode=code, **call_kwargs),
            **btn_kwargs,
        ))
    return ui.Stack(direction="h", gap=1, children=children)


def _current_mode(mirror: list[str], steer: list[str], enabled: bool) -> str:
    if not enabled:
        return "off"
    for mode, spec in _MODES.items():
        if spec["enabled"] and sorted(spec["mirror"]) == sorted(mirror or []) and sorted(spec["steer"]) == sorted(steer or []):
            return mode
    return ""


def _tab_label(tab) -> str:
    """PURE. Effective display label for a tab. The gateway's own label wins
    once the terminal has reported one; until then render the browser-tab-style
    fallback ``kind + (slot or 'main')`` so a row is never blank."""
    if tab.label:
        return tab.label
    return f"{tab.kind or 'session'} {tab.slot or 'main'}"


def _summary_line(tabs: list) -> str:
    """PURE. Honest one-line account summary — replaces the old fake
    single-session "Live" stat. Counts by lifecycle; never invents a state."""
    if not tabs:
        return "No coding sessions open"
    n = len(tabs)
    parts = [f"{n} tab" + ("s" if n != 1 else "")]
    for word in ("running", "parked", "idle"):
        c = sum(1 for t in tabs if t.status == word)
        if c:
            parts.append(f"{c} {word}")
    return " · ".join(parts)


def _tab_card(tab, enabled: bool, allow_autopilot: bool) -> ui.Card:
    """One tab = one self-contained card with FULL control, ALWAYS visible
    (running/parked/idle). ``●`` online / ``○`` offline; a colored status
    pill; the Default/Plan/Autopilot mode segment (targeted at THIS tab, works
    even when idle); Stop only while running/parked (idle has nothing to
    stop); Approve/Decline only when THIS tab has its own pending approval (a
    multi-tab user can have several waiting, each answered independently)."""
    glyph = "●" if tab.terminal_online else "○"
    status_word = _STATUS_GLYPH_WORD.get(tab.status, tab.status or "unknown")
    commandable = tab.status in ("running", "parked")

    children = [
        ui.Stat(label="Status", value=status_word,
                color=_STATUS_COLOR.get(tab.status, "gray")),
        _coding_mode_buttons(enabled, tab.mode, tab.requested_mode,
                             session_id=tab.session_id,
                             allow_autopilot=allow_autopilot, compact=True),
    ]
    if not enabled:
        # Context-aware: when this tab is ALSO waiting on an approval (its
        # Approve/Decline are hidden while remote control is off), tie the
        # hint to the approval too — not just the mode.
        hint = ("turn on remote control above to answer this approval or change the mode"
                if tab.pending_consent else
                "turn on remote control above to change this tab's mode")
        children.append(ui.Text(content=hint, variant="caption"))

    # Per-tab route (v1.7.0): this tab's OWN mirror/steer (Off disables just
    # this tab); the header route stays the account default.
    children.append(ui.Text(content="Mirror & steer (this tab)", variant="caption"))
    children.append(_route_buttons(_current_mode(tab.mirror, tab.steer, bool(tab.enabled)),
                                   session_id=tab.session_id))

    actions = []
    if tab.pending_consent and enabled:
        actions.append(ui.Button(
            label="Approve", variant="primary", size="sm",
            on_click=ui.Call("reply_consent", text="approve", session_id=tab.session_id)))
        actions.append(ui.Button(
            label="Decline", variant="secondary", size="sm",
            on_click=ui.Call("reply_consent", text="decline", session_id=tab.session_id)))
    if commandable and enabled:
        actions.append(ui.Button(
            label="Stop", variant="danger", size="sm", icon="Square",
            on_click=ui.Call("stop_session", session_id=tab.session_id)))
    if actions:
        children.append(ui.Stack(direction="h", gap=1, children=actions))
    if tab.pending_consent:
        children.insert(1, ui.Alert(
            message=_consent_label(tab.pending_consent), type="warn"))

    return ui.Card(title=f"{glyph} {_tab_label(tab)}",
                   content=ui.Stack(direction="v", gap=1, children=children))


def _consent_label(pending: dict) -> str:
    """FACTS from the kernel (tool + summary), never invented here."""
    tool = (pending or {}).get("tool") or ""
    summary = (pending or {}).get("summary") or ""
    if tool and summary:
        return f"waiting for approval: {tool} — {summary}"
    return summary or tool or "waiting for your approval"


@ext.panel(
    "control", slot="left", title="Coding remote", icon="Terminal",
    # LIVE (v1.6.0): poll every 5s so terminal-side changes (a tab starts
    # running, a consent appears, the mode changed in the terminal) show up
    # without a manual reload — the old on_event refresh only fired after the
    # user's OWN panel writes. 5s = the Auth-GW panel-cache TTL (faster is
    # wasted) and ≈ the terminal's own poll cadence.
    refresh="interval:5s",
)
async def coding_remote_control_panel(ctx, **kwargs):
    """Tab-first, live control surface for the terminal Webbee Code
    session(s). Header (honest account summary + account route) → one card per
    open tab (its own mode segment + per-tab route + Stop + Approve/Decline) →
    Send box (target-tab picker when >1) → honest empty-state. Everything
    remote needs remote control on. Full contract: module docstring above."""
    uid = _user_id(ctx)
    try:
        res = await fn_status(ctx, EmptyParams())
    except Exception as e:
        log.error("coding-remote control panel load error for %s: %s", uid, e)
        return ui.Stack(children=[
            ui.Alert(message="Could not load coding-remote status — retrying live", type="error"),
        ])
    if res.status != "success":
        return ui.Stack(children=[
            ui.Alert(message=res.error or "Could not load coding-remote status", type="error"),
        ])

    data = res.data
    enabled = bool(data.enabled)
    tabs = list(getattr(data, "tabs", None) or [])
    route_mode = _current_mode(data.mirror, data.steer, enabled)
    allow_autopilot = "panel" in (data.steer or [])
    # commandable = anything stoppable/steerable (running OR parked — the
    # gateway reaches parked sessions); truly_live = a turn is ACTUALLY running
    # right now (a live terminal). Only truly_live is green; parked-only is
    # yellow (matches the per-tab status pill), never green — parked ≠ live.
    anything_running = any(t.status in ("running", "parked") for t in tabs) or bool(data.running)
    truly_live = any(t.status == "running" for t in tabs) or bool(data.active)

    # ── Header: honest summary + account routing ──────────────────────── #
    # When the tab inventory is empty but a session IS running (fail-soft),
    # never claim "No coding sessions open" — that would contradict the
    # fallback session card below. Distinguish live from parked honestly.
    if tabs:
        summary_val = _summary_line(tabs)
    elif data.running:
        summary_val = "1 active session" if data.active else "1 session (parked — terminal offline)"
    else:
        summary_val = "No coding sessions open"
    header_children = [
        ui.Stat(label="Coding sessions", value=summary_val,
                color="green" if truly_live else ("yellow" if anything_running else "gray")),
        ui.Text(content="Remote routing — where your sessions mirror & steer", variant="caption"),
        _route_buttons(route_mode),
    ]
    if not enabled:
        header_children.append(ui.Text(
            content="Remote control is off — pick a surface above to steer your tabs from here",
            variant="caption"))
    elif (tabs or data.running) and not allow_autopilot:
        # Fires for the fallback path too (a running session with no tab
        # inventory), not only when tabs are present — so a greyed Autopilot
        # button always has its explanation.
        header_children.append(ui.Text(
            content="Autopilot from the panel needs Panel or Both routing",
            variant="caption"))
    header_children.append(ui.Text(content="updates live", variant="caption"))

    children = [ui.Card(title="Coding sessions", content=ui.Stack(direction="v", gap=2, children=header_children))]

    # ── One card per tab (full control, always visible) ───────────────── #
    for tab in tabs:
        children.append(_tab_card(tab, enabled, allow_autopilot))

    # Fallback: the per-tab inventory failed soft (empty) while the freshest
    # read still says a session is running — never leave the user with no
    # control. A single mode segment + pending-approval alert targeting the
    # freshest session keeps them covered until the inventory recovers (≤5s).
    if not tabs and data.running:
        fb = [
            ui.Text(content="tab list unavailable — showing the current session", variant="caption"),
            _coding_mode_buttons(enabled, data.mode, getattr(data, "requested_mode", None),
                                 allow_autopilot=allow_autopilot),
        ]
        if data.pending_consent and enabled:
            fb.append(ui.Alert(message=_consent_label(data.pending_consent), type="warn"))
            fb.append(ui.Stack(direction="h", gap=1, children=[
                ui.Button(label="Approve", variant="primary", on_click=ui.Call("reply_consent", text="approve")),
                ui.Button(label="Decline", variant="secondary", on_click=ui.Call("reply_consent", text="decline")),
            ]))
        if anything_running and enabled:
            fb.append(ui.Button(label="Stop", variant="danger", icon="Square",
                                on_click=ui.Call("stop_session")))
        fb_title = "Active session" if data.active else "Session (parked — terminal offline)"
        children.append(ui.Card(title=fb_title, content=ui.Stack(direction="v", gap=1, children=fb)))

    # ── Send composer (target a tab when there is more than one) ──────── #
    if enabled and (tabs or data.running):
        send_children = [ui.Input(placeholder="Type an instruction for your coding session…", param_name="text")]
        if len(tabs) > 1:
            send_children.append(ui.Select(
                options=[{"value": t.session_id, "label": _tab_label(t)} for t in tabs],
                param_name="session_id",
                placeholder="Target tab (defaults to the most recently active)",
            ))
        children.append(ui.Section(title="Send instruction", children=[
            ui.Form(action="send_instruction", submit_label="Send", children=send_children),
        ]))

    # ── Empty state ───────────────────────────────────────────────────── #
    if not tabs and not data.running:
        children.append(ui.Alert(
            message="No coding sessions open — open Webbee Code in a terminal to start one",
            type="info"))

    return ui.Stack(direction="v", gap=2, children=children)


__all__ = ["coding_remote_control_panel"]
