import os
from datetime import timedelta
from math import ceil, floor

from .geo import haversine
from .engine import Expert, Registry
from .japan import JAPAN_CITIES, RAIL as OFFICIAL_RAIL, SMART_EX, official
from .models import Attractions, Budget, Cities, Food, Lodging, Mobility, Plan, Review, Transfers, TransportOption, Weather
from .planner import optimize, verify
from .providers import DEMO, ESTIMATE

CONTRACTS = {"geocode": Cities, "attractions": Attractions, "food": Food, "accommodation": Lodging,
             "mobility": Mobility, "budget": Budget, "cross_city": Transfers, "weather": Weather,
             "schedule": Plan, "verify": Review}
RAIL = {frozenset(("Tokyo", "Osaka")): (205, 700),
        frozenset(("Tokyo", "Kyoto")): (185, 670),
        frozenset(("Osaka", "Kyoto")): (75, 55)}


def build_registry(providers, disabled=()):
    registry = Registry()
    disabled = set(disabled) | set(os.getenv("TRAVEL_DISABLED", "").split(","))

    def register(key, capability, execute, version="1.0.0", **settings):
        registry.register(Expert(key, key.replace("-", " ").title(), version, frozenset({capability}),
                                 CONTRACTS[capability], execute, enabled=key not in disabled, **settings))

    def cities(payload):
        return Cities.model_validate(payload.results["city"]).cities

    async def geocode(payload):
        found, unresolved = [], []
        for name in payload.intent.destinations:
            city = await providers.geocode(name)
            if city:
                if all((row.name, row.country) != (city.name, city.country) for row in found):
                    found.append(city)
            else:
                unresolved.append(name)
        return Cities(cities=found, unresolved=unresolved)

    async def places(payload):
        return Attractions(attractions=[place for city in cities(payload) for place in await providers.places(city)])

    async def food(payload):
        return Food(restaurants=[place for city in cities(payload) for place in providers.food(city)])

    async def lodging(payload):
        return Lodging(hotels=[place for city in cities(payload) for place in providers.lodging(city)])

    async def budget(payload):
        intent = payload.intent
        exchange = await providers.exchange(intent.currency)
        if not exchange:
            raise ValueError("Exchange unavailable")
        rate, evidence = exchange
        yen = (exchange if intent.currency == "JPY" else await providers.exchange("JPY")) if (
            not providers.demo and any(city.name in JAPAN_CITIES for city in cities(payload))) else None
        return Budget(cny_limit=floor(intent.budget / rate) if intent.budget else 100000000,
                      units_per_cny=rate, evidence=evidence,
                      jpy_per_cny=yen[0] if yen else None, jpy_evidence=yen[1] if yen else None)

    async def mobility(payload):
        return Mobility(mode=payload.intent.transport, walking_km=payload.intent.walking_km)

    async def weather(payload):
        if not payload.intent.start_date:
            return Weather(conditions=[])
        start = payload.intent.start_date
        end = start + timedelta(days=payload.intent.days - 1)
        return Weather(conditions=[value for city in cities(payload) for value in await providers.weather(city, start, end)])

    async def rail(payload):
        ordered = cities(payload)
        transfers = []
        for origin, destination in zip(ordered, ordered[1:]):
            key = frozenset((origin.name, destination.name))
            if not providers.demo:
                entry = OFFICIAL_RAIL.get(key)
                if entry and payload.intent.transport == "public":
                    yen, minutes, stations = entry
                    rate = await providers.exchange("JPY")
                    if rate:
                        transfers.append(TransportOption(origin=origin.name, destination=destination.name,
                            mode=f"东海道新干线 Nozomi · {stations} · 普通指定席平季参考价，未核对班次、余票或季节差价；时间含转场预留",
                            minutes=minutes, cost_cny=ceil(yen / rate[0]), price_jpy=yen, walking_km=0.4,
                            evidence=official(SMART_EX, "SmartEX 官方票价表")))
                continue
            entry = RAIL.get(key)
            if entry and payload.intent.transport == "public":
                minutes, cost = entry
                transfers.append(TransportOption(origin=origin.name, destination=destination.name,
                    mode="铁路演示估算，含进出站，班次未验证", minutes=minutes,
                    cost_cny=cost, walking_km=0.4, evidence=DEMO))
        if len(transfers) != len(ordered) - 1:
            raise ValueError("Rail or exchange coverage incomplete")
        return Transfers(transfers=transfers)

    async def road_estimates(payload):
        ordered = cities(payload)
        transfers = []
        for origin, destination in zip(ordered, ordered[1:]):
            distance = haversine(origin.lat, origin.lon, destination.lat, destination.lon) * 1.35
            if origin.country != destination.country or distance > 500 or payload.intent.transport != "taxi":
                continue
            transfers.append(TransportOption(origin=origin.name, destination=destination.name,
                mode="专车成本与时长估算，路线未验证", minutes=max(30, round(distance / 55 * 60) + 30),
                cost_cny=round(60 + distance * 3), walking_km=0.2, evidence=ESTIMATE))
        if len(transfers) != len(ordered) - 1:
            raise ValueError("Road estimate coverage or requested mode unavailable")
        return Transfers(transfers=transfers)

    async def schedule(payload):
        return optimize(payload, payload.attempt)

    async def review(payload):
        return verify(payload)

    register("global-geocoder", "geocode", geocode, timeout=35)
    for key, cap, fn in [("places-provider", "attractions", places), ("food-catalog", "food", food),
                         ("lodging-catalog", "accommodation", lodging), ("currency-provider", "budget", budget),
                         ("local-mobility", "mobility", mobility), ("forecast-provider", "weather", weather)]:
        register(key, cap, fn, dependencies=("city",), timeout=35, version="1.1.0")
    register("japan-rail", "cross_city", rail, supported_regions=frozenset({"JP"}),
             dependencies=("city",), standby=True, allowed_modes=frozenset({"public"}), version="1.1.0")
    register("road-estimate", "cross_city", road_estimates, dependencies=("city",), standby=True,
             cost_estimate=1, allowed_modes=frozenset({"taxi"}))
    register("deterministic-scheduler", "schedule", schedule, dependencies=("city", "mobility"), version="1.1.0")
    register("constraint-validator", "verify", review, version="1.1.0")
    return registry
