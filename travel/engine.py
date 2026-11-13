import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable

from pydantic import BaseModel

from .models import Intent, Task, TaskInput

ALIASES = {"延吉": "Yanji", "东京": "Tokyo", "大阪": "Osaka", "京都": "Kyoto",
           "莫斯科": "Moscow", "纽约": "New York"}
SUPERVISOR_PROMPT = (
    "Extract a travel Intent from the request. For a follow-up preserve every prior field "
    "unless the user changes it. Preserve destination order. Never invent booking details, "
    "dates, prices, or eligibility. Put unverified special requirements in constraints."
)


def parse_intent(text: str, previous: Intent | None = None) -> Intent:
    values = previous.model_dump() if previous else {}
    names = "|".join(ALIASES) + "|" + "|".join(ALIASES.values())
    found = [ALIASES.get(m[0], m[0].title()) for m in re.finditer(names, text, re.I)]
    match = re.search(r"(?:加入|添加|去|visit\s+|trip to\s+|travel to\s+|add\s+)([^，。！？,;\n]+)", text, re.I)
    if match:
        phrase = re.split(r"玩|旅行|旅游|\s+for\s+|\s+with\s+|\s+on\s+|\s+to the", match[1])[0]
        if re.fullmatch(r"[\w\s、和·'-]{2,65}", phrase) and not re.search(r"预算|budget|\d", phrase, re.I):
            found = [ALIASES.get(part, part.title()) for piece in re.split(r"、|和|\s+and\s+", phrase)
                     if (part := piece.strip())]
    if found:
        prior = values.get("destinations", []) if re.search(r"加入|添加|加上|\badd\b", text, re.I) else []
        values["destinations"] = list(dict.fromkeys([*prior, *found]))
    numerals = dict(zip("一二三四五六七八九十两", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 2]))
    explicit = set()
    for field, suffix in [("days", r"天|日游|days?"), ("travelers", r"人|people|travelers?")]:
        match = re.search(r"(\d+|[一二三四五六七八九十两])\s*(?:" + suffix + ")", text, re.I)
        if match:
            values[field] = numerals.get(match[1], int(match[1]) if match[1].isdigit() else 1)
            explicit.add(field)
    match = re.search(r"(?:预算|budget)\s*[:：约 ]*([¥$€]?)\s*(\d[\d,]*)\s*(人民币|元|日元|美元|卢布|欧元|CNY|JPY|USD|RUB|EUR)?", text, re.I)
    if match:
        values["budget"] = int(match[2].replace(",", ""))
        currencies = {"人民币": "CNY", "元": "CNY", "日元": "JPY", "美元": "USD",
                      "卢布": "RUB", "欧元": "EUR", "¥": "CNY", "$": "USD", "€": "EUR"}
        token = match[3] or match[1]
        values["currency"] = currencies.get(token, token.upper() or values.get("currency", "CNY"))
    if match := re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
        values["start_date"] = date.fromisoformat(match[0])
        explicit.add("start_date")
    interests = [tag for tag, pattern in {"动漫": "动漫|anime", "历史": "历史|博物馆|museum|history",
                                        "美食": "美食|food", "户外": "户外|nature"}.items()
                 if re.search(pattern, text, re.I)]
    if interests:
        values["interests"] = list(dict.fromkeys([*values.get("interests", []), *interests]))
    if re.search(r"少走|不想走|low.walk|limited walking", text, re.I):
        values["walking_km"] = 2
    if match := re.search(r"(\d+(?:\.\d+)?)\s*(?:公里|km)", text, re.I):
        values["walking_km"] = float(match[1])
    if re.search(r"不(?:用|需要)住宿|no hotel", text, re.I):
        values["lodging"] = False
    if re.search(r"天气|weather", text, re.I):
        values["weather"] = True
    if re.search(r"打车|taxi", text, re.I):
        values["transport"] = "taxi"
    if re.search(r"只走路|walk only", text, re.I):
        values["transport"] = "walk"
    if re.search(r"已订|已买|必须|轮椅|签证|过敏|素食|booked|allerg|wheelchair", text, re.I):
        values["constraints"] = [*values.get("constraints", []), "请补充已确认安排或特殊限制的结构化详情。"]
    assumptions = ["预算覆盖当地行程和城际移动，抵达首城及离开末城的大交通另计。"]
    for field, message in [("days", "未指定天数，按 3 天。"), ("travelers", "未指定人数，按 1 人。"),
                           ("start_date", "未指定日期，仅安排相对日程。")]:
        if field not in explicit and (not values.get(field) or previous and message in previous.assumptions):
            assumptions.append(message)
    values["assumptions"] = assumptions
    return Intent.model_validate(values)


def task_graph(intent: Intent) -> list[Task]:
    tasks = [Task(id="city", capability="geocode")]
    research = {"places": "attractions", "food": "food", "mobility": "mobility",
                "budget": "budget"}
    if intent.lodging and intent.days > 1:
        research["hotel"] = "accommodation"
    if len(intent.destinations) > 1:
        research["transfer"] = "cross_city"
    if intent.weather:
        research["weather"] = "weather"
    tasks.extend(Task(id=key, capability=cap, depends_on=["city"]) for key, cap in research.items())
    tasks.extend([Task(id="plan", capability="schedule", depends_on=["city", *research]),
                  Task(id="review", capability="verify", depends_on=["plan", "city", *research])])
    return tasks


