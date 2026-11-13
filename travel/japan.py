from datetime import UTC, date, datetime

from .models import Attraction, Evidence

CHECKED = datetime(2026, 11, 13, tzinfo=UTC)
JAPAN_CITIES = {"Tokyo", "Osaka", "Kyoto"}
SMART_EX = "https://smart-ex.jp/en/product/plan/service/pdf/service-reserve_price.pdf"
RAIL = {
    frozenset(("Tokyo", "Osaka")): (14520, 200, "东京站 ↔ 新大阪站"),
    frozenset(("Tokyo", "Kyoto")): (13970, 180, "东京站 ↔ 京都站"),
    frozenset(("Osaka", "Kyoto")): (2880, 90, "新大阪站 ↔ 京都站"),
}


def official(source, provider):
    return Evidence(source=source, provider=provider, confidence="provider", retrieved_at=CHECKED)


def attractions(city):
    # Hours are normal operations, with separate last admission and known closures.
    rows = {
        "Tokyo": [
            ("秋叶原电气街（公共街区）", 35.6984, 139.7731, 0, 0, 1440, 0, [],
             "动漫", "https://www.gotokyo.org/en/spot/55/index.html", "公共街区免费，店铺营业时间各异。"),
            ("浅草寺本堂", 35.7148, 139.7967, 0, 390, 1020, 0, [],
             "历史", "https://www.gotokyo.org/en/spot/15/index.html", "常规开放，4–9 月 06:00 开门，其余月份 06:30。"),
            ("东京国立博物馆（常设展）", 35.7188, 139.7765, 1000, 570, 1020, 30, [0],
             "历史", "https://www.tnm.jp/modules/r_free_page/index.php?id=113-0",
             "周五和周六常规延长至 20:00。节假日、年末年初及特别展时间另查。"),
        ],
        "Osaka": [
            ("日本桥电电城（公共街区）", 34.6593, 135.5055, 0, 0, 1440, 0, [],
             "动漫", "https://osaka-info.jp/en/spot/nippombashi-denden-town/", "公共街区免费，店铺营业时间各异。"),
            ("道顿堀（公共街区）", 34.6687, 135.5013, 0, 0, 1440, 0, [],
             "美食", "https://www.osaka-info.jp/en/spot/dotonbori/", "公共街区免费，餐饮和其他项目另计。"),
            ("大阪城天守阁", 34.6873, 135.5262, 1200, 540, 1080, 30, [],
             "历史", "https://www.osakacastle.net/guide/?lang=en", "12 月 28 日至 1 月 1 日休馆。"),
            ("大阪历史博物馆（常设展）", 34.6828, 135.5205, 600, 570, 1020, 30, [1],
             "历史", "https://www.osakamushis.jp/eng/about/faq.html", "周二和年末年初休馆，节假日例外见官网。"),
        ],
        "Kyoto": [
            ("京都国际漫画博物馆", 35.0117, 135.7593, 1200, 600, 1020, 30, [2],
             "动漫", "https://kyotomm.jp/en/opentime-cost/", "周三通常休馆，节假日和临时安排见开放日历。"),
            ("锦市场（公共通道）", 35.0050, 135.7649, 0, 0, 1440, 0, [],
             "美食", "https://kyoto.travel/en/destinations/kyoto-nishiki-food-market/",
             "通道免费，店铺时间和消费各异。"),
        ],
    }
    result = []
    for name, lat, lon, yen, start, end, cutoff, closed, tag, source, note in rows[city]:
        hours = {day: [(start, end)] for day in range(7) if day not in closed}
        closures = []
        if name.startswith("东京国立"):
            hours.update({4: [(start, 1200)], 5: [(start, 1200)]})
            closures = ["12-22", "12-23", "12-24", "12-25", "12-26", "12-27", "12-28",
                        "12-29", "12-30", "12-31", "01-01"]
        if name in {"大阪城天守阁", "大阪历史博物馆（常设展）"}:
            closures = ["12-28", "12-29", "12-30", "12-31", "01-01"]
            if closed:
                closures += ["01-02", "01-03", "01-04"]
        result.append(Attraction(name=name, city=city, lat=lat, lon=lon, duration=60,
                                 hours=hours, price_jpy=yen, last_admission=cutoff, closed_dates=closures,
                                 tags=[tag], notes=note, evidence=official(source, "景点官网 / 官方旅游机构")))
    if city == "Kyoto":
        result.append(Attraction(
            name="京都国立博物馆（常设展）", city=city, lat=34.9901, lon=135.7721, duration=60,
            hours={day: [(570, 1200 if day == 4 else 1020)] for day in range(1, 7)},
            price_jpy=700, last_admission=30, tags=["历史"],
            valid_dates=[(date(2026, 12, 22), date(2026, 12, 27)), (date(2027, 1, 2), date(2027, 3, 22))],
            notes="仅在列出的常设展日期适用。特别展和仅开放庭园期间另计。",
            evidence=official("https://www.kyohaku.go.jp/eng/visit/info/", "京都国立博物馆官网"),
        ))
    return result
