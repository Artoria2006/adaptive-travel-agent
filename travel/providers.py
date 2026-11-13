import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from .japan import JAPAN_CITIES, attractions as japan_attractions, official

from .models import Accommodation, Attraction, City, Evidence, Restaurant, WeatherCondition

DEMO = Evidence(source="课程演示数据 v1", provider="demo", confidence="demo")
ESTIMATE = Evidence(source="地理距离与固定参数估算", provider="distance-model", confidence="estimate")
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
FX = "https://api.frankfurter.dev/v2/rate"

CATALOG = {
    "Tokyo": ("JP", 35.6812, 139.7671, 350, 55, [
        ("秋叶原动漫街区", 35.6984, 139.7731, 0, "动漫"),
        ("东京动漫中心", 35.6623, 139.6981, 40, "动漫"),
        ("东京国立博物馆", 35.7188, 139.7765, 50, "历史"),
        ("浅草寺", 35.7148, 139.7967, 0, "历史"),
    ]),
    "Osaka": ("JP", 34.7025, 135.4959, 280, 45, [
        ("日本桥电电城", 34.6593, 135.5055, 0, "动漫"),
        ("大阪城天守阁", 34.6873, 135.5262, 60, "历史"),
        ("道顿堀", 34.6687, 135.5013, 0, "美食"),
        ("大阪历史博物馆", 34.6828, 135.5205, 35, "历史"),
    ]),
    "Kyoto": ("JP", 34.9858, 135.7588, 300, 45, [
        ("京都国际漫画博物馆", 35.0117, 135.7593, 60, "动漫"),
        ("京都国立博物馆", 34.9901, 135.7721, 50, "历史"),
        ("锦市场", 35.0050, 135.7649, 0, "美食"),
    ]),
    "Moscow": ("RU", 55.7558, 37.6173, 320, 60, [
        ("红场", 55.7540, 37.6204, 0, "历史"),
        ("特列季亚科夫画廊", 55.7415, 37.6209, 85, "历史"),
        ("高尔基公园", 55.7294, 37.6035, 0, "户外"),
    ]),
    "New York": ("US", 40.7536, -73.9832, 720, 100, [
        ("纽约公共图书馆", 40.7532, -73.9822, 0, "历史"),
        ("现代艺术博物馆", 40.7614, -73.9776, 215, "历史"),
        ("布莱恩特公园", 40.7536, -73.9832, 0, "户外"),
    ]),
}


def daily(start, end):
    return {day: [(start, end)] for day in range(7)}


