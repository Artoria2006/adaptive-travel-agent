from datetime import date

import httpx
from fastapi.testclient import TestClient

from travel.japan import attractions
from travel.main import create_app
from travel.planner import opening
from travel.providers import Providers


def test_japan_uses_published_prices_and_closures():
    tokyo = attractions("Tokyo")[-1]
    osaka = attractions("Osaka")[2]
    kyoto = attractions("Kyoto")[0]
    assert [place.price_jpy for place in (tokyo, osaka, kyoto)] == [1000, 1200, 1200]
    assert opening(tokyo, date(2026, 11, 16), 570, 1080) is None
    assert opening(osaka, date(2026, 12, 28), 540, 1080) is None
    assert opening(kyoto, date(2026, 11, 18), 600, 1080) is None
    assert opening(kyoto, date(2026, 11, 19), 600, 1080) == 600
    collection = attractions("Kyoto")[-1]
    assert opening(collection, date(2026, 11, 19), 600, 1080) is None
    assert opening(collection, date(2026, 12, 23), 600, 1080) == 600


def test_default_japan_has_no_demo_and_osrm_geometry(tmp_path):
    def fixture(request):
        if request.url.host == "api.frankfurter.dev":
            return httpx.Response(200, json={"rate": 23.9})
        assert "routed-foot" in str(request.url)
        return httpx.Response(200, json={"code": "Ok", "routes": [{
            "distance": 1240, "duration": 970,
            "geometry": {"coordinates": [[139.77, 35.69], [139.78, 35.70], [139.79, 35.71]]},
        }]})

    providers = Providers(tmp_path / "cache", online=True, transport=httpx.MockTransport(fixture))
    with TestClient(create_app(tmp_path / "runs", providers)) as client:
        result = client.post("/api/plan", json={
            "text": "我想去东京和大阪和京都玩五天，2026-11-19，预算 5000 元人民币，喜欢动漫"
        }).json()
        assert result["status"] == "partial"
        days = result["plan"]["days"]
        items = [item for day in days for item in day["items"]]
        assert not any(item["evidence"]["confidence"] == "demo" for item in items)
        assert [item["price_jpy"] for item in items if item["kind"] == "transfer"] == [14520, 2880]
        assert any(row["price_jpy"] == 1200 for row in result["attraction_catalog"])
        day = next(day for day in days if len(day["items"]) >= 2
                   and all(item["kind"] == "visit" for item in day["items"][:2]))
        route = client.get(f"/api/runs/{result['run_id']}/route",
                           params={"day": day["number"], "start": 0, "end": 1, "mode": "walk"}).json()
        assert route["route"]["coordinates"][1] == [35.70, 139.78]
