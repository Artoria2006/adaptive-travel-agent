from datetime import timedelta
from math import ceil
from typing import Protocol

from .geo import haversine
from .models import (Attractions, Budget, Cities, Day, Evidence, Food, Item, Lodging,
                     Mobility, Plan, Review, Transfers, TravelConstraint)

OPEN, CLOSE = 540, 1200
MODES = {"public": "公共交通（估算）", "taxi": "打车（估算）", "walk": "步行（估算）"}


class RoutingProvider(Protocol):
    def estimate(self, origin: tuple[float, float], target: tuple[float, float],
                 mode: str) -> tuple[int, float, int]: ...


class DistanceRouting:
    def estimate(self, origin, target, mode):
        distance = haversine(origin[0], origin[1], target[0], target[1]) * 1.3
        if distance < 0.05:
            return 0, 0, 0
        if mode == "walk":
            return ceil(distance / 4 * 60), distance, 0
        if mode == "taxi":
            return ceil(distance / 22 * 60) + 5, 0.1, ceil(12 + 3 * distance)
        return ceil(distance / 18 * 60) + 8, min(0.35, distance), 12


def date_for(intent, day):
    return intent.start_date + timedelta(days=day - 1) if intent.start_date else None


def opening(place, on, earliest, deadline):
    if place.hours is None:
        return None
    if place.valid_dates and (on is None or not any(start <= on <= end for start, end in place.valid_dates)):
        return None
    if on and (on.month not in place.months or on.strftime("%m-%d") in place.closed_dates):
        return None
    if on:
        windows = place.hours.get(on.weekday(), [])
    else:
        windows = [window for window in place.hours.get(0, [])
                   if all(window in place.hours.get(day, []) for day in range(1, 7))]
    for start, end in windows:
        beginning = max(start, earliest)
        if beginning + place.duration <= min(end, deadline) and beginning <= end - place.last_admission:
            return beginning
    return None


def constraint(code, detail, hard=True):
    return TravelConstraint(code=code, detail=detail, hard=hard)


