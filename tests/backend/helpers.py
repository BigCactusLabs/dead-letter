from __future__ import annotations

from httpx import AsyncClient


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/session")
    return {"X-Dead-Letter-CSRF": response.json()["csrf_token"]}
