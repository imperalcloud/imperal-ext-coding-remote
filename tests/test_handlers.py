"""Tests for coding-remote get_status / set_mode / send_instruction /
stop_session / set_coding_mode.

Gateway is mocked with the gw_mock fixture (httpx.MockTransport under the
hood, see tests/conftest.py) — no real network, no respx. Every test drives
the real handler functions in handlers.py against app.AUTH_GW.
"""
import json

import httpx
import pytest

import handlers as h

# make_ctx / gw_mock are pytest fixtures (see tests/conftest.py) — auto-injected
# by name into any test function below that declares them as parameters. No
# cross-module import needed, so collection is portable regardless of pytest
# rootdir.

UID = "imp_u_TEST"

STATUS_PATH = f"/v1/internal/coding-remote/{UID}"
STEER_PATH = f"/v1/internal/coding-remote/{UID}/steer"
STOP_PATH = f"/v1/internal/coding-remote/{UID}/stop"
MODE_PATH = f"/v1/internal/coding-remote/{UID}/mode"

IDLE_STATE = {"user_id": UID, "session_id": None, "active": False,
              "state": {"enabled": False, "mirror": [], "steer": []}}
LIVE_STATE = {"user_id": UID, "session_id": f"coding-{UID}-abc123", "active": True,
              "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]}}


# ─── get_status ───────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_get_status_reports_idle_when_no_session(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=IDLE_STATE)

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.active is False
    assert res.data.session_id is None
    assert res.data.enabled is False
    assert res.data.mirror == []
    assert res.data.steer == []
    assert "idle" in res.summary


@pytest.mark.asyncio
async def test_get_status_reports_live_session_and_routing(make_ctx, gw_mock):
    gw_mock.get(STATUS_PATH, json=LIVE_STATE)

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert res.data.active is True
    assert res.data.session_id == f"coding-{UID}-abc123"
    assert res.data.enabled is True
    assert res.data.mirror == ["telegram"]
    assert res.data.steer == ["telegram"]
    assert "live" in res.summary


@pytest.mark.asyncio
async def test_get_status_uses_ctx_user_id_only_never_a_param(make_ctx, gw_mock):
    """No user_id can be smuggled in — the handler reads ctx.user.imperal_id and
    calls the gateway for THAT id, regardless of anything in params."""
    other_uid = "imp_u_OTHER"
    gw_mock.get(f"/v1/internal/coding-remote/{other_uid}", json=IDLE_STATE)

    res = await h.fn_status(make_ctx(imperal_id=other_uid), h.EmptyParams())
    assert res.status == "success"
    assert gw_mock.was_called("GET", f"/v1/internal/coding-remote/{other_uid}")
    assert not gw_mock.was_called("GET", STATUS_PATH)


# ─── set_mode ─────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_set_mode_tg_puts_the_route_body_and_echoes_state(make_ctx, gw_mock):
    echoed = {"user_id": UID, "session_id": f"coding-{UID}-abc123", "active": True,
              "state": {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]}}
    gw_mock.put(STATUS_PATH, json=echoed)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="tg"))

    assert res.status == "success"
    assert gw_mock.was_called("PUT", STATUS_PATH)
    body = json.loads(gw_mock.last_request("PUT", STATUS_PATH).content)
    assert body == {"enabled": True, "mirror": ["telegram"], "steer": ["telegram"]}
    assert res.data.enabled is True
    assert res.data.mirror == ["telegram"]
    assert res.data.steer == ["telegram"]
    assert "tg" in res.summary


@pytest.mark.asyncio
async def test_set_mode_off_disables_routing(make_ctx, gw_mock):
    echoed = {"user_id": UID, "session_id": None, "active": False,
              "state": {"enabled": False, "mirror": [], "steer": []}}
    gw_mock.put(STATUS_PATH, json=echoed)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="off"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("PUT", STATUS_PATH).content)
    assert body == {"enabled": False, "mirror": [], "steer": []}
    assert res.data.enabled is False


@pytest.mark.asyncio
async def test_set_mode_both_mirrors_and_steers_telegram_and_panel(make_ctx, gw_mock):
    # v1.3.0: panel is now a real steer surface too — "both" steers via
    # Telegram AND the panel, not Telegram alone (the panel steer allowlist
    # widened on the gateway side, T3).
    echoed = {"user_id": UID, "session_id": f"coding-{UID}-abc123", "active": True,
              "state": {"enabled": True, "mirror": ["telegram", "panel"], "steer": ["telegram", "panel"]}}
    gw_mock.put(STATUS_PATH, json=echoed)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="both"))

    body = json.loads(gw_mock.last_request("PUT", STATUS_PATH).content)
    assert body == {"enabled": True, "mirror": ["telegram", "panel"], "steer": ["telegram", "panel"]}
    assert res.data.mirror == ["telegram", "panel"]
    assert res.data.steer == ["telegram", "panel"]


