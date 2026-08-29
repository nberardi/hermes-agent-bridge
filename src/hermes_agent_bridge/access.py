"""Cloudflare Access JWT check at the origin.

Rejects requests that never went through Access, including docker-network
neighbors. Loopback GET /healthz is the only skip (container HEALTHCHECK).
MCP, card-create, and health-as-a-tool still require a JWT even on localhost.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import jwt
from jwt import PyJWKClient
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

JWT_HEADER = "cf-access-jwt-assertion"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(scope: Scope) -> bool:
    client = scope.get("client")
    if not client:
        return False
    return client[0] in _LOOPBACK


class AccessJWTMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        jwks_url: str,
        audience: str,
        issuer: str,
        skip_paths: Iterable[str] = (),
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        self.app = app
        self.audience = audience
        self.issuer = issuer
        self.skip_paths = set(skip_paths)
        self._jwks = jwks_client or PyJWKClient(jwks_url, cache_keys=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path in self.skip_paths:
            await self.app(scope, receive, send)
            return
        # Container liveness only. Neighbors on the docker network still need a JWT.
        if path == "/healthz" and _is_loopback(scope):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers") or []
        }
        token = (headers.get(JWT_HEADER) or "").strip()
        if not token:
            await JSONResponse(
                {"error": "missing Cloudflare Access JWT"}, status_code=401
            )(scope, receive, send)
            return
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            log.info("access jwt rejected: %s", exc)
            await JSONResponse(
                {"error": "invalid Cloudflare Access JWT"}, status_code=401
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
