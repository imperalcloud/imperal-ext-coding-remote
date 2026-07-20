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
Tabs section lists every open tab the user has (label, live/offline glyph,
its own status, mode, its OWN pending approval if any) with per-tab
Approve/Decline/Stop, and the Send form gains a tab picker once there is
more than one — every write tool now accepts an optional session_id so any
of these can target a specific tab instead of the gateway's freshest-
session guess.

v1.4.1 (W4c follow-up, same day — live feedback: "panel doesn't show tabs,
Stop is unclear"): the Tabs section now renders whenever there is at least
one tab AT ALL — even exactly one, with nothing pending — because
visibility of what is actually open was the whole point Valentin flagged;
previously it only appeared for >1 tab or a pending approval, so a single-
tab user (the common case) never saw it. Each row also shows the tab's own
lifecycle status (running/parked/idle — see CodingTab.status), and a
per-tab Stop only renders while that tab is running or parked (an idle tab
has nothing to stop). When there are truly no open tabs at all (and the
top-level read agrees nothing is running), the panel says so honestly
instead of silently omitting the section. The Session card's Stop button
also gained a caption line explaining what it actually does — Valentin's
other complaint, that "Stop" alone reads as ambiguous (stop the whole
session? just the run?).

v1.5.0 (W4c, same day — live feedback: "I want to change coding mode per
tab, each tab its own full control, so the user sees EVERYTHING properly"):
every running/parked tab row now carries its OWN Default/Plan/Autopilot
coding-mode segment, targeted at that tab's session_id via the exact same
set_coding_mode path the chat tool and the global card use (the gateway
/mode route already accepts session_id targeting from T2). One
`_coding_mode_buttons` builder serves both the global card and every
per-tab row — no second code path. The GLOBAL Coding-mode section is now a
FALLBACK: it renders only when the per-tab inventory is unavailable (fetch
failed soft, or no open tabs), so a mode control is always reachable
without duplicating the per-tab segments when the inventory IS present.
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


def _coding_mode_buttons(enabled: bool, applied_mode: str | None,
                         requested_mode: str | None = None,
                         session_id: str | None = None,
                         compact: bool = False) -> ui.Stack:
    # Segmented control: each button requests that mode for the session via
    # set_coding_mode (same single code path as the chat tool). ``enabled``
    # gates clickability — a mode flip is a command to a session, not a stored
    # setting, but it DOES reach a parked session (running, terminal offline),
    # same as steer/send; the caller passes whether THIS target has a
    # commandable session. The button matching applied_mode is highlighted
    # primary; when applied_mode is None (the terminal hasn't ACK'd one yet)
    # every button stays secondary — never guess which one is "current".
    # Autopilot additionally gets a local y/n confirm at the terminal before
    # it takes effect.
    #
    # ``session_id`` (v1.5.0, W4c 2026-07-20 — Valentin: "each tab its OWN
    # full control, change coding mode per tab") targets ONE tab's
    # set_coding_mode instead of the gateway's freshest-session pick; ``None``
    # = the freshest session (the global card's fail-soft fallback path when
    # the per-tab inventory is unavailable). ``compact`` renders sm buttons
    # for the tighter per-tab row. ONE builder for both the global card and
    # every per-tab row — no second code path.
    call_kwargs = {"session_id": session_id} if session_id else {}
    btn_kwargs = {"size": "sm"} if compact else {}
    return ui.Stack(direction="h", gap=1, children=[
        ui.Button(
            label=(f"{label} (applying…)" if requested_mode and code == requested_mode
                   and requested_mode != applied_mode else label),
            variant="primary" if code == (requested_mode or applied_mode) else "secondary",
            disabled=not enabled,
            on_click=ui.Call("set_coding_mode", mode=code, **call_kwargs),
            **btn_kwargs,
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


# Lifecycle glyph+word per tab.status (v1.4.1, W4c follow-up) — mirrors the
# ○/● terminal_online glyph convention already in this row, and the
# 'mode?'-style honest placeholder convention for a value the gateway
# hasn't sent yet (see _tab_status_text).
_STATUS_GLYPH_WORD = {
    "running": "▶ running",
    "parked": "⏸ parked",
    "idle": "○ idle",
}


def _tab_status_text(tab) -> str:
    """PURE. Effective status text for a tab row. ``tab.status`` is the
    gateway's own lifecycle word for this one tab (v1.4.1) — glyph+word
    mapped for known values, or an honest "status?" placeholder when the
    gateway hasn't sent the field yet (older gateway / deploy transition),
    same never-guess convention as ``mode`` ("mode?" when unset) — never
    inferred from terminal_online or any other field."""
    return _STATUS_GLYPH_WORD.get(tab.status, tab.status or "status?")


def _tab_row(tab) -> ui.Stack:
    """One row of the Tabs section: a glyph+label+status+mode line, per-tab
    action buttons, and (v1.5.0) this tab's OWN coding-mode segment. ``●`` =
    terminal_online (T1 gateway report: this can be true for more than one
    tab at once in a genuine multi-tab session — never assumed "at most one
    online" here), ``○`` = not. Approve/Decline render only when THIS tab has
    its own pending_consent — a multi-tab user can have more than one approval
    waiting at once, each answered independently. Stop (v1.4.1, W4c follow-up)
    and the coding-mode segment (v1.5.0, W4c 2026-07-20 — Valentin: "change
    coding mode per tab, each tab its own full control") render only while
    THIS tab's own status is running or parked — an idle tab (terminal open,
    nothing running) has no run to stop and no session to flip a mode on, so
    neither is shown for it; when they do render they are scoped to this one
    tab via session_id, the same remote-Esc / set_coding_mode semantics as the
    Session card and the global Coding-mode section, never the gateway's
    freshest-session guess."""
    glyph = "●" if tab.terminal_online else "○"
    mode_txt = tab.mode or "mode?"
    status_txt = _tab_status_text(tab)
    line = f"{glyph} {_tab_label(tab)} · {status_txt} · {mode_txt}"
    if tab.pending_consent:
        line += " · ⚠ approval pending"
    commandable = tab.status in ("running", "parked")
    actions = []
    if tab.pending_consent:
        actions.append(ui.Button(
            label="Approve", variant="primary", size="sm",
            on_click=ui.Call("reply_consent", text="approve", session_id=tab.session_id)))
        actions.append(ui.Button(
            label="Decline", variant="secondary", size="sm",
            on_click=ui.Call("reply_consent", text="decline", session_id=tab.session_id)))
    if commandable:
        actions.append(ui.Button(
            label="Stop", variant="danger", size="sm", icon="Square",
            on_click=ui.Call("stop_session", session_id=tab.session_id)))
    children = [ui.Text(content=line)]
    if actions:
        children.append(ui.Stack(direction="h", gap=1, children=actions))
    if commandable:
        children.append(_coding_mode_buttons(
            True, tab.mode, tab.requested_mode,
            session_id=tab.session_id, compact=True))
    return ui.Stack(direction="v", gap=1, children=children)


def _tabs_section(tabs: list) -> ui.Section | None:
    """The Tabs section — renders whenever there is at least one open tab
    at all (v1.4.1, W4c follow-up: was previously gated on >1 tab or a
    pending consent, so the common single-tab case never showed it —
    visibility of what is actually open is the whole point). Genuinely no
    tabs (empty list) renders no section here — the panel's bottom-of-page
    empty-state line (see coding_remote_control_panel) covers that case
    honestly instead."""
    if not tabs:
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
    current run only, with a caption line explaining that the session and
    its history survive, added v1.4.1 W4c follow-up after live feedback
    that "Stop" alone read as ambiguous) that is disabled unless the
    top-level read OR some tab is running/parked; an Approval-pending
    section with Approve/Decline when the session is waiting on a consent
    reply (calls reply_consent directly); a row of route buttons (Telegram/
    Panel/Both/Off — each calls set_mode directly); a Tabs section (v1.4.0,
    T2; v1.4.1 W4c follow-up: now renders for ANY non-empty tab list, even a
    lone tab with nothing pending — visibility of what is actually open was
    the point of the fix) listing every open tab with its own status
    (running/parked/idle), per-tab Approve/Decline/Stop (only while that tab
    is running or parked), and — v1.5.0, W4c 2026-07-20 (Valentin: "change
    coding mode per tab, each tab its own full control") — that tab's OWN
    Default/Plan/Autopilot coding-mode segment, targeted by session_id, so
    every tab is flipped independently; a GLOBAL Coding-mode row now renders
    only as a FALLBACK when the per-tab inventory is unavailable (so a mode
    control is always reachable), never duplicating the per-tab segments;
    an honest empty-state line when there are truly no open tabs at all;
    and a text box (plus a tab-target Select once there is more than one
    tab) to send an instruction into the session (calls send_instruction
    directly). Steer/mode/send controls stay enabled whenever the session is
    running, live or parked — only Idle disables them. No local computation
    of session state — always the gateway's live answer via get_status.
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
    tabs = list(getattr(data, "tabs", None) or [])

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

    # Global Coding-mode section (v1.5.0): now a FALLBACK, not the primary
    # control. When the per-tab inventory is available every tab carries its
    # own Default/Plan/Autopilot segment (see _tab_row), so the global card
    # would only duplicate them — it renders ONLY when `tabs` is empty (the
    # inventory fetch failed soft, or the user genuinely has no open tab), so
    # a mode control is always reachable even when the /sessions inventory is
    # down while the freshest read still says a session is running.
    requested_mode = getattr(data, "requested_mode", None)
    coding_mode_children = [_coding_mode_buttons(running, applied_mode, requested_mode)]
    if running and applied_mode is None and requested_mode is None:
        coding_mode_children.append(
            ui.Text(content="mode unknown — terminal hasn't reported yet", variant="caption"))

    # Stop disabled unless SOMETHING is actually stoppable (v1.4.1, W4c
    # follow-up): the top-level read (running, unchanged pre-tabs signal —
    # keeps this true when the /sessions fetch fails soft and tabs is
    # empty) OR any per-tab status is running/parked (catches a genuine
    # multi-tab case where a non-freshest tab is still running/parked).
    stop_disabled = not (running or any(t.status in ("running", "parked") for t in tabs))

    children = [
        ui.Card(
            title="Session",
            content=ui.Stack(children=[
                ui.Stat(label="Coding session", value=session_label, color=session_color),
                ui.KeyValue(items=kv_items),
            ]),
            # Remote Esc — cancels the current run only (session/thread
            # survive). Calls stop_session directly, the same single code
            # path the chat tool uses; disabled only while nothing anywhere
            # is running/parked (enabled while parked — the gateway reaches
            # parked sessions too). Caption line (v1.4.1, W4c follow-up,
            # live feedback: "Stop is unclear") spells out what Stop
            # actually does — never lets the bare label carry that alone.
            footer=ui.Stack(direction="v", gap=1, children=[
                ui.Button(
                    label="Stop", variant="danger", size="sm", icon="Square",
                    disabled=stop_disabled,
                    on_click=ui.Call("stop_session"),
                ),
                ui.Text(
                    content="stops the current run — like pressing Esc in the terminal; "
                            "the session and its history survive",
                    variant="caption",
                ),
            ]),
        ),
    ]

    if pending:
        children.append(_approval_section(pending, data.session_id))

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

    children.append(ui.Section(title="Route", children=[_route_buttons(route_mode)]))
    if not tabs:
        children.append(ui.Section(title="Coding mode", children=coding_mode_children))
    children.append(ui.Section(title="Send instruction", children=[
        ui.Form(
            action="send_instruction",
            submit_label="Send",
            children=send_children,
        ),
    ]))

    # Empty state (v1.4.1, W4c follow-up): tabs is now the more accurate
    # "is anything open at all" signal — it includes idle tabs (terminal
    # open, nothing running), which running alone never counted. Gated on
    # BOTH `not tabs` and `not running` on purpose: a tabs fetch that failed
    # soft (empty list) while the top-level read says a session IS running
    # must never be reported as "no open tabs" — that would be a fabricated
    # claim contradicting the Session card right above it.
    if not tabs and not running:
        children.append(ui.Alert(
            message="no open tabs — open Webbee Code in a terminal (or send an instruction to start one)",
            type="info",
        ))

    return ui.Stack(direction="v", gap=2, children=children)


__all__ = ["coding_remote_control_panel"]