@pytest.mark.asyncio
async def test_set_mode_case_and_whitespace_insensitive(make_ctx, gw_mock):
    echoed = {"user_id": UID, "session_id": None, "active": False,
              "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]}}
    gw_mock.put(STATUS_PATH, json=echoed)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="  Panel  "))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("PUT", STATUS_PATH).content)
    assert body == {"enabled": True, "mirror": ["panel"], "steer": ["panel"]}


@pytest.mark.asyncio
async def test_set_mode_panel_now_steers_via_panel(make_ctx, gw_mock):
    # v1.3.0: the panel preset gains real remote steer (T3 widened the
    # gateway's steer allowlist) — no longer mirror-only.
    echoed = {"user_id": UID, "session_id": f"coding-{UID}-abc123", "active": True,
              "state": {"enabled": True, "mirror": ["panel"], "steer": ["panel"]}}
    gw_mock.put(STATUS_PATH, json=echoed)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="panel"))

    body = json.loads(gw_mock.last_request("PUT", STATUS_PATH).content)
    assert body == {"enabled": True, "mirror": ["panel"], "steer": ["panel"]}
    assert res.data.steer == ["panel"]


@pytest.mark.asyncio
async def test_set_mode_invalid_mode_errors_before_any_write(make_ctx, gw_mock):
    # No PUT route registered — an unknown mode must be rejected before any write.
    res = await h.fn_set(make_ctx(), h.SetParams(mode="bogus"))

    assert res.status == "error"
    assert "mode must be one of" in res.error
    assert not gw_mock.was_called("PUT", STATUS_PATH)


@pytest.mark.asyncio
async def test_set_mode_gateway_422_surfaces_as_error(make_ctx, gw_mock):
    gw_mock.put(STATUS_PATH, json={"detail": "invalid coding_remote: bad surface"}, status=422)

    res = await h.fn_set(make_ctx(), h.SetParams(mode="tg"))

    assert res.status == "error"
    assert "422" in res.error
    assert "invalid coding_remote" in res.error


# ─── send_instruction ─────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_send_instruction_success_posts_steer_and_reports_ok(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": f"coding-{UID}-abc123"})

    res = await h.fn_send(make_ctx(), h.SendParams(text="run the tests"))

    assert res.status == "success"
    assert gw_mock.was_called("POST", STEER_PATH)
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body == {"text": "run the tests"}
    assert res.data.active is True
    assert res.data.session_id == f"coding-{UID}-abc123"


@pytest.mark.asyncio
async def test_send_instruction_uses_ctx_user_id_only_never_a_param(make_ctx, gw_mock):
    other_uid = "imp_u_OTHER"
    gw_mock.post(f"/v1/internal/coding-remote/{other_uid}/steer",
                 json={"ok": True, "session_id": f"coding-{other_uid}-xyz"})

    res = await h.fn_send(make_ctx(imperal_id=other_uid), h.SendParams(text="hi"))
    assert res.status == "success"
    assert gw_mock.was_called("POST", f"/v1/internal/coding-remote/{other_uid}/steer")
    assert not gw_mock.was_called("POST", STEER_PATH)


# ─── send_instruction origin honesty (v2): surface in the steer body ─── #

