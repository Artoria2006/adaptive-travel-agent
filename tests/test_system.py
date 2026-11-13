import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from travel.audit import Audit, digest
from travel.engine import Expert, Gateway, Registry, parse_intent, task_graph
from travel.main import create_app
from travel.models import Cities, Intent, Task, TaskInput
from travel.providers import GEOCODE, Providers

SAMPLE = "我想去东京和大阪玩五天，预算 5000 元人民币，喜欢动漫，不想走太多路。"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ("TRAVEL_LLM_KEY", "TRAVEL_ONLINE", "TRAVEL_DISABLED", "TRAVEL_OVERPASS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRAVEL_ONLINE", "0")
    monkeypatch.setenv("TRAVEL_DEMO", "1")


def request(client, text, **fields):
    response = client.post("/api/plan", json={"text": text, **fields})
    assert response.status_code == 200
    return response.json()


def test_supervisor_and_dag():
    intent = parse_intent(SAMPLE)
    assert (intent.destinations, intent.days, intent.budget, intent.currency) == (["Tokyo", "Osaka"], 5, 5000, "CNY")
    assert intent.walking_km == 2 and intent.interests == ["动漫"]
    assert parse_intent("东京三日游，预算 3000 元").days == 3
    assert parse_intent("我想去东京和 Paris 玩五天，预算 5000 元").destinations == ["Tokyo", "Paris"]
    graph = task_graph(intent)
    seen = set()
    for task in graph:
        assert set(task.depends_on) <= seen
        seen.add(task.id)
    assert "transfer" in seen and "weather" not in seen


def test_single_city_manifest_and_replay(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        result = request(client, "东京三日游，预算 3500 元")
        assert result["status"] == "success"
        audit = client.get("/api/runs/" + result["run_id"]).json()
        assert audit["mode"] == "audit_replay"
        assert audit["final_plan"] == result
        assert all(row["capability"] != "cross_city" for row in audit["routing_trace"])
        assert [row["sequence"] for row in audit["events"]] == list(range(1, len(audit["events"]) + 1))
        assert audit["manifest"]["build"] and audit["manifest"]["source_hash"]
        assert audit["manifest"]["dependency_lock_hash"]
        for value in audit["evidence"].values():
            assert value["sha256"] == digest(value["snapshot"])
        assert "text" not in audit["request"]


def test_multicity_followup(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        first = request(client, SAMPLE)
        second = request(client, "我还想加入京都，但总预算不变。", previous_run=first["run_id"])
        for result, cities in [(first, {"Tokyo", "Osaka"}), (second, {"Tokyo", "Osaka", "Kyoto"})]:
            assert result["status"] == "success"
            assert {day["city"] for day in result["plan"]["days"]} == cities
            assert result["plan"]["total"] <= 5000
            assert all(sum(item["walking_km"] for item in day["items"]) <= 2 for day in result["plan"]["days"])
            audit = client.get("/api/runs/" + result["run_id"]).json()
            assert audit["request"]["intent"]["budget"] == 5000
            assert any(row["status"] == "supplemented" and row["agent_id"] == "japan-rail" for row in audit["events"])
        assert len(client.get("/api/runs").json()) == 2


@pytest.mark.parametrize("disabled,gap", [
    (("japan-rail", "road-estimate"), "transfer"), (("local-mobility",), "mobility"),
])
def test_missing_capability(tmp_path, disabled, gap):
    with TestClient(create_app(tmp_path, disabled=disabled)) as client:
        result = request(client, SAMPLE)
        assert result["status"] == "partial"
        assert gap in result["gaps"]


def test_registered_expert_fallback_timeout(tmp_path):
    async def timeout(payload):
        await asyncio.sleep(1)
        return Cities(cities=[])

    async def broken(payload):
        raise RuntimeError("secret never logged")

    async def replacement(payload):
        return Cities(cities=[])

    async def run():
        registry = Registry()
        registry.register(Expert("primary", "Primary", "1.0.0", frozenset({"geocode"}), Cities, timeout, timeout=0.01))
        registry.register(Expert("secondary", "Secondary", "1.0.0", frozenset({"geocode"}), Cities, broken, cost_estimate=1))
        audit = Audit(tmp_path)
        gateway = Gateway(registry, {"geocode": Cities}, audit)
        task = Task(id="city", capability="geocode")
        payload = TaskInput(intent=Intent(), results={})
        assert await gateway.dispatch(task, payload) is None
        registry.register(Expert("primary", "Primary", "2.0.0", frozenset({"geocode"}), Cities,
                                 replacement, standby=True))
        assert len(registry.query("geocode")) == 3
        registry.experts.pop(("secondary", "1.0.0"))
        assert await gateway.dispatch(task, payload) == {"cities": [], "unresolved": []}
        assert {row.get("error") for row in audit.events} >= {"TimeoutError", "RuntimeError"}
        assert "secret never logged" not in json.dumps(audit.events)
        assert any(row["agent_version"] == "2.0.0" and row["status"] == "supplemented" for row in audit.events)

    asyncio.run(run())


def test_unknown_city_provider_and_cache(tmp_path):
    def fixture(req):
        if str(req.url).startswith(GEOCODE):
            assert req.url.params["name"] == "Lisbon"
            return httpx.Response(200, json={"results": [{"name": "Lisbon", "country_code": "PT",
                "feature_code": "PPLC", "latitude": 38.72, "longitude": -9.14}]})
        return httpx.Response(200, json={"elements": [{"lat": 38.72, "lon": -9.14,
            "tags": {"name": "Fixture Museum"}}]})

    provider = Providers(tmp_path / "cache", online=True, transport=httpx.MockTransport(fixture))
    provider.overpass = "https://example.test/interpreter"
    with TestClient(create_app(tmp_path / "runs", provider)) as client:
        result = request(client, "我想去 Lisbon 玩一天，预算 500 元，不用住宿")
        assert result["status"] == "partial"
        audit = client.get("/api/runs/" + result["run_id"]).json()
        city = audit["evidence"]["city"]["snapshot"]["cities"][0]
        assert city["country"] == "PT" and city["evidence"]["retrieved_at"]
        place = audit["evidence"]["places"]["snapshot"]["attractions"][0]
        assert place["name"] == "Fixture Museum" and place["hours"] is None
    provider.online = False
    assert asyncio.run(provider.geocode("Lisbon")).name == "Lisbon"


def test_budget_replan_opening_and_booking(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        result = request(client, "东京 2026-11-16 玩一天，预算 1 元，不用住宿，喜欢博物馆")
        assert result["status"] == "partial"
        assert any(issue["code"] == "over_budget" for issue in result["conflicts"])
        audit = client.get("/api/runs/" + result["run_id"]).json()
        assert len([row for row in audit["events"] if row["event"] == "replan"]) == 1
        booking = {"city": "Tokyo", "name": "已购票活动", "day": 1, "start": 650, "duration": 55,
                   "cost": 200, "lat": 35.6812, "lon": 139.7671}
        fixed = request(client, "东京 1 天，预算 1000 元，不用住宿", reservations=[booking])
        assert any(item["kind"] == "confirmed" and item["start"] == 650
                   for item in fixed["plan"]["days"][0]["items"])


def test_validation_and_stream(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/plan", json={"text": "", "extra": True}).status_code == 422
        assert client.get("/api/runs/invalid").status_code == 404
        response = client.post("/api/plan/stream", json={"text": SAMPLE})
        assert "event: trace" in response.text and "event: result" in response.text
        assert "japan-rail" in response.text
        assert request(client, "我想旅行")["status"] == "clarification"
