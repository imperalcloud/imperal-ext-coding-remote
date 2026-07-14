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
  session. Requires an active session.

## Panel

A control page (left slot) showing live session status, route buttons
(Telegram/Panel/Both/Off), and a send box — this extension IS the control
surface, so it stays visible in the sidebar.

## Access

System app — available to every user without an explicit install.

## Deploy

Published through the Imperal Cloud Developer Portal at
https://panel.imperal.io/developer.
