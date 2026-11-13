import hashlib
import json
import os

import httpx
from pydantic import ValidationError

from . import __version__
from .audit import digest
from .engine import Gateway, SUPERVISOR_PROMPT, parse_intent, task_graph
from .experts import CONTRACTS
from .models import Intent, TaskInput


async def understand(request, previous, audit):
    model = os.getenv("TRAVEL_LLM_MODEL")
    url = os.getenv("TRAVEL_LLM_URL")
    key = os.getenv("TRAVEL_LLM_KEY")
    metadata = {"agent_id": "supervisor", "version": __version__,
                "prompt_hash": hashlib.sha256(SUPERVISOR_PROMPT.encode()).hexdigest(),
                "model_identifier": model if url and key else None,
                "model_configuration": {"temperature": 0} if model and url and key else {},
                "tool_version": "intent-parser/1"}
    if model and url and key:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"}, json={
                        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
                        "messages": [{"role": "system", "content": SUPERVISOR_PROMPT +
                                      " Schema: " + json.dumps(Intent.model_json_schema())},
                                     {"role": "user", "content": json.dumps({"request": request.text,
                                         "previous": previous.model_dump(mode="json") if previous else None},
                                         ensure_ascii=False)}]})
                response.raise_for_status()
                intent = Intent.model_validate_json(response.json()["choices"][0]["message"]["content"])
                intent.reservations = previous.reservations if previous else []
                audit.emit("intent", "supervisor", "model", agent_id="supervisor", agent_version=__version__)
                return intent, metadata
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            audit.emit("intent", "supervisor", "fallback", agent_id="supervisor",
                       agent_version=__version__, error=type(exc).__name__)
    intent = parse_intent(request.text, previous)
    return intent, metadata


async def execute(request, previous, registry, audit):
    audit.write("manifest", audit.manifest(registry, {"agent_id": "supervisor", "version": __version__}))
    try:
        intent, metadata = await understand(request, previous, audit)
    except (ValidationError, ValueError):
        result = {"run_id": audit.run_id, "status": "clarification",
                  "questions": ["请检查日期、预算、人数及天数。支持 1–14 天、1–30 人。"]}
        audit.emit("intent", "supervisor", "clarification", agent_id="supervisor", agent_version=__version__)
        audit.write("final_plan", result)
        return result
    if request.reservations is not None:
        intent.reservations = request.reservations
    audit.write("manifest", audit.manifest(registry, metadata))
    audit.write("request", {"intent": intent.model_dump(mode="json"), "previous_run": request.previous_run,
                            "retention": "structured planning inputs only"})
    if not intent.destinations or intent.budget is None:
        questions = []
        if not intent.destinations:
            questions.append("你想去哪个城市？")
        if intent.budget is None:
            questions.append("总预算和币种是多少？")
        result = {"run_id": audit.run_id, "status": "clarification", "questions": questions}
        audit.emit("intent", "supervisor", "clarification", agent_id="supervisor", agent_version=__version__)
        audit.write("final_plan", result)
        return result
    tasks = task_graph(intent)
    audit.write("task_graph", [task.model_dump() for task in tasks])
    audit.emit("task_graph", "supervisor", "completed", agent_id="supervisor", agent_version=__version__,
               tasks=[task.model_dump() for task in tasks])
    gateway = Gateway(registry, CONTRACTS, audit)
    results = await gateway.run(tasks, intent)
    review = results.get("review", {})
    if "plan" in results and any(issue["code"] == "over_budget" for issue in review.get("conflicts", [])):
        audit.emit("replan", "plan", "requested", reason="over_budget", attempt=1,
                   previous_total=results["plan"]["total"])
        alternative = await gateway.dispatch(tasks[-2], TaskInput(intent=intent, attempt=1,
            results={key: value for key, value in results.items() if key in tasks[-2].depends_on}))
        if alternative and alternative["total_cny"] <= results["plan"]["total_cny"]:
            revised = {**results, "plan": alternative}
            reviewed = await gateway.dispatch(tasks[-1], TaskInput(intent=intent, attempt=1,
                results={key: value for key, value in revised.items() if key in tasks[-1].depends_on}))
            old_codes = {issue["code"] for issue in review["conflicts"] if issue["hard"]}
            if reviewed and all(not issue["hard"] or issue["code"] in old_codes for issue in reviewed["conflicts"]):
                results.update(plan=alternative, review=reviewed)
                review = reviewed
    snapshots = {key: {"sha256": digest(value), "snapshot": value}
                 for key, value in results.items() if key not in {"plan", "review"}}
    audit.write("evidence", snapshots)
    gaps = [event["task_id"] for event in audit.events
            if event["event"] == "capability_gap" and event["status"] == "missing"]
    conflicts = review.get("conflicts", [])
    if "review" not in results:
        conflicts = [*conflicts, {"code": "unverified", "detail": "验证专家未完成，行程仍是草案。", "hard": True}]
    status = "partial" if gaps or any(issue["hard"] for issue in conflicts) else "success"
    result = {"run_id": audit.run_id, "status": status, "plan": results.get("plan"),
              "conflicts": conflicts, "gaps": gaps, "weather": results.get("weather", {}).get("conditions", []),
              "assumptions": intent.assumptions,
              "city_anchors": results.get("city", {}).get("cities", []),
              "attraction_catalog": results.get("places", {}).get("attractions", []),
              "unscheduled_candidates": [place for place in results.get("places", {}).get("attractions", [])
                                         if place["hours"] is None or (place["cost_cny"] is None and
                                             (place["price_jpy"] is None or place["price_jpy"] > 0 and
                                              not results.get("budget", {}).get("jpy_per_cny")))]}
    audit.emit("run", None, status)
    audit.write("final_plan", result)
    return result
