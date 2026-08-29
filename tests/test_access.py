"""Fail-closed 1–2: unauth origin and JWT at the origin, not only the edge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Scope

from hermes_agent_bridge.access import AccessJWTMiddleware

TEAM_ISS = "https://example.cloudflareaccess.com"
THIS_AUD = "this-app-aud"
OTHER_AUD = "hermes-dashboard-aud"

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public = _private.public_key()


class _StubJWK:
    def get_signing_key_from_jwt(self, token: str):
        # Garbage tokens fail before a network JWKS fetch.
        jwt.get_unverified_header(token)

        class _Key:
            key = _public

        return _Key()


def _token(*, aud: str = THIS_AUD, exp_delta: timedelta = timedelta(hours=1), extra: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "aud": aud,
        "iss": TEAM_ISS,
        "iat": now,
        "exp": now + exp_delta,
        **(extra or {}),
    }
    return jwt.encode(payload, _private, algorithm="RS256")


def _app() -> Starlette:
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"]), Route("/healthz", ok)])
    app.add_middleware(
        AccessJWTMiddleware,
        jwks_url=f"{TEAM_ISS}/cdn-cgi/access/certs",
        audience=THIS_AUD,
        issuer=TEAM_ISS,
        jwks_client=_StubJWK(),
    )
    return app


def test_missing_jwt_get_and_post_are_401():
    c = TestClient(_app())
    for method in ("get", "post"):
        r = getattr(c, method)("/mcp")
        assert r.status_code == 401, r.text
        assert "missing" in r.json()["error"].lower()


def test_empty_jwt_is_401():
    r = TestClient(_app()).post("/mcp", headers={"Cf-Access-Jwt-Assertion": "   "})
    assert r.status_code == 401
    assert "missing" in r.json()["error"].lower()


def test_garbage_jwt_is_401():
    r = TestClient(_app()).post("/mcp", headers={"Cf-Access-Jwt-Assertion": "not-a-jwt"})
    assert r.status_code == 401
    assert "invalid" in r.json()["error"].lower()


def test_expired_jwt_is_401():
    token = _token(exp_delta=timedelta(hours=-1))
    r = TestClient(_app()).post("/mcp", headers={"Cf-Access-Jwt-Assertion": token})
    assert r.status_code == 401
    assert "invalid" in r.json()["error"].lower()


def test_wrong_aud_is_401():
    token = _token(aud=OTHER_AUD)
    r = TestClient(_app()).post("/mcp", headers={"Cf-Access-Jwt-Assertion": token})
    assert r.status_code == 401
    assert "invalid" in r.json()["error"].lower()


def test_this_app_aud_is_ok():
    token = _token(aud=THIS_AUD)
    r = TestClient(_app()).post("/mcp", headers={"Cf-Access-Jwt-Assertion": token})
    assert r.status_code == 200
    assert r.text == "ok"


def test_neighbor_healthz_without_jwt_is_401():
    """TestClient peer is not loopback; a docker neighbor must not skip JWT."""
    r = TestClient(_app()).get("/healthz")
    assert r.status_code == 401


def test_loopback_healthz_without_jwt_is_ok():
    inner = Starlette(routes=[Route("/healthz", lambda r: PlainTextResponse("ok"))])
    app = AccessJWTMiddleware(
        inner,
        jwks_url=f"{TEAM_ISS}/cdn-cgi/access/certs",
        audience=THIS_AUD,
        issuer=TEAM_ISS,
        jwks_client=_StubJWK(),
    )

    async def _call() -> int:
        status = {"code": 0}

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/healthz",
            "raw_path": b"/healthz",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 9),
            "server": ("127.0.0.1", 8080),
        }
        await app(scope, receive, send)
        return status["code"]

    import asyncio

    assert asyncio.run(_call()) == 200


def _status_for(*, path: str, method: str = "GET", client: tuple[str, int], headers: list[tuple[bytes, bytes]] | None = None) -> int:
    inner = Starlette(
        routes=[
            Route("/healthz", lambda r: PlainTextResponse("ok")),
            Route("/mcp", lambda r: PlainTextResponse("ok"), methods=["GET", "POST"]),
        ]
    )
    app = AccessJWTMiddleware(
        inner,
        jwks_url=f"{TEAM_ISS}/cdn-cgi/access/certs",
        audience=THIS_AUD,
        issuer=TEAM_ISS,
        jwks_client=_StubJWK(),
    )

    async def _call() -> int:
        status = {"code": 0}

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers or [],
            "client": client,
            "server": ("127.0.0.1", 8080),
        }
        await app(scope, receive, send)
        return status["code"]

    import asyncio

    return asyncio.run(_call())


def test_forwarded_loopback_on_healthz_is_401():
    """X-Forwarded-For must not count as loopback, even if the socket looks local."""
    headers = [(b"x-forwarded-for", b"127.0.0.1")]
    # Neighbor socket + spoofed XFF
    assert _status_for(path="/healthz", client=("203.0.113.10", 9), headers=headers) == 401
    # uvicorn proxy_headers rewrite: client becomes 127.0.0.1 but XFF remains
    assert _status_for(path="/healthz", client=("127.0.0.1", 9), headers=headers) == 401


def test_forwarded_loopback_on_mcp_is_401():
    headers = [(b"x-forwarded-for", b"127.0.0.1")]
    assert _status_for(path="/mcp", method="POST", client=("203.0.113.10", 9), headers=headers) == 401
    assert _status_for(path="/mcp", method="POST", client=("127.0.0.1", 9), headers=headers) == 401


def test_uvicorn_does_not_trust_proxy_headers():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "hermes_agent_bridge" / "server.py").read_text()
    assert "proxy_headers=True" not in src
    assert "proxy_headers=False" in src
