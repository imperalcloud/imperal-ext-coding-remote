"""Test harness for imperal-ext-coding-remote. The tools only talk to the
gateway over HTTP (httpx), so tests mock the three gateway routes (GET/PUT
/v1/internal/coding-remote/{user_id} + POST .../steer) via httpx.MockTransport
— no real network, no third-party mocking library (the validation host's
worker venv has httpx+pytest but NOT respx)."""
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

# Make the ext modules importable (they use bare `import app`, `from app import …`).
# MUST run before any test module does `import app` / `import handlers` / `import panels`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402 (import after sys.path fix-up, see above)


@pytest.fixture
def make_ctx():
    """Factory fixture: minimal ctx stand-in — handlers use ctx.user.imperal_id
    plus (tolerantly) a turn surface via ``ctx.surface`` / ``ctx._metadata``.
    Auto-discovered by pytest (no cross-module import), so it is portable
    regardless of pytest rootdir / import-mode / how the validation host
    invokes pytest.

    ``surface`` sets a ``ctx.surface`` attribute; ``metadata`` sets
    ``ctx._metadata`` — both optional, mirroring that the real SDK Context
    (5.9.x) exposes NEITHER today (the default ctx has neither attribute)."""
    def _make(imperal_id: str = "imp_u_TEST", surface=None, metadata=None):
        user = SimpleNamespace(imperal_id=imperal_id)
        ctx = SimpleNamespace(user=user)
        if surface is not None:
            ctx.surface = surface
        if metadata is not None:
            ctx._metadata = metadata
        return ctx
    return _make


class GatewayMock:
    """Routes (method, path) -> canned httpx.Response (or an Exception to
    raise, e.g. httpx.ConnectError) through an httpx.MockTransport, and
    monkeypatches app.httpx.AsyncClient so every gw_get/gw_put/gw_post call
    made from app.py is served by the mock instead of hitting the network.

    Every request that passes through is recorded so tests can assert on
    the PUT/POST body / whether a route was actually called."""

    def __init__(self, monkeypatch):
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[httpx.Request] = []
        self._install(monkeypatch)

    def _install(self, monkeypatch) -> None:
        handler = self._handler
        real_async_client = httpx.AsyncClient  # capture BEFORE patching — the
        # factory below must construct the real client, not recurse into itself.

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        # app.httpx IS the shared httpx module object (bare `import httpx`),
        # so this patches httpx.AsyncClient for the duration of the test only
        # (monkeypatch auto-restores at teardown).
        monkeypatch.setattr(app_mod.httpx, "AsyncClient", factory)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        spec = self.routes.get(key)
        if spec is None:
            raise AssertionError(f"gw_mock: no route registered for {key}")
        if isinstance(spec, BaseException):
            raise spec
        if callable(spec):
            return spec(request)
        return spec

    def get(self, path: str, *, json=None, status: int = 200) -> None:
        self.routes[("GET", path)] = httpx.Response(status, json=json)

    def put(self, path: str, *, json=None, status: int = 200) -> None:
        self.routes[("PUT", path)] = httpx.Response(status, json=json)

    def post(self, path: str, *, json=None, status: int = 200) -> None:
        self.routes[("POST", path)] = httpx.Response(status, json=json)

    def error(self, method: str, path: str, exc: BaseException) -> None:
        self.routes[(method, path)] = exc

    def was_called(self, method: str, path: str) -> bool:
        return any(r.method == method and r.url.path == path for r in self.calls)

    def last_request(self, method: str, path: str) -> httpx.Request:
        for r in reversed(self.calls):
            if r.method == method and r.url.path == path:
                return r
        raise AssertionError(f"gw_mock: no recorded request for {(method, path)}")


@pytest.fixture
def gw_mock(monkeypatch):
    """See GatewayMock above — the no-respx replacement for gateway mocking."""
    return GatewayMock(monkeypatch)
