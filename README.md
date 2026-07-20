# imperal-ext-coding-remote

Imperal-owned system extension for remote control of the terminal Webbee
Code (coding agent) session. Lets Webbee check, route, and steer a live
session from chat or the panel — while it keeps running on your machine.

## Tools

- `get_status` (read) — is a coding session live (terminal online), parked
  (a session/marathon exists but the terminal is offline), or idle (none at
  all); how it is routed (mirror = where output is echoed, steer = where
  replies can drive it back); the applied consent mode; any pending
  approval waiting for a reply; and `tabs` — every open tab the user has,
  each with its own status (`running` / `parked` / `idle`), label, mode,
  and its own pending approval if any.
- `set_mode` (write) — route the session: `tg` (mirror+steer via Telegram),
  `panel` (mirror+steer via the panel), `both` (mirror to both, steer via
  Telegram AND the panel), or `off` (turn remote control off). Steer reaches
  the session whether it is live or parked.
- `send_instruction` (write) — push a new instruction into the session.
  Works whenever one is running (live or parked). Origin-honest: the
  acting turn's surface (telegram / web-panel / discord / api) rides along
  so the terminal labels the instruction with its true origin.
- `stop_session` (write) — stop the running turn, like pressing Esc in
  the terminal. Cancels the current run only; the session and its
  conversation survive. An idle session is an honest no-op error.
- `set_coding_mode` (write) — switch the session's consent mode:
  `default` (ask before risky actions), `plan` (read-only planning), or
  `autopilot` (auto-approve — the terminal asks its local user to confirm
  the switch; downgrades apply right away). Distinct from `set_mode`
  (routing). Ownership and remote-control state are gated server-side;
  autopilot also requires the origin surface in the steer allowlist.
- `reply_consent` (write) — reply to a pending approval (e.g.
  `approve`/`decline`, or any free-form text) — relayed raw to the session
  (ICNLI: the kernel interprets the words). 404 when none is waiting means
  it was already answered elsewhere (the card refreshes); sending a fresh
  instruction instead declines it.

Every write tool above also accepts an optional `session_id` — target one
of the tabs from `get_status`'s `tabs` list instead of the most recently
active session.

## Panel

A control page (left slot) showing live/parked/idle session status, a Stop
button (remote Esc, with a caption explaining it cancels only the current
run — the session and its history survive), an Approval-pending section
with Approve/Decline when a consent reply is waiting, a Tabs section
(renders whenever the user has at least one open tab, even just one —
label, live/offline glyph, status, mode, and per-tab Approve/Decline/Stop;
Stop only while that tab is running or parked) with an honest empty-state
line when there are truly no open tabs at all, route buttons (Telegram/
Panel/Both/Off), coding-mode buttons (Default/Plan/Autopilot — highlighting
the REAL applied mode once the terminal ACKs one), and a send box (with a
tab picker once there is more than one tab) — this extension IS the
control surface, so it stays visible in the sidebar. Steer/mode/send
controls stay enabled whenever a session is running, live or parked.

## Access

System app — available to every user without an explicit install.

## Deploy

Published through the Imperal Cloud Developer Portal at
https://panel.imperal.io/developer.
