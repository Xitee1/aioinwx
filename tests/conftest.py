"""Fixtures for the aioinwx tests."""

from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aioinwx import InwxClient, client as client_module


@dataclass
class Request:
    """Request received by the fake API."""

    method: str
    params: dict[str, Any]
    cookies: dict[str, str]


@dataclass
class FakeApi:
    """Fake DomRobot JSON-RPC API that answers with prepared responses."""

    url: str = ""
    responses: list[web.StreamResponse] = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)

    def respond(self, body: Any, cookies: dict[str, str] | None = None) -> None:
        """Add a JSON response."""
        response = web.json_response(body)
        for name, value in (cookies or {}).items():
            response.set_cookie(name, value)
        self.responses.append(response)

    def respond_raw(self, response: web.StreamResponse) -> None:
        """Add a raw response."""
        self.responses.append(response)

    def ok(self, res_data: dict[str, Any] | None = None) -> None:
        """Add a successful response."""
        body: dict[str, Any] = {"code": 1000, "msg": "Command completed successfully"}
        if res_data is not None:
            body["resData"] = res_data
        self.respond(body)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Record the request and return the next response."""
        payload = await request.json()
        self.requests.append(Request(payload["method"], payload["params"], dict(request.cookies)))
        return self.responses.pop(0)


@pytest.fixture
async def fake_api() -> AsyncGenerator[FakeApi]:
    """Return a running fake API."""
    api = FakeApi()
    app = web.Application()
    app.router.add_post("/jsonrpc/", api.handle)
    server = TestServer(app)
    await server.start_server()
    api.url = str(server.make_url("/jsonrpc/"))
    yield api
    await server.close()
    assert not api.responses, "Not all prepared responses were requested"


@pytest.fixture
async def make_client(
    fake_api: FakeApi,
) -> AsyncGenerator[Callable[..., InwxClient]]:
    """Return a factory for clients of the fake API."""
    clients: list[InwxClient] = []

    def factory(shared_secret: str | None = None) -> InwxClient:
        client = InwxClient("user", "pass", shared_secret, url=fake_api.url)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()


@pytest.fixture(autouse=True)
def reset_totp_steps() -> Generator[None]:
    """Reset the remembered TOTP steps between tests."""
    yield
    client_module._last_totp_step.clear()