@pytest.mark.asyncio
async def test_send_instruction_omits_surface_when_ctx_does_not_expose_it(make_ctx, gw_mock):
    """SDK Context 5.9.x exposes no turn surface — the field is OMITTED and the
    gateway applies its default (web-panel). Never a fabricated value."""
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(), h.SendParams(text="run the tests"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body == {"text": "run the tests"}
    assert "surface" not in body


@pytest.mark.asyncio
async def test_send_instruction_passes_surface_from_ctx_attr(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(surface="telegram"), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body["surface"] == "telegram"


@pytest.mark.asyncio
async def test_send_instruction_reads_surface_from_metadata_fallback(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(metadata={"surface": "discord"}), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body["surface"] == "discord"


@pytest.mark.asyncio
async def test_send_instruction_normalizes_panel_to_web_panel(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(surface="panel"), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body["surface"] == "web-panel"


@pytest.mark.asyncio
async def test_send_instruction_surface_case_and_whitespace_insensitive(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(surface="  Telegram "), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert body["surface"] == "telegram"


@pytest.mark.asyncio
async def test_send_instruction_unknown_surface_is_omitted_not_guessed(make_ctx, gw_mock):
    """Unknown value -> OMIT (gateway defaults web-panel); never send junk the
    endpoint would 422, never silently coerce to a wrong origin."""
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(surface="smoke-signal"), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert "surface" not in body


@pytest.mark.asyncio
async def test_send_instruction_non_string_surface_is_omitted(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_send(make_ctx(surface=42), h.SendParams(text="hi"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", STEER_PATH).content)
    assert "surface" not in body


def test_turn_surface_vocab_is_exactly_the_core_four():
    assert h._SURFACES == {"telegram", "web-panel", "discord", "api"}


# ─── stop_session ─────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_stop_session_posts_stop_and_reports_ok(make_ctx, gw_mock):
    gw_mock.post(STOP_PATH, json={"ok": True, "session_id": f"coding-{UID}-abc123"})

    res = await h.fn_stop(make_ctx(), h.EmptyParams())

    assert res.status == "success"
    assert gw_mock.was_called("POST", STOP_PATH)
    body = json.loads(gw_mock.last_request("POST", STOP_PATH).content)
    assert body == {}
    assert "stop sent to your coding session" in res.summary
    assert res.data.session_id == f"coding-{UID}-abc123"


@pytest.mark.asyncio
async def test_stop_session_uses_ctx_user_id_only_never_a_param(make_ctx, gw_mock):
    other_uid = "imp_u_OTHER"
    gw_mock.post(f"/v1/internal/coding-remote/{other_uid}/stop", json={"ok": True})

    res = await h.fn_stop(make_ctx(imperal_id=other_uid), h.EmptyParams())
    assert res.status == "success"
    assert gw_mock.was_called("POST", f"/v1/internal/coding-remote/{other_uid}/stop")
    assert not gw_mock.was_called("POST", STOP_PATH)


@pytest.mark.asyncio
async def test_stop_session_idle_409_is_honest_clean_error(make_ctx, gw_mock):
    """No running session -> honest no-op error, gateway reason surfaced as-is."""
    gw_mock.post(STOP_PATH, json={"detail": "no running coding turn"}, status=409)

    res = await h.fn_stop(make_ctx(), h.EmptyParams())

    assert res.status == "error"
    assert "409" in res.error
    assert "no running coding turn" in res.error
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_stop_session_gateway_unreachable_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("POST", STOP_PATH, httpx.ConnectError("boom"))

    res = await h.fn_stop(make_ctx(), h.EmptyParams())

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


# ─── set_coding_mode (consent mode: default | plan | autopilot) ───────── #

@pytest.mark.asyncio
async def test_set_coding_mode_plan_posts_mode_and_reports_ok(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": f"coding-{UID}-abc123"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan"))

    assert res.status == "success"
    assert gw_mock.was_called("POST", MODE_PATH)
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert body == {"mode": "plan"}
    assert "coding mode → plan" in res.summary
    assert res.data.session_id == f"coding-{UID}-abc123"


@pytest.mark.asyncio
async def test_set_coding_mode_autopilot_summary_notes_terminal_confirm(make_ctx, gw_mock):
    """Autopilot never applies silently — the summary must say the terminal
    will ask its local user to confirm, so the narrator relays that FACT."""
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="autopilot"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert body == {"mode": "autopilot"}
    assert "coding mode → autopilot" in res.summary
    assert "asks the terminal to confirm" in res.summary


@pytest.mark.asyncio
async def test_set_coding_mode_downgrade_summary_has_no_confirm_note(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="default"))

    assert res.status == "success"
    assert "coding mode → default" in res.summary
    assert "confirm" not in res.summary


@pytest.mark.asyncio
async def test_set_coding_mode_case_and_whitespace_insensitive(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="  Plan  "))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert body == {"mode": "plan"}


@pytest.mark.asyncio
async def test_set_coding_mode_invalid_mode_errors_before_any_write(make_ctx, gw_mock):
    # No POST route registered — an unknown mode must be rejected before any write.
    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="yolo"))

    assert res.status == "error"
    assert "mode must be one of: default, plan, autopilot" in res.error
    assert not gw_mock.was_called("POST", MODE_PATH)


@pytest.mark.asyncio
async def test_set_coding_mode_rejects_routing_vocab(make_ctx, gw_mock):
    """The routing modes (tg/panel/both/off) belong to set_mode — the consent
    tool must refuse them, never silently cross the two vocabularies."""
    for routing_mode in ("tg", "panel", "both", "off"):
        res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode=routing_mode))
        assert res.status == "error", routing_mode
    assert not gw_mock.was_called("POST", MODE_PATH)


@pytest.mark.asyncio
async def test_set_coding_mode_uses_ctx_user_id_only_never_a_param(make_ctx, gw_mock):
    other_uid = "imp_u_OTHER"
    gw_mock.post(f"/v1/internal/coding-remote/{other_uid}/mode",
                 json={"ok": True, "session_id": f"coding-{other_uid}-xyz"})

    res = await h.fn_coding_mode(make_ctx(imperal_id=other_uid), h.CodingModeParams(mode="plan"))
    assert res.status == "success"
    assert gw_mock.was_called("POST", f"/v1/internal/coding-remote/{other_uid}/mode")
    assert not gw_mock.was_called("POST", MODE_PATH)


@pytest.mark.asyncio
async def test_set_coding_mode_no_active_session_409_is_clean_error(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"detail": "no active coding session"}, status=409)

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan"))

    assert res.status == "error"
    assert "409" in res.error
    assert "no active coding session" in res.error
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_set_coding_mode_autopilot_not_allowed_422_reason_surfaced(make_ctx, gw_mock):
    """Autopilot from a surface outside the steer allowlist — the gateway's
    refusal reason is surfaced as-is (honest refuse), no URL/IP leaked."""
    gw_mock.post(MODE_PATH,
                 json={"detail": "autopilot requires the surface in the steer allowlist"},
                 status=422)

    res = await h.fn_coding_mode(make_ctx(surface="telegram"), h.CodingModeParams(mode="autopilot"))

    assert res.status == "error"
    assert "422" in res.error
    assert "steer allowlist" in res.error
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_set_coding_mode_gateway_unreachable_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("POST", MODE_PATH, httpx.ConnectError("boom"))

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan"))

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_set_coding_mode_passes_surface_from_ctx_attr(make_ctx, gw_mock):
    """Origin rides along when readable — the gateway needs it for the
    autopilot steer-allowlist check + origin-honest tagging."""
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_coding_mode(make_ctx(surface="telegram"), h.CodingModeParams(mode="autopilot"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert body == {"mode": "autopilot", "surface": "telegram"}


@pytest.mark.asyncio
async def test_set_coding_mode_omits_surface_when_ctx_does_not_expose_it(make_ctx, gw_mock):
    gw_mock.post(MODE_PATH, json={"ok": True, "session_id": "s"})

    res = await h.fn_coding_mode(make_ctx(), h.CodingModeParams(mode="plan"))

    assert res.status == "success"
    body = json.loads(gw_mock.last_request("POST", MODE_PATH).content)
    assert "surface" not in body


def test_coding_mode_vocab_is_exactly_default_plan_autopilot():
    assert h._CODING_MODES == ("default", "plan", "autopilot")


# ─── 409 no-session: clean ActionResult.error, NO internal URL ───────── #

@pytest.mark.asyncio
async def test_send_instruction_no_active_session_409_is_clean_error(make_ctx, gw_mock):
    gw_mock.post(STEER_PATH, json={"detail": "no active coding session"}, status=409)

    res = await h.fn_send(make_ctx(), h.SendParams(text="run the tests"))

    assert res.status == "error"
    assert "no active coding session" in res.error
    assert "409" in res.error
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


# ─── Gateway errors never leak an internal URL/IP ─────────────────────── #

@pytest.mark.asyncio
async def test_get_status_gateway_connect_error_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("GET", STATUS_PATH, httpx.ConnectError("boom"))

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_get_status_gateway_5xx_status_error_has_no_internal_url(make_ctx, gw_mock):
    # gw_get calls r.raise_for_status(), whose httpx.HTTPStatusError message
    # embeds the full request URL — the exact leak vector _safe_err exists for.
    gw_mock.get(STATUS_PATH, json={"detail": "boom"}, status=500)

    res = await h.fn_status(make_ctx(), h.EmptyParams())

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_set_mode_gateway_unreachable_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("PUT", STATUS_PATH, httpx.ConnectError("boom"))

    res = await h.fn_set(make_ctx(), h.SetParams(mode="tg"))

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


@pytest.mark.asyncio
async def test_send_instruction_gateway_unreachable_has_no_internal_url(make_ctx, gw_mock):
    gw_mock.error("POST", STEER_PATH, httpx.ConnectError("boom"))

    res = await h.fn_send(make_ctx(), h.SendParams(text="run the tests"))

    assert res.status == "error"
    assert "104.224" not in res.error
    assert "http://" not in res.error
    assert "https://" not in res.error


# ─── Security: no user_id in the write-surface ───────────────────────── #

def test_params_models_have_no_user_id_field():
    """Tools operate ONLY on ctx.user.imperal_id — a caller must never be able
    to pass a foreign user_id through params."""
    assert "user_id" not in h.EmptyParams.model_fields
    assert "user_id" not in h.SetParams.model_fields
    assert "user_id" not in h.SendParams.model_fields
    assert "user_id" not in h.CodingModeParams.model_fields
