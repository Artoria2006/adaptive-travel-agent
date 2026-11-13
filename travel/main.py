import asyncio
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from typing import Literal
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audit import Audit, ROOT, json_read, replay
from .experts import build_registry
from .models import Intent, Request
from .providers import Providers
from .service import execute


def create_app(root=None, providers=None, disabled=()):
    root = Path(root or os.getenv("TRAVEL_RUNS", ROOT / "runs"))
    providers = providers or Providers(Path(os.getenv("TRAVEL_CACHE", ROOT / ".cache")),
                                      online=os.getenv("TRAVEL_ONLINE", "1") == "1",
                                      demo=os.getenv("TRAVEL_DEMO") == "1")
    registry = build_registry(providers, disabled)
    app = FastAPI(title="Adaptive Travel Agent", version=__version__)

    def locate(run_id):
        path = root / run_id
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not path.is_dir():
            raise HTTPException(404, "Unknown run")
        return path

    def initialize(request):
        previous = None
        if request.previous_run:
            path = locate(request.previous_run)
            prior = json_read(path / "request.json").get("intent")
            if prior:
                previous = Intent.model_validate(prior)
            else:
                raise HTTPException(409, "Previous run has no usable intent")
        return previous, Audit(root, request.previous_run)

    async def perform(request, previous, audit):
        try:
            return await execute(request, previous, registry, audit)
        except Exception as exc:
            audit.emit("run", None, "failed", error=type(exc).__name__)
            result = {"status": "failed", "run_id": audit.run_id}
            audit.write("final_plan", result)
            return result

    @app.post("/api/plan")
    async def plan(request: Request):
        previous, audit = initialize(request)
        return await perform(request, previous, audit)

    @app.post("/api/plan/stream")
    async def stream(request: Request):
        previous, audit = initialize(request)
        task = asyncio.create_task(perform(request, previous, audit))

        async def events():
            position = 0
            yield "event: start\ndata: " + json.dumps({"run_id": audit.run_id}) + "\n\n"
            while not task.done() or position < len(audit.events):
                for event in audit.events[position:]:
                    yield "event: trace\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    position += 1
                await asyncio.sleep(0.08)
            result = task.result()
            yield "event: result\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs")
    def history():
        if not root.exists():
            return []
        runs = sorted(root.glob("*/final_plan.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return [{"run_id": path.parent.name, "status": json_read(path)["status"],
                 "destinations": json_read(path.parent / "request.json").get("intent", {}).get("destinations", [])}
                for path in runs[:50]]

    @app.get("/api/runs/{run_id}")
    def audit_replay(run_id: str):
        return replay(locate(run_id))

    @app.get("/api/runs/{run_id}/route")
    async def route(run_id: str, day: int, start: int, end: int, mode: Literal["walk", "taxi"]):
        path = locate(run_id) / "final_plan.json"
        if not path.exists():
            raise HTTPException(409, "Run in progress")
        result = json_read(path)
        days = (result.get("plan") or {}).get("days", [])
        entry = next((item for item in days if item["number"] == day), None)
        if not entry or start < -1 or end != start + 1 or end >= len(entry["items"]):
            raise HTTPException(404, "Unknown local leg")
        after = entry["items"][end]
        if start == -1:
            evidence = json_read(path.parent / "evidence.json")
            cities = evidence.get("city", {}).get("snapshot", {}).get("cities", [])
            city = next((row for row in cities if row["name"] == entry["city"]), None)
            if not city:
                raise HTTPException(404, "Unknown city anchor")
            before = {**city, "kind": "anchor"}
        else:
            before = entry["items"][start]
        if any(item["kind"] in {"transfer", "confirmed"} or item["lat"] is None or item["lon"] is None
               for item in (before, after)):
            return {"status": "unavailable", "reason": "铁路和用户确认地点的路线不发送给道路服务。"}
        value = await providers.route((before["lat"], before["lon"]), (after["lat"], after["lon"]), mode)
        return {"status": "available", "route": value} if value else {
            "status": "unavailable", "reason": "路线服务未返回可用路径。"}

    @app.get("/api/experts")
    def experts():
        return [expert.manifest() for expert in registry.experts.values()]

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app


app = create_app()