class Providers:
    """Cache provider payloads with their retrieval time and bounded freshness."""

    def __init__(self, cache: Path, online=False, transport=None, demo=False):
        self.cache = cache
        self.online = online
        self.transport = transport
        self.demo = demo
        self.lock = asyncio.Lock()
        self.overpass = os.getenv("TRAVEL_OVERPASS_URL", "")

    async def fetch(self, url, params, provider, ttl=24, post=False):
        key = hashlib.sha256(json.dumps([url, params, post], sort_keys=True).encode()).hexdigest()
        path = self.cache / (key + ".json")
        async with self.lock:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
            now = datetime.now(UTC)
            if cached and datetime.fromisoformat(cached["valid_until"]) > now:
                return self.unpack(cached, url, provider)
            if self.online:
                try:
                    async with httpx.AsyncClient(timeout=5, transport=self.transport,
                                                headers={"User-Agent": "AdaptiveTravelAgent/0.1"}) as client:
                        await asyncio.sleep(1.05 if "routing.openstreetmap.de" in url else 0.25)
                        response = await (client.post(url, data=params) if post else client.get(url, params=params))
                        response.raise_for_status()
                        record = {"data": response.json(), "retrieved_at": now.isoformat(),
                                  "valid_until": (now + timedelta(hours=ttl)).isoformat()}
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix("." + uuid4().hex)
                    temporary.write_text(json.dumps(record), encoding="utf-8")
                    temporary.replace(path)
                    return self.unpack(record, url, provider)
                except (httpx.HTTPError, ValueError):
                    pass
            if cached:
                return self.unpack(cached, url, provider, stale=True)
        return None

    @staticmethod
    def unpack(record, url, provider, stale=False):
        return record["data"], Evidence(source=url, provider=provider, confidence="provider",
                                         retrieved_at=record["retrieved_at"], valid_until=record["valid_until"],
                                         stale=stale)

    async def geocode(self, name):
        if name in CATALOG:
            country, lat, lon, *_ = CATALOG[name]
            evidence = DEMO
            if name in JAPAN_CITIES and not self.demo:
                evidence = official("https://www.openstreetmap.org/", "城市中心参考坐标")
            return City(name=name, country=country, lat=lat, lon=lon, evidence=evidence)
        fetched = await self.fetch(GEOCODE, {"name": name, "count": 5, "language": "en"}, "Open-Meteo / GeoNames", 168)
        if fetched:
            data, evidence = fetched
            results = [row for row in data.get("results", []) if row.get("country_code") and
                       row.get("feature_code", "").startswith("PPL")]
            if results:
                row = max(results, key=lambda item: (item["name"].casefold() == name.casefold(),
                                                     item.get("population", 0)))
                return City(name=row["name"], country=row["country_code"], lat=row["latitude"],
                            lon=row["longitude"], evidence=evidence)
        return None

    async def places(self, city):
        offline = (japan_attractions(city.name) if city.name in JAPAN_CITIES and not self.demo
                   else self.demo_places(city) if self.demo else [])
        if self.overpass:
            query = (f'[out:json][timeout:5];nwr["tourism"~"^(museum|attraction|gallery)$"]["name"]'
                     f'(around:4500,{city.lat},{city.lon});out center 25;')
            fetched = await self.fetch(self.overpass, {"data": query}, "OpenStreetMap contributors / Overpass", 72, True)
            if fetched:
                data, evidence = fetched
                discovered = []
                for row in data.get("elements", []):
                    tags = row.get("tags", {})
                    position = row.get("center", row)
                    name = tags.get("name:zh") or tags.get("name")
                    if not name or "lat" not in position or "lon" not in position:
                        continue
                    source = evidence.source
                    if row.get("type") in {"node", "way", "relation"} and isinstance(row.get("id"), int):
                        source = f"https://www.openstreetmap.org/{row['type']}/{row['id']}"
                    discovered.append(Attraction(name=name, city=city.name, lat=position["lat"],
                        lon=position["lon"], evidence=evidence.model_copy(update={"source": source}), duration=60))
                return [*discovered, *offline]
        return offline

    def demo_places(self, city):
        if city.name in CATALOG:
            return [Attraction(name=name, city=city.name, lat=lat, lon=lon, evidence=DEMO, duration=65,
                               hours=daily(600, 1020), cost_cny=cost, tags=[tag])
                    for name, lat, lon, cost, tag in CATALOG[city.name][5]]
        return []

    def food(self, city):
        if not self.demo or city.name not in CATALOG:
            return []
        return [Restaurant(name=f"{city.name} 市区餐饮预留", city=city.name, lat=city.lat, lon=city.lon,
                           meal_cost_cny=CATALOG[city.name][4], evidence=DEMO)]

    def lodging(self, city):
        if not self.demo or city.name not in CATALOG:
            return []
        return [Accommodation(name=f"{city.name} 中心区住宿预留", city=city.name, lat=city.lat,
                              lon=city.lon, nightly_cny=CATALOG[city.name][3], evidence=DEMO)]

    async def exchange(self, currency):
        if currency == "CNY":
            return 1, Evidence(source="同币种，无需换汇", provider="identity", confidence="provider")
        fetched = await self.fetch(f"{FX}/CNY/{currency}", {}, "Frankfurter reference rates")
        if fetched:
            data, evidence = fetched
            return data["rate"], evidence
        return None

    async def route(self, origin, destination, mode):
        profile = {"walk": "foot", "taxi": "car"}[mode]
        endpoint = os.getenv(f"TRAVEL_OSRM_{profile.upper()}",
                             f"https://routing.openstreetmap.de/routed-{profile}/route/v1/driving")
        coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in (origin, destination))
        fetched = await self.fetch(f"{endpoint.rstrip('/')}/{coordinates}",
                                   {"overview": "full", "geometries": "geojson"}, "OSRM / OpenStreetMap", 168)
        if fetched:
            data, evidence = fetched
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                points = route.get("geometry", {}).get("coordinates", [])
                if len(points) >= 2:
                    return {"mode": mode, "coordinates": [[lat, lon] for lon, lat in points],
                            "distance_m": round(route["distance"]), "duration_s": round(route["duration"]),
                            "evidence": evidence.model_copy(update={"source": endpoint}).model_dump(mode="json")}
        return None

    async def weather(self, city, start, end):
        fetched = await self.fetch(FORECAST, {"latitude": city.lat, "longitude": city.lon,
            "daily": "temperature_2m_min,temperature_2m_max", "timezone": "auto",
            "start_date": str(start), "end_date": str(end)}, "Open-Meteo", 3)
        if not fetched:
            return []
        data, evidence = fetched
        daily_values = data.get("daily", {})
        return [WeatherCondition(city=city.name, date=day, low=low, high=high, evidence=evidence)
                for day, low, high in zip(daily_values.get("time", []), daily_values.get("temperature_2m_min", []),
                                          daily_values.get("temperature_2m_max", []))
                if low is not None and high is not None]