@dataclass(frozen=True)
class Expert:
    agent_id: str
    name: str
    version: str
    capabilities: frozenset[str]
    output_schema: type[BaseModel]
    execute: Callable[[TaskInput], Awaitable[BaseModel]]
    supported_regions: frozenset[str] = frozenset({"*"})
    input_schema: type[BaseModel] = TaskInput
    dependencies: tuple[str, ...] = ()
    timeout: float = 8
    cost_estimate: float = 0
    enabled: bool = True
    standby: bool = False
    tool_version: str = "1"
    allowed_modes: frozenset[str] = frozenset({"public", "taxi", "walk"})

    def manifest(self):
        return {"agent_id": self.agent_id, "name": self.name, "version": self.version,
                "capabilities": sorted(self.capabilities), "regions": sorted(self.supported_regions),
                "input_schema": self.input_schema.model_json_schema(),
                "output_schema": self.output_schema.model_json_schema(),
                "dependencies": self.dependencies, "timeout": self.timeout, "cost_estimate": self.cost_estimate,
                "enabled": self.enabled, "standby": self.standby, "prompt_hash": None,
                "allowed_modes": sorted(self.allowed_modes),
                "model_identifier": None, "model_configuration": {}, "tool_version": self.tool_version}


class Registry:
    def __init__(self):
        self.experts: dict[tuple[str, str], Expert] = {}
        self.history: dict[tuple[str, str], tuple[int, int]] = {}

    def register(self, expert: Expert):
        key = (expert.agent_id, expert.version)
        if key in self.experts or not re.fullmatch(r"\d+\.\d+\.\d+", expert.version):
            raise ValueError("Duplicate expert or invalid version")
        self.experts[key] = expert

    def query(self, capability: str):
        return [expert for expert in self.experts.values() if capability in expert.capabilities]


class Gateway:
    def __init__(self, registry, contracts, audit):
        self.registry, self.contracts, self.audit = registry, contracts, audit

    async def dispatch(self, task, payload):
        regions = {city["country"] for city in payload.results.get("city", {}).get("cities", [])}
        candidates, accepted = [], []
        for expert in self.registry.query(task.capability):
            key = (expert.agent_id, expert.version)
            success, total = self.registry.history.get(key, (0, 0))
            factors = {"capability": 1, "region": int("*" in expert.supported_regions or
                       bool(regions) and regions <= expert.supported_regions),
                       "available": int(expert.enabled), "cost": -expert.cost_estimate,
                       "reliability": (success + 1) / (total + 2),
                       "constraints": int(set(expert.dependencies) <= payload.results.keys()
                                          and payload.intent.transport in expert.allowed_modes),
                       "compatible": int(expert.input_schema is TaskInput and
                                         expert.output_schema is self.contracts[task.capability])}
            eligible = all(factors[key] for key in ("region", "available", "constraints", "compatible"))
            score = sum(factors.values())
            candidates.append({"agent_id": expert.agent_id, "version": expert.version,
                               "factors": factors, "eligible": eligible, "score": score,
                               "standby": expert.standby})
            if eligible:
                accepted.append((expert, score))
        accepted.sort(key=lambda pair: (not pair[0].standby, pair[1], tuple(map(int, pair[0].version.split("."))),
                                        pair[0].agent_id), reverse=True)
        self.audit.emit("routing", task.id, "evaluated", candidates=candidates, capability=task.capability)
        active = [pair for pair in accepted if not pair[0].standby]
        standby = [pair for pair in accepted if pair[0].standby]
        attempts = ([active[0], standby[0]] if active and standby else accepted[:2])
        for expert, _ in attempts:
            key = (expert.agent_id, expert.version)
            if expert.standby:
                self.audit.emit("capability_gap", task.id, "supplemented", expert,
                                reason="No active compatible expert completed the task.")
            self.audit.emit("expert", task.id, "running", expert)
            try:
                validated = expert.input_schema.model_validate(payload.model_dump())
                result = await asyncio.wait_for(expert.execute(validated), expert.timeout)
                result = expert.output_schema.model_validate(result.model_dump())
            except Exception as exc:
                success, total = self.registry.history.get(key, (0, 0))
                self.registry.history[key] = (success, total + 1)
                self.audit.emit("expert", task.id, "failed", expert, error=type(exc).__name__)
                continue
            success, total = self.registry.history.get(key, (0, 0))
            self.registry.history[key] = (success + 1, total + 1)
            output = result.model_dump(mode="json")
            self.audit.emit("expert", task.id, "completed", expert, result=output)
            return output
        self.audit.emit("capability_gap", task.id, "missing", capability=task.capability)
        return None

    async def run(self, tasks, intent):
        remaining = {task.id: task for task in tasks}
        finished, results = set(), {}
        while remaining:
            ready = [task for task in remaining.values() if set(task.depends_on) <= finished]
            if not ready:
                raise ValueError("Task DAG has a cycle or missing dependency")
            payloads = [TaskInput(intent=intent, results={key: results[key] for key in task.depends_on
                                                          if key in results}) for task in ready]
            outputs = await asyncio.gather(*(self.dispatch(task, payload) for task, payload in zip(ready, payloads)))
            for task, output in zip(ready, outputs):
                if output is not None:
                    results[task.id] = output
                finished.add(task.id)
                del remaining[task.id]
        return results
