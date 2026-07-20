"""Coding Remote · Panel — remote-control page for the terminal Webbee Code
session.

Unlike notifications/web-search (informational-only, hidden from the
sidebar), this extension IS the control surface: it renders the live
status plus write controls (route + coding-mode buttons + a Stop button via
ui.Call, a send box via ui.Form, and — since v1.3.0 — Approve/Decline
buttons for a pending approval) that call straight into the same
get_status/set_mode/set_coding_mode/send_instruction/stop_session/
reply_consent handlers the chat tools use — every action bypasses chat and
invokes the @chat.function directly, so there is exactly one code path for
every write, chat or panel.

Since v1.4.0 (T2, W4c 2026-07-20) the panel is the tab control center: a
Tabs section lists every RUNNING session the user owns (label, live/offline
glyph, mode, its OWN pending approval if any) with per-tab Approve/Decline/
Stop, and the Send form gains a tab picker once there is more than one —
every write tool now accepts an optional session_id so any of these can
target a specific tab instead of the gateway's freshest-session guess.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from imperal_sdk import ui

from app import ext, _user_id
from handlers import _MODES, EmptyParams, fn_status

log = logging.getLogger("coding-remote")

_MODE_LABELS = {"tg": "Telegram", "panel": "Panel", "both": "Both", "off": "Off"}

# Coding CONSENT modes (set_coding_mode) — distinct from the routing modes
# above. Since v1.3.0 the gateway ACKs the applied mode (CodingRemote.mode),
# so a button now highlights the REAL current mode instead of always
# rendering as a bare one-shot request.
_CODING_MODE_LABELS = {"default": "Default", "plan": "Plan", "autopilot": "Autopilot"}


def _route_buttons(current_mode: str) -> ui.Stack:
    return ui.Stack(direction="h", gap=1, children=[
        ui.Button(
            label=label,
            variant="primary" if mode == current_mode else "secondary",
            on_click=ui.Call("set_mode", mode=mode),
        )
        for mode, label in _MODE_LABELS.items()
    ])


def _coding_mode_buttons(running: bool, applied_mode: str | None,
                         requested_mode: str | None = None) -> ui.Stack:
    # Segmented control: each button requests that mode for the session via
    # set_coding_mode (same single code path as the chat tool). Disabled
    # while no session is running at all — a mode flip is a command to a
    # session, not a stored setting, but it DOES reach a parked session
    # (running=True, active=False), same as steer/send. The button matching
    # applied_mode is highlighted primary; when applied_mode is None (the
    # terminal hasn't ACK'd one yet) every button stays secondary — never
    # guess which one is "current". Autopilot additionally gets a local y/n
    # confirm at the terminal before it takes effect.
    return ui.Stack(direction="h", gap=1, children=[
        ui.Button(
            label=(f"{label} (applying…)" if requested_mode and code == requested_mode
                   and requested_mode != applied_mode else label),
            variant="primary" if code == (requested_mode or applied_mode) else "secondary",
            disabled=not running,
            on_click=ui.Call("set_coding_mode", mode=code),
        )
        for code, label in _CODING_MODE_LABELS.items()
    ])


def _current_mode(mirror: list[str], steer: list[str], enabled: bool) -> str:
    if not enabled:
        return "off"
    for mode, spec in _MODES.items():
        if spec["enabled"] and sorted(spec["mirror"]) == sorted(mirror or []) and sorted(spec["steer"]) == sorted(steer or []):
            return mode
    return ""


def _ago(epoch) -> str:
    """PURE. Humanize an epoch-seconds marker («3m ago»). Never raises —
    unknown/garbage input renders as an empty string (the row is then
    simply skipped by the caller's truthiness check)."""
    try:
        import time
        delta = max(0, int(time.time()) - int(epoch))
    except Exception:
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _fmt_checked_at(checked_at: str | None) -> str:
    """Render CodingRemote.checked_at (UTC ISO-8601) as a short, honest
    "as-of" label. Never claims to be the terminal's last-activity time —
    that timestamp doesn't exist on the gateway today (see models.py). A
    missing/unparsable value renders "unknown" rather than hiding the row or
    silently defaulting to "now", so a stale/cached panel render is visible
    as such instead of masquerading as fresh."""
    if not checked_at:
        return "unknown"
    try:
        dt = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _approval_section(pending: dict, session_id: str | None) -> ui.Section:
    """Render the pending consent request + Approve/Decline. ``pending`` is
    ``CodingRemote.pending_consent`` — {req_id, tool, summary, since}. The
    label is built from tool+summary as-provided by the gateway (FACTS from
    the kernel), never invented here.

    ``session_id`` (T2, W4c 2026-07-20 consent-staleness fix c) — the TOP-
    LEVEL status read's own resolved session (the SAME session
    ``pending_consent`` was read for) — rides along explicitly on both
    buttons instead of leaving it to the gateway's freshest-session
    resolution. Single-tab and multi-tab behave identically either way
    today, but pinning it here removes any race between "the card was
    rendered for session A" and "freshest just became session B" — the
    click always answers the approval that is actually on screen. Both
    buttons still call reply_consent directly with a fixed, honest text
    ('approve'/'decline') — the same single code path the chat tool uses,
    ICNLI raw-words relay."""
    tool = (pending or {}).get("tool") or ""
    summary = (pending or {}).get("summary") or ""
    label = f"{tool} — {summary}" if tool and summary else (summary or tool or "your coding session is waiting for your approval")
    approve_kwargs = {"text": "approve"}
    decline_kwargs = {"text": "decline"}
    if session_id:
        approve_kwargs["session_id"] = session_id
        decline_kwargs["session_id"] = session_id
    return ui.Section(title="Approval pending", children=[
        ui.Alert(message=label, type="warn"),
        ui.Stack(direction="h", gap=1, children=[
            ui.Button(label="Approve", variant="primary", on_click=ui.Call("reply_consent", **approve_kwargs)),
            ui.Button(label="Decline", variant="secondary", on_click=ui.Call("reply_consent", **decline_kwargs)),
        ]),
        ui.Text(content="sending a new instruction instead will decline this approval", variant="caption"),
    ])


def _tab_label(tab) -> str:
    """PURE. Effective display label for a tab (T2, W4c 2026-07-20). The
    gateway's own label wins once the terminal has reported one (T3/0.3.25
    client work); until then ``label`` is ``None`` (T1 gateway report,
    contract note) and this renders the browser-tab-style fallback the
    report calls for explicitly — ``kind + (slot or 'main')`` — so a tab
    row is never blank."""
    if tab.label:
        return tab.label
    return f"{tab.kind or 'session'} {tab.slot or 'main'}"


def _tab_row(tab) -> ui.Stack:
    """One row of the Tabs section: a glyph+label+mode line plus per-tab
    action buttons. ``●`` = terminal_online (T1 gateway report: this can be
    true for more than one tab at once in a genuine multi-tab session —
    never assumed "at most one online" here), ``○`` = not. Approve/Decline
    render only when THIS tab has its own pending_consent — a multi-tab user
    can have more than one approval waiting at once, each answered
    independently. Stop always renders — same remote-Esc semantics as the
    Session card's Stop button, scoped to this one tab via session_id."""
    glyph = "●" if tab.terminal_online else "○"
    mode_txt = tab.mode or "mode?"
    line = f"{glyph} {_tab_label(tab)} · {mode_txt}"
    if tab.pending_consent:
        line += " · ⚠ approval pending"
    actions = []
    if tab.pending_consent:
        actions.append(ui.Button(
            label="Approve", variant="primary", size="sm",
            on_click=ui.Call("reply_consent", text="approve", session_id=tab.session_id)))
        actions.append(ui.Button(
            label="Decline", variant="secondary", size="sm",
            on_click=ui.Call("reply_consent", text="decline", session_id=tab.session_id)))
    actions.append(ui.Button(
        label="Stop", variant="danger", size="sm", icon="Square",
        on_click=ui.Call("stop_session", session_id=tab.session_id)))
    return ui.Stack(direction="v", gap=1, children=[
        ui.Text(content=line),
        ui.Stack(direction="h", gap=1, children=actions),
    ])


def _tabs_section(tabs: list) -> ui.Section | None:
    """The Tabs section (T2, W4c 2026-07-20) — renders when there is more
    than one tab, OR any tab (even a lone one) has its own pending_consent
    (so a per-tab Approve/Decline is always reachable, not just the
    top-level Approval-pending card). A genuine single-tab user with
    nothing pending sees no Tabs section at all — v1.3.2 behavior,
    unchanged."""
    if not tabs:
        return None
    if len(tabs) <= 1 and not any(t.pending_consent for t in tabs):
        return None
    return ui.Section(title="Tabs", children=[_tab_row(t) for t in tabs])


@ext.panel(
    "control", slot="left", title="Coding remote", icon="Terminal",
    # Re-render after every write this panel can trigger (route/send/stop/
    # coding-mode/consent-reply) so Live/Parked/Idle, the Stop button, the
    # mode highlight, and the approval section always reflect a FRESH
    # get_status answer instead of the snapshot from the last manual panel
    # load. Bug fix (2026-07-17): the panel used to sit on refresh="manual"
    # with no write tool declaring event=, so nothing ever told the platform
    # to re-fetch — the card could show "Live" long after the terminal
    # session had actually ended.
    refresh="on_event:coding-remote.route_changed,coding-remote.instruction_sent,"
            "coding-remote.stopped,coding-remote.coding_mode_changed,"
            "coding-remote.consent_replied",
)
async def coding_remote_control_panel(ctx, **kwargs):
    """Status + controls for the terminal Webbee Code session.

    Shows whether a session is Live (running + terminal online), Parked
    (running but the terminal is offline — steer/mode/send still reach it)
    or Idle (nothing running at all); the effective routing (mirror/steer);
    a Stop button (remote Esc — calls stop_session directly, cancels the
    current run only); an Approval-pending section with Approve/Decline
    when the session is waiting on a consent reply (calls reply_consent
    directly); a row of route buttons (Telegram/Panel/Both/Off — each calls
    set_mode directly); a row of coding-mode buttons (Default/Plan/
    Autopilot — each calls set_coding_mode directly, highlighting the REAL
    applied mode once the terminal ACKs one); a Tabs section (v1.4.0, T2)
    listing every RUNNING session with per-tab Approve/Decline/Stop when
    there is more than one tab or any tab has its own pending approval; and
    a text box (plus a tab-target Select once there is more than one tab)
    to send an instruction into the session (calls send_instruction
    directly). Steer/mode/send controls stay enabled whenever the session
    is running, live or parked — only Idle disables them. No local
    computation of session state — always the gateway's live answer via
    get_status.
    """
    uid = _user_id(ctx)
    try:
        res = await fn_status(ctx, EmptyParams())
    except Exception as e:
        log.error("coding-remote control panel load error for %s: %s", uid, e)
        return ui.Stack(children=[
            ui.Alert(message="Could not load coding-remote status — try again shortly", type="error"),
        ])

    if res.status != "success":
        return ui.Stack(children=[
            ui.Alert(message=res.error or "Could not load coding-remote status", type="error"),
        ])

    data = res.data
    active = bool(data.active)
    running = bool(data.running)
    applied_mode = data.mode
    pending = data.pending_consent
    route_mode = _current_mode(data.mirror, data.steer, bool(data.enabled))

    if active:
        session_label, session_color = "Live", "green"
    elif running:
        session_label, session_color = "Parked — terminal offline", "yellow"
    else:
        session_label, session_color = "Idle", "gray"

    kv_items = [
        {"key": "Remote control", "value": "On" if data.enabled else "Off"},
        {"key": "Mirror", "value": ", ".join(data.mirror or []) or "none"},
        {"key": "Steer", "value": ", ".join(data.steer or []) or "none"},
        {"key": "Checked", "value": _fmt_checked_at(data.checked_at)},
    ]
    if running and not active and data.last_seen and _ago(data.last_seen):
        kv_items.insert(0, {"key": "Terminal", "value": f"last seen {_ago(data.last_seen)}"})

    requested_mode = getattr(data, "requested_mode", None)
    coding_mode_children = [_coding_mode_buttons(running, applied_mode, requested_mode)]
    if running and applied_mode is None and requested_mode is None:
        coding_mode_children.append(
            ui.Text(content="mode unknown — terminal hasn't reported yet", variant="caption"))

    children = [
        ui.Card(
            title="Session",
            content=ui.Stack(children=[
                ui.Stat(label="Coding session", value=session_label, color=session_color),
                ui.KeyValue(items=kv_items),
            ]),
            # Remote Esc — cancels the current run only (session/thread
            # survive). Calls stop_session directly, the same single code
            # path the chat tool uses; disabled only while nothing is
            # running at all (enabled while parked — the gateway reaches
            # parked sessions too).
            footer=ui.Button(
                label="Stop", variant="danger", size="sm", icon="Square",
                disabled=not running,
                on_click=ui.Call("stop_session"),
            ),
        ),
    ]

    if pending:
        children.append(_approval_section(pending, data.session_id))

    tabs = list(getattr(data, "tabs", None) or [])
    tabs_section = _tabs_section(tabs)
    if tabs_section is not None:
        children.append(tabs_section)

    # Send-to (T2, W4c 2026-07-20): with more than one tab, the Send form
    # gains a Select so an instruction can be aimed at a chosen tab instead
    # of always landing on the gateway's freshest-session pick. A single tab
    # keeps the plain text box exactly as v1.3.2 — no Select clutter for the
    # common case.
    send_children = [ui.Input(placeholder="Type an instruction for your coding session…", param_name="text")]
    if len(tabs) > 1:
        send_children.append(ui.Select(
            options=[{"value": t.session_id, "label": _tab_label(t)} for t in tabs],
            param_name="session_id",
            placeholder="Target tab (optional — defaults to the most recently active)",
        ))

    children.extend([
        ui.Section(title="Route", children=[_route_buttons(route_mode)]),
        ui.Section(title="Coding mode", children=coding_mode_children),
        ui.Section(title="Send instruction", children=[
            ui.Form(
                action="send_instruction",
                submit_label="Send",
                children=send_children,
            ),
        ]),
    ])

    if not running:
        children.append(ui.Alert(
            message="No coding session is live right now — start one from your terminal to steer it here.",
            type="info",
        ))

    return ui.Stack(direction="v", gap=2, children=children)


__all__ = ["coding_remote_control_panel"]
