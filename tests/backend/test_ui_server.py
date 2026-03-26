from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from dead_letter.backend.ui_server import create_ui_app


@pytest.mark.anyio
async def test_ui_server_serves_index_and_static_assets() -> None:
    app = create_ui_app(frontend_root=Path("src/dead_letter/frontend"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        index = await client.get("/")
        script = await client.get("/static/app.js")

    assert index.status_code == 200
    assert "dead-letter" in index.text
    assert "Drop .eml files to convert" in index.text
    assert '<script type="module" src="/static/app.js"></script>' in index.text
    assert "/static/vendor/alpine.min.js" not in index.text
    assert "/static/vendor/htmx.min.js" not in index.text
    assert script.status_code == 200
    assert "deadLetterApp" in script.text
