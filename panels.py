"""Coding Remote · Panel — remote-control page for the terminal Webbee Code
session.

Unlike notifications/web-search (informational-only, hidden from the
sidebar), this extension IS the control surface: it renders the live
status plus write controls (route + coding-mode buttons + a Stop button via
ui.Call, a send box via ui.Form) that call straight into the same
get_status/set_mode/set_coding_mode/send_instruction/stop_session handlers
the chat tools use — every action bypasses chat and invokes the
@chat.function directly, so there is exactly one code path for every write,
chat or panel.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext, _user_id
from handlers import _MODES, EmptyParams, fn_status

log = logging.getLogger("coding-remote")

_MODE_LABELS = {"tg": "Telegram", "panel": "Panel", "both": "Both", "off": "Off"}

# Coding CONSENT modes (set_coding_mode) — distinct from the routing modes
# above. Mode lives client-side at the terminal (never persisted server-side),
# so there is no "current" value to highlight — a click is a one-shot request.
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


def _coding_mode_buttons(active: bool) -> ui.Stack:
    # Segmented one-shot control: each button requests that mode for the LIVE
    # session via set_coding_mode (same single code path as the chat tool).
    # Disabled while no session is live — a mode flip is a command to a
    # running terminal, not a stored setting. Autopilot additionally gets a
    # local y/n confirm at the terminal before it takes effect.
    return ui.Stack(direction="h", gap=1, children=[
        ui.Button(
            label=label,
            variant="secondary",
            disabled=not active,
            on_click=ui.Call("set_coding_mode", mode=mode),
        )
        for mode, label in _CODING_MODE_LABELS.items()
    ])


def _current_mode(mirror: list[str], steer: list[str], enabled: bool) -> str:
    if not enabled:
        return "off"
    for mode, spec in _MODES.items():
        if spec["enabled"] and sorted(spec["mirror"]) == sorted(mirror or []) and sorted(spec["steer"]) == sorted(steer or []):
            return mode
    return ""


@ext.panel(
    "control", slot="left", title="Coding remote", icon="Terminal",
    refresh="manual",
)
async def coding_remote_control_panel(ctx, **kwargs):
    """Status + controls for the terminal Webbee Code session.

    Shows whether a session is currently live, the effective routing
    (mirror/steer), a Stop button (remote Esc — calls stop_session directly,
    cancels the current run only), a row of route buttons (Telegram/Panel/
    Both/Off — each calls set_mode directly), a row of coding-mode buttons
    (Default/Plan/Autopilot — each calls set_coding_mode directly; live
    session only, autopilot confirms at the terminal), and a text box to
    send an instruction into the live session (calls send_instruction
    directly). No local computation of session state — always the gateway's
    live answer via get_status.
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
    mode = _current_mode(data.mirror, data.steer, bool(data.enabled))

    children = [
        ui.Card(
            title="Session",
            content=ui.Stack(children=[
                ui.Stat(label="Coding session", value="Live" if active else "Idle",
                        color="green" if active else "gray"),
                ui.KeyValue(items=[
                    {"key": "Remote control", "value": "On" if data.enabled else "Off"},
                    {"key": "Mirror", "value": ", ".join(data.mirror or []) or "none"},
                    {"key": "Steer", "value": ", ".join(data.steer or []) or "none"},
                ]),
            ]),
            # Remote Esc — cancels the current run only (session/thread
            # survive). Calls stop_session directly, the same single code
            # path the chat tool uses; disabled while no session is live.
            footer=ui.Button(
                label="Stop", variant="danger", size="sm", icon="Square",
                disabled=not active,
                on_click=ui.Call("stop_session"),
            ),
        ),
        ui.Section(title="Route", children=[_route_buttons(mode)]),
        ui.Section(title="Coding mode", children=[_coding_mode_buttons(active)]),
        ui.Section(title="Send instruction", children=[
            ui.Form(
                action="send_instruction",
                submit_label="Send",
                children=[
                    ui.Input(placeholder="Type an instruction for your coding session…", param_name="text"),
                ],
            ),
        ]),
    ]

    if not active:
        children.append(ui.Alert(
            message="No coding session is live right now — start one from your terminal to steer it here.",
            type="info",
        ))

    return ui.Stack(direction="v", gap=2, children=children)


__all__ = ["coding_remote_control_panel"]
