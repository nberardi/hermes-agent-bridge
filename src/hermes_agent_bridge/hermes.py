"""Internal HTTP client for Hermes dashboard REST and optional :8642 ask.

Never calls specify, decompose, dispatch, run approval, or status=ready/running.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings

KANBAN = "/api/plugins/kanban"

# Paths this bridge will never call, even if a future tool is added.
_FORBIDDEN_SUBSTRINGS = (
    "/specify",
    "/decompose",
    "/dispatch",
    "/approval",
    "/v1/runs/",
)

# Fail closed: do not wait on a dead :8642.
_ASK_TIMEOUT = httpx.Timeout(10.0, connect=2.0)
_DASH_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._s = settings
        headers = {}
        if settings.dashboard_token:
            headers["Authorization"] = f"Bearer {settings.dashboard_token}"
        self._http = httpx.AsyncClient(
            base_url=settings.dashboard_url,
            headers=headers,
            timeout=_DASH_TIMEOUT,
            transport=transport,
        )
        self._api: httpx.AsyncClient | None = None
        if settings.ask_enabled:
            api_headers = {}
            if settings.api_key:
                api_headers["Authorization"] = f"Bearer {settings.api_key}"
            self._api = httpx.AsyncClient(
                base_url=settings.api_url,
                headers=api_headers,
                timeout=_ASK_TIMEOUT,
                transport=transport,
            )

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._api:
            await self._api.aclose()

    def _guard(self, path: str) -> None:
        lowered = path.lower()
        for needle in _FORBIDDEN_SUBSTRINGS:
            if needle in lowered:
                raise HermesError(f"refusing to call Hermes path {path}")

    async def dashboard_get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._guard(path)
        r = await self._http.get(path, params=params)
        r.raise_for_status()
        return r.json() if r.content else None

    async def dashboard_post(
        self,
        path: str,
        json: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        self._guard(path)
        r = await self._http.post(path, json=json, params=params)
        r.raise_for_status()
        return r.json() if r.content else None

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "site": self._s.site,
            "dashboard": "unknown",
            "ask": "disabled",
        }
        try:
            r = await self._http.get("/api/health")
            if r.status_code == 404:
                r = await self._http.get("/health")
            out["dashboard"] = "ok" if r.is_success else f"http_{r.status_code}"
            out["dashboard_status"] = r.status_code
        except httpx.HTTPError as exc:
            out["dashboard"] = f"error:{exc.__class__.__name__}"
        if self._api:
            try:
                r = await self._api.get("/health")
                out["ask"] = "ok" if r.is_success else f"http_{r.status_code}"
            except httpx.HTTPError as exc:
                out["ask"] = f"error:{exc.__class__.__name__}"
        return out

    def _board_params(self, board: str) -> dict[str, str]:
        board = board.strip()
        if board not in self._s.allowed_kanban_boards:
            raise HermesError(f"board is not allowed: {board!r}")
        return {"board": board}

    async def list_board(self, board: str) -> Any:
        return await self.dashboard_get(
            f"{KANBAN}/board", params=self._board_params(board)
        )

    async def get_task(self, board: str, task_id: str) -> Any:
        return await self.dashboard_get(
            f"{KANBAN}/tasks/{task_id}", params=self._board_params(board)
        )

    async def create_queued_card(self, board: str, title: str, body: str = "") -> Any:
        """Queue work without auto-start.

        Hermes POST /tasks with triage=true and no assignee stays in triage
        and does not dispatch. We never set status, never assign, never specify.
        """
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "triage": True,
        }
        return await self.dashboard_post(
            f"{KANBAN}/tasks",
            json=payload,
            params=self._board_params(board),
        )

    async def ask(self, prompt: str, model: str | None = None) -> str:
        if not self._api:
            raise HermesError("ask is disabled: HERMES_API_URL not set")
        try:
            probe = await self._api.get("/health")
        except httpx.HTTPError as exc:
            raise HermesError("ask is disabled: Hermes :8642 is not enabled") from exc
        if not probe.is_success:
            raise HermesError("ask is disabled: Hermes :8642 is not enabled")
        body: dict[str, Any] = {
            "model": model or "hermes",
            "messages": [{"role": "user", "content": prompt}],
        }
        r = await self._api.post("/v1/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesError(f"unexpected ask response: {data!r}") from exc
