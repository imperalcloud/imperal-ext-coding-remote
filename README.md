# imperal-ext-coding-remote

Imperal-owned system extension for remote control of the terminal Webbee
Code (coding agent) session. Lets Webbee check, route, and steer a live
session from chat or the panel — while it keeps running on your machine.

## Tools

- `get_status` (read) — is a coding session currently live, and how is it
  routed (mirror = where output is echoed, steer = where replies can drive
  it back).
- `set_mode` (write) — route the session: `tg` (mirror+steer via Telegram),
  `panel` (mirror to the panel only, no remote steer), `both` (mirror to
  both, steer via Telegram), or `off` (turn remote control off).
- `send_instruction` (write) — push a new instruction into the live
  session. Requires an active session. Origin-honest: the acting turn's
  surface (telegram / web-panel / discord / api) rides along so the
  terminal labels the instruction with its true origin.
- `stop_session` (write) — stop the running turn, like pressing Esc in
  the terminal. Cancels the current run only; the session and its
  conversation survive. An idle session is an honest no-op error.
- `set_coding_mode` (write) — switch the live session's consent mode:
  `default` (ask before risky actions), `plan` (read-only planning), or
  `autopilot` (auto-approve — the terminal asks its local user to confirm
  the switch; downgrades apply right away). Distinct from `set_mode`
  (routing). Ownership and remote-control state are gated server-side;
  autopilot also requires the origin surface in the steer allowlist.

## Panel

A control page (left slot) showing live session status, a Stop button
(remote Esc), route buttons (Telegram/Panel/Both/Off), coding-mode buttons
(Default/Plan/Autopilot), and a send box — this extension IS the control
surface, so it stays visible in the sidebar.

## Access

System app — available to every user without an explicit install.

## Deploy

Published through the Imperal Cloud Developer Portal at
https://panel.imperal.io/developer.
