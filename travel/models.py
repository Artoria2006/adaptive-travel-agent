from datetime import date as CalendarDate, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Request(Model):
    text: str = Field(min_length=2, max_length=1200)
    previous_run: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    reservations: list["Reservation"] | None = Field(default=None, max_length=20)


class Reservation(Model):
    city: str
    name: str
    day: int = Field(ge=1, le=14)
    start: int = Field(ge=0, lt=1440)
    duration: int = Field(gt=0, le=720)
    cost: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Intent(Model):
    destinations: list[str] = Field(default_factory=list, max_length=6)
    days: int = Field(default=3, ge=1, le=14)
    start_date: CalendarDate | None = None
    budget: int | None = Field(default=None, gt=0, le=100000000)
    currency: Literal["CNY", "JPY", "USD", "RUB", "EUR"] = "CNY"
    travelers: int = Field(default=1, ge=1, le=30)
    interests: list[str] = Field(default_factory=list, max_length=12)
    walking_km: float = Field(default=6, gt=0, le=40)
    transport: Literal["public", "taxi", "walk"] = "public"
    lodging: bool = True
    weather: bool = False
    reservations: list[Reservation] = Field(default_factory=list, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class Evidence(Model):
    source: str
    provider: str
    retrieved_at: datetime | None = None
    valid_until: datetime | None = None
    confidence: Literal["demo", "estimate", "provider", "user"]
    stale: bool = False


class City(Model):
    name: str
    country: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    evidence: Evidence


class Place(Model):
    name: str
    city: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    evidence: Evidence


class Attraction(Place):
    duration: int = Field(gt=0, le=720)
    hours: dict[int, list[tuple[int, int]]] | None = None
    cost_cny: int | None = Field(default=None, ge=0)
    price_jpy: int | None = Field(default=None, ge=0)
    last_admission: int = 0
    closed_dates: list[str] = Field(default_factory=list)
    valid_dates: list[tuple[CalendarDate, CalendarDate]] = Field(default_factory=list)
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    walking_km: float = Field(default=0.5, ge=0)
    months: list[int] = Field(default_factory=lambda: list(range(1, 13)))


class Restaurant(Place):
    meal_cost_cny: int = Field(ge=0)


class Accommodation(Place):
    nightly_cny: int = Field(ge=0)


class TransportOption(Model):
    origin: str
    destination: str
    mode: str
    minutes: int = Field(gt=0)
    cost_cny: int = Field(ge=0)
    walking_km: float = Field(ge=0)
    evidence: Evidence
    price_jpy: int | None = None


class WeatherCondition(Model):
    city: str
    date: CalendarDate
    low: float
    high: float
    evidence: Evidence


class TravelConstraint(Model):
    code: str
    detail: str
    hard: bool = True


class Task(Model):
    id: str
    capability: str
    depends_on: list[str] = Field(default_factory=list)


class TaskInput(Model):
    intent: Intent
    results: dict[str, dict]
    attempt: int = Field(default=0, ge=0, le=1)


class Cities(Model):
    cities: list[City]
    unresolved: list[str] = Field(default_factory=list)


class Attractions(Model):
    attractions: list[Attraction]


class Food(Model):
    restaurants: list[Restaurant]


class Lodging(Model):
    hotels: list[Accommodation]


class Transfers(Model):
    transfers: list[TransportOption]


class Weather(Model):
    conditions: list[WeatherCondition]


class Budget(Model):
    cny_limit: int
    units_per_cny: float = Field(gt=0)
    evidence: Evidence
    jpy_per_cny: float | None = Field(default=None, gt=0)
    jpy_evidence: Evidence | None = None


class Mobility(Model):
    mode: Literal["public", "taxi", "walk"]
    walking_km: float


class Item(Model):
    name: str
    city: str
    start: int
    end: int
    cost_cny: int
    walking_km: float = 0
    reason: str
    evidence: Evidence
    kind: Literal["transfer", "visit", "meal", "lodging", "confirmed"]
    lat: float | None = None
    lon: float | None = None
    travel_minutes: int = 0
    price_jpy: int | None = None
    transfer_mode: str | None = None


class Day(Model):
    number: int
    date: CalendarDate | None = None
    city: str
    items: list[Item]


class Plan(Model):
    days: list[Day] = Field(default_factory=list)
    total_cny: int = 0
    total: float = 0
    currency: str = "CNY"
    attempt: int = 0
    conflicts: list[TravelConstraint] = Field(default_factory=list)


class Review(Model):
    conflicts: list[TravelConstraint]
