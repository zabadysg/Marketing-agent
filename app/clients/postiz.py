from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings


class PostizError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PostizAuthError(PostizError):
    pass


class PostizNotFoundError(PostizError):
    pass


class PostizRateLimitError(PostizError):
    pass


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    msg = f"Postiz API error {response.status_code}: {response.text}"
    if response.status_code in (401, 403):
        raise PostizAuthError(msg, response.status_code)
    if response.status_code == 404:
        raise PostizNotFoundError(msg, response.status_code)
    if response.status_code == 429:
        raise PostizRateLimitError(msg, response.status_code)
    raise PostizError(msg, response.status_code)


async def _with_backoff(coro_fn, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await coro_fn()
        except PostizRateLimitError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt * 5)
    return None  # unreachable


class PostizClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        resolved_api_url = (base_url or settings.postiz_api_url).rstrip("/")
        # Postiz's internal nginx routes /api/ → NestJS backend
        # Public API endpoints are registered as /public/v1/... on NestJS,
        # so external callers must use /api/public/v1/...
        self._base_url = f"{resolved_api_url}/api/public/v1"
        self._api_key = api_key or settings.postiz_api_key.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    async def __aenter__(self) -> PostizClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_integrations(self) -> list[dict[str, Any]]:
        async def _call():
            response = await self._client.get("/integrations")
            _raise_for_status(response)
            return response.json()

        return await _with_backoff(_call)

    async def schedule_post(
        self,
        integration_id: str,
        content: str,
        provider: str,
        scheduled_at: datetime,
        images: list[dict[str, Any]] | None = None,
        post_type: str = "schedule",
    ) -> dict[str, Any]:
        date_str = (
            scheduled_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )
        payload: dict[str, Any] = {
            "type": post_type,
            "date": date_str,
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [{"content": content, "image": images or []}],
                    "settings": {
                        "__type": provider,
                        **({"who_can_reply_post": "everyone"} if provider == "x" else {}),
                    },
                }
            ],
        }

        async def _call():
            response = await self._client.post("/posts", json=payload)
            _raise_for_status(response)
            return response.json()

        return await _with_backoff(_call)

    async def get_posts(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if start_date:
            params["startDate"] = start_date.astimezone(timezone.utc).isoformat()
        if end_date:
            params["endDate"] = end_date.astimezone(timezone.utc).isoformat()

        async def _call():
            response = await self._client.get("/posts", params=params)
            _raise_for_status(response)
            data = response.json()
            return data.get("posts", data) if isinstance(data, dict) else data

        return await _with_backoff(_call)

    async def upload_media(self, file_content: bytes, filename: str) -> dict[str, Any]:
        async def _call():
            files = {"file": (filename, file_content)}
            response = await self._client.post(
                "/upload",
                files=files,
                headers={"Authorization": self._api_key},
            )
            _raise_for_status(response)
            return response.json()

        return await _with_backoff(_call)

    async def upload_media_from_url(self, url: str) -> dict[str, Any]:
        async def _call():
            response = await self._client.post("/upload-from-url", json={"url": url})
            _raise_for_status(response)
            return response.json()

        return await _with_backoff(_call)

    async def delete_post(self, post_id: str) -> None:
        async def _call():
            response = await self._client.delete(f"/posts/{post_id}")
            _raise_for_status(response)

        await _with_backoff(_call)
