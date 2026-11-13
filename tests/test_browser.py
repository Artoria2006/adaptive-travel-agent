import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from travel.main import create_app
from travel.providers import Providers

pytestmark = pytest.mark.browser


def test_web_stream_followup_and_history(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(tmp_path / "runs", Providers(tmp_path / "cache", demo=True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(url, timeout=0.2).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel=os.getenv("TRAVEL_BROWSER_CHANNEL") or None)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.get_by_role("button", name="填入示例").click()
            page.get_by_role("button", name="生成行程").click()
            page.get_by_text("验证通过", exact=True).wait_for()
            page.get_by_text("版本 Manifest", exact=True).wait_for()
            assert page.locator(".day").count() == 5
            assert page.locator("#map").is_visible()
            assert page.locator("#map .leaflet-marker-icon").count() >= 1
            assert page.get_by_role("link", name="OpenStreetMap", exact=True).is_visible()
            page.route("**/api/runs/*/route?*", lambda route: route.fulfill(json={
                "status": "available", "route": {"coordinates": [[35.68, 139.76], [35.69, 139.77]],
                                                "distance_m": 1000, "duration_s": 700},
            }))
            page.get_by_role("button", name="查询步行备选").first.click()
            page.get_by_text("步行备选 · 1000 米", exact=False).wait_for()
            assert page.locator("#map path.leaflet-interactive").count() == 1
            page.locator("#map-day").select_option("4")
            assert "Osaka" in page.locator("#map-day option:checked").inner_text()
            page.locator("#request").fill("我还想加入京都，但总预算不变。")
            page.get_by_role("button", name="生成行程").click()
            page.get_by_role("heading", name="第 5 天 · Kyoto").wait_for()
            page.get_by_role("button", name="Tokyo → Osaka · 验证通过", exact=True).click()
            page.get_by_role("heading", name="第 5 天 · Osaka").wait_for()
            assert not errors
            if output := os.getenv("TRAVEL_SCREENSHOT"):
                page.screenshot(path=output, full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