def optimize(payload, attempt=0, routing: RoutingProvider = DistanceRouting()):
    local_leg = routing.estimate
    intent, data = payload.intent, payload.results
    cities = Cities.model_validate(data.get("city", {"cities": []}))
    budget = Budget.model_validate(data["budget"]) if "budget" in data else None
    mobility = Mobility.model_validate(data["mobility"])
    plan = Plan(attempt=attempt, currency=intent.currency)
    if cities.unresolved or not cities.cities:
        plan.conflicts.append(constraint("city", "缺少可解析的目的地：" + "、".join(cities.unresolved)))
    if len(cities.cities) > intent.days:
        plan.conflicts.append(constraint("days", "每个城市至少需要一天，请增加天数或减少目的地。"))
    if not budget:
        plan.conflicts.append(constraint("budget_data", "缺少可用预算或汇率，请补充预算或改用人民币。"))
    if plan.conflicts:
        return plan

    places = Attractions.model_validate(data.get("places", {"attractions": []})).attractions
    food = {row.city: row for row in Food.model_validate(data.get("food", {"restaurants": []})).restaurants}
    hotels = {row.city: row for row in Lodging.model_validate(data.get("hotel", {"hotels": []})).hotels}
    transfers = {(row.origin, row.destination): row for row in Transfers.model_validate(
        data.get("transfer", {"transfers": []})).transfers}
    names = [city.name for city in cities.cities]
    if any(r.city not in names or r.day > intent.days for r in intent.reservations):
        plan.conflicts.append(constraint("reservation", "已确认安排的城市或日期不在本次行程内。"))
        return plan
    allocation = [intent.days // len(names) + (i < intent.days % len(names)) for i in range(len(names))]
    day_cities = [city for city, count in zip(cities.cities, allocation) for _ in range(count)]
    used = set()
    for number, city in enumerate(day_cities, 1):
        on = date_for(intent, number)
        day = Day(number=number, date=on, city=city.name, items=[])
        plan.days.append(day)
        center = (city.lat, city.lon)
        position, clock, walking = center, OPEN, 0

        def append(name, start, duration, cost, kind, reason, evidence, lat=None, lon=None, km=0, minutes=0,
                   price_jpy=None):
            nonlocal clock, walking, position
            day.items.append(Item(name=name, city=city.name, start=start, end=start + duration,
                                  cost_cny=cost, kind=kind, reason=reason, evidence=evidence, lat=lat, lon=lon,
                                  walking_km=round(km, 3), travel_minutes=minutes, price_jpy=price_jpy,
                                  transfer_mode=mobility.mode if minutes else None))
            clock = start + duration
            walking += round(km, 3)
            if lat is not None:
                position = (lat, lon)

        if number > 1 and day_cities[number - 2].name != city.name:
            origin = day_cities[number - 2].name
            transfer = transfers.get((origin, city.name))
            if not transfer:
                plan.conflicts.append(constraint("transfer", f"缺少 {origin} → {city.name} 可用交通。"))
                continue
            append(f"{origin} → {city.name}", OPEN, transfer.minutes, transfer.cost_cny * intent.travelers,
                   "transfer", transfer.mode, transfer.evidence, *center, km=transfer.walking_km,
                   price_jpy=transfer.price_jpy)

        fixed = sorted((r for r in intent.reservations if r.day == number), key=lambda r: r.start)
        for reservation in fixed:
            minutes, km, fare = local_leg(position, (reservation.lat, reservation.lon), mobility.mode)
            if reservation.city != city.name or clock + minutes > reservation.start:
                plan.conflicts.append(constraint("reservation", f"第 {number} 天无法赶上 {reservation.name}。"))
                continue
            append(reservation.name, reservation.start, reservation.duration,
                   reservation.cost + fare * intent.travelers, "confirmed", "用户已确认的安排",
                   Evidence(source="结构化用户输入", provider="user", confidence="user"),
                   reservation.lat, reservation.lon, km, minutes)

        restaurant = food.get(city.name)
        hotel = hotels.get(city.name) if intent.lodging and number < intent.days else None
        if not restaurant and not any(issue.code == "food_data" and city.name in issue.detail for issue in plan.conflicts):
            plan.conflicts.append(constraint("food_data", f"{city.name} 缺少餐饮费用数据。"))
        if (intent.lodging and number < intent.days and not hotel
                and not any(issue.code == "hotel_data" and city.name in issue.detail for issue in plan.conflicts)):
            plan.conflicts.append(constraint("hotel_data", f"{city.name} 缺少住宿费用数据。"))
        night_cost = hotel.nightly_cny * ceil(intent.travelers / 2) if hotel else 0
        meal_cost = restaurant.meal_cost_cny * intent.travelers if restaurant else 0

        def visit(deadline):
            candidates = []
            for place in places:
                if place.city != city.name or (place.city, place.name) in used:
                    continue
                ticket = place.cost_cny
                if place.price_jpy == 0:
                    ticket = 0
                elif place.price_jpy is not None and budget.jpy_per_cny:
                    ticket = ceil(place.price_jpy / budget.jpy_per_cny)
                if ticket is None:
                    continue
                minutes, km, fare = local_leg(position, (place.lat, place.lon), mobility.mode)
                return_minutes, return_km, return_fare = local_leg((place.lat, place.lon), center, mobility.mode)
                start = opening(place, on, clock + minutes, deadline - return_minutes)
                if start is None or walking + km + place.walking_km + return_km > mobility.walking_km:
                    continue
                cost = (ticket + fare) * intent.travelers
                if attempt:
                    spent = sum(item.cost_cny for entry in plan.days for item in entry.items)
                    outstanding = sum(2 * food[entry.name].meal_cost_cny * intent.travelers
                                      for entry in day_cities[number:] if entry.name in food)
                    outstanding += sum(hotels[entry.name].nightly_cny * ceil(intent.travelers / 2)
                                       for entry in day_cities[number:-1] if intent.lodging and entry.name in hotels)
                    outstanding += sum(transfers[(before.name, after.name)].cost_cny * intent.travelers
                                       for before, after in zip(day_cities[number - 1:], day_cities[number:])
                                       if (before.name, after.name) in transfers)
                    meals_due = 2 - sum(item.kind == "meal" for item in day.items)
                    if spent + outstanding + night_cost + meals_due * meal_cost + cost + return_fare * intent.travelers > budget.cny_limit:
                        continue
                match = len(set(intent.interests) & set(place.tags))
                rank = (match * 90 - minutes - (cost * 3 if attempt else cost / 5), -start, place.name)
                candidates.append((rank, place, start, minutes, km, cost))
            if candidates:
                _, place, start, minutes, km, cost = max(candidates, key=lambda row: row[0])
                used.add((place.city, place.name))
                reason = "匹配旅行兴趣" if set(intent.interests) & set(place.tags) else "符合时段和步行限制"
                reason += f"，转场方式：{MODES[mobility.mode]}"
                if place.notes:
                    reason += "。" + place.notes
                append(place.name, start, place.duration, cost, "visit", reason, place.evidence, place.lat,
                       place.lon, km + place.walking_km, minutes, price_jpy=place.price_jpy)

        def meal(name, earliest):
            if restaurant:
                minutes, km, fare = local_leg(position, center, mobility.mode)
                start = max(earliest, clock + minutes)
                if start + 35 <= CLOSE and walking + km <= mobility.walking_km:
                    append(name, start, 35, meal_cost + fare * intent.travelers, "meal",
                           "中心区餐饮预算，含转场估算", restaurant.evidence, *center, km, minutes)
                else:
                    plan.conflicts.append(constraint("meal_time", f"第 {number} 天无法安排{name}。"))

        if clock < 660:
            visit(720)
        meal("午餐", 720)
        visit(1080)
        meal("晚餐", 1080)
        if hotel:
            minutes, km, fare = local_leg(position, center, mobility.mode)
            append(hotel.name, clock + minutes, 10, night_cost + fare * intent.travelers, "lodging",
                   "按中心区住宿和双人房估算", hotel.evidence, *center, km, minutes)
        if not any(item.kind in {"visit", "confirmed"} for item in day.items):
            plan.conflicts.append(constraint("visit_data", f"第 {number} 天暂无满足条件的景点，可作为自由活动。", False))
    plan.total_cny = sum(item.cost_cny for day in plan.days for item in day.items)
    plan.total = round(plan.total_cny * budget.units_per_cny, 2)
    if plan.days and not any(item.kind in {"visit", "confirmed"} for day in plan.days for item in day.items):
        plan.conflicts.append(constraint("no_activities", "当前数据和约束下无法排入景点。可补充票价与营业时间，或放宽限制。"))
    return plan


def verify(payload, routing: RoutingProvider = DistanceRouting()):
    local_leg = routing.estimate
    data = payload.results
    if "plan" not in data:
        return Review(conflicts=[constraint("plan", "排程专家未返回行程。")])
    plan = Plan.model_validate(data["plan"])
    issues = list(plan.conflicts)
    intent = payload.intent
    issues.extend(constraint("clarification", message) for message in intent.constraints)
    if intent.budget is not None and plan.total > intent.budget:
        issues.append(constraint("over_budget", f"当前 {plan.total} {plan.currency}，上限 {intent.budget}。"
                                 "可减少付费景点、缩短行程或提高预算。"))
    if plan.total_cny != sum(item.cost_cny for day in plan.days for item in day.items):
        issues.append(constraint("total", "费用汇总与逐项费用不一致。"))
    if len(plan.days) != intent.days:
        issues.append(constraint("day_count", "生成天数与需求不一致。"))
    places = {(row.city, row.name): row for row in Attractions.model_validate(
        data.get("places", {"attractions": []})).attractions}
    transfers = {(row["origin"], row["destination"]): row
                 for row in data.get("transfer", {}).get("transfers", [])}
    previous_city = None
    for day in plan.days:
        if previous_city and previous_city != day.city:
            leg = transfers.get((previous_city, day.city))
            first = day.items[0] if day.items else None
            if not leg or not first or first.kind != "transfer" or first.end - first.start != leg["minutes"]:
                issues.append(constraint("transfer_validation", f"第 {day.number} 天跨城交通未通过校验。"))
        previous_city = day.city
        last = OPEN
        city = next((row for row in data.get("city", {}).get("cities", []) if row["name"] == day.city), None)
        position = (city["lat"], city["lon"]) if city else None
        for item in day.items:
            target = (item.lat, item.lon) if item.lat is not None and item.lon is not None else None
            minimum = 0
            if position and target and item.kind != "transfer":
                minimum, _, _ = local_leg(position, target, intent.transport)
            if item.start < last + max(minimum, item.travel_minutes) or item.end > CLOSE or item.end <= item.start:
                issues.append(constraint("time", f"第 {day.number} 天 {item.name} 的时段或转场冲突。"))
            last = item.end
            position = target or position
            if item.kind == "visit":
                place = places.get((day.city, item.name))
                if not place or opening(place, day.date, item.start, item.end) != item.start:
                    issues.append(constraint("hours", f"{item.name} 营业时段未通过校验。"))
        if sum(item.walking_km for item in day.items) > intent.walking_km:
            issues.append(constraint("walking", f"第 {day.number} 天步行超过 {intent.walking_km} 公里。"))
    for reservation in intent.reservations:
        if not any(day.number == reservation.day and day.city == reservation.city
                   and any(item.kind == "confirmed" and item.name == reservation.name
                           and item.start == reservation.start and item.end == reservation.start + reservation.duration
                           for item in day.items) for day in plan.days):
            issues.append(constraint("confirmed", f"未保留已确认安排 {reservation.name}。"))
    if intent.weather and not data.get("weather", {}).get("conditions"):
        issues.append(constraint("weather_data", "没有可用日期范围内的天气预报。", False))
    return Review(conflicts=issues)
