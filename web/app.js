const $ = (id) => document.getElementById(id);
const sample = "我想去东京和大阪玩五天，预算 5000 元人民币，喜欢动漫，不想走太多路。";
const labels = {
  geocode: "目的地解析", attractions: "景点发现", food: "餐饮", mobility: "本地交通",
  budget: "预算", accommodation: "住宿", cross_city: "跨城交通", weather: "天气",
  schedule: "行程优化", verify: "约束验证", success: "验证通过", partial: "待补充",
  clarification: "需要澄清", failed: "失败", running: "执行中", completed: "完成",
  supplemented: "补充专家", missing: "能力缺口", evaluated: "候选评分", requested: "请求返工",
};
const provenance = {demo: "演示数据", estimate: "估算", provider: "提供者数据", user: "用户确认"};
let activeRun = null;

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function details(title, data) {
  const node = element("details");
  node.append(element("summary", title), element("pre", JSON.stringify(data, null, 2)));
  return node;
}

function trace(event) {
  if (event.event === "task_graph") {
    $("graph").replaceChildren(...event.tasks.map(task => {
      const node = element("span", labels[task.capability] || task.capability, "task");
      node.title = task.depends_on.length ? "依赖 " + task.depends_on.join(", ") : "无前置任务";
      return node;
    }));
  }
  const parts = [event.sequence, event.task_id, event.agent_id, event.agent_version,
    labels[event.status] || event.status].filter(Boolean);
  const row = element("li");
  row.append(details(parts.join(" · "), event));
  $("events").append(row);
}

function formatTime(minutes) {
  return [Math.floor(minutes / 60), minutes % 60].map(value => String(value).padStart(2, "0")).join(":");
}

let leafletMap;
let mapLayers;
let routeLayers;
let mapRevision = 0;


function map(days, candidates, anchors) {
  const groups = days.map(day => ({day, points: day.items.filter(item =>
    item.lat !== null && item.lon !== null)}))
    .filter(group => group.points.length);
  const discovered = candidates.map(place => ({...place, name: place.name + "（待核实）"}));
  $("map-area").hidden = groups.length === 0 && !discovered.length;
  if ($("map-area").hidden) return;
  const filter = $("map-day");
  const option = (label, value) => {
    const node = element("option", label);
    node.value = value;
    return node;
  };
  filter.replaceChildren(...(groups.length ? [option("全部日期", "")] : []), ...groups.map(({day}) =>
    option(`第 ${day.number} 天 · ${day.city}`, day.number)),
    ...(discovered.length ? [option("在线发现地点", "discovered")] : []));
  filter.value = groups.length ? String(groups[0].day.number) : "discovered";

  function draw() {
    const revision = ++mapRevision;
    const visible = groups.filter(({day}) => !filter.value || String(day.number) === filter.value);
    const points = filter.value === "discovered" ? discovered : visible.flatMap(group => group.points);
    const coordinates = points.map(point => [point.lat, point.lon]);
    $("map-key").replaceChildren(...points.map(point => element("li", point.name)));
    if (!leafletMap) {
      const L = window.L;
      leafletMap = L.map("map", {scrollWheelZoom: false, maxZoom: 19});
      const layer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · <a href="https://www.openstreetmap.org/fixthemap">纠错</a>',
      });
      layer.on("tileerror", () => {
        $("map-status").textContent = "部分底图瓦片加载失败，仍可查看已标记地点。";
        $("map-status").hidden = false;
      });
      layer.on("load", () => {
        if ($("map").querySelector(".leaflet-tile-loaded")) $("map-status").hidden = true;
      });
      layer.addTo(leafletMap);
      mapLayers = L.layerGroup().addTo(leafletMap);
      routeLayers = L.layerGroup().addTo(leafletMap);
    }
    const L = window.L;
    mapLayers.clearLayers();
    routeLayers.clearLayers();
    $("map-routes").replaceChildren();
    if (filter.value !== "discovered") {
      for (const {day} of visible) {
        for (const transfer of day.items.filter(item => item.kind === "transfer")) {
          $("map-routes").append(element("p", transfer.name + " · 城际路径未核实，地图只标记目的地", "muted"));
        }
        for (let index = 0; index < day.items.length; index++) {
          const anchor = anchors.find(city => city.name === day.city);
          const before = index ? day.items[index - 1] : anchor && {...anchor, name: day.city + " 市区参考点"};
          const after = day.items[index];
          if (!before || before.lat === null || after.lat === null || before.lat === after.lat && before.lon === after.lon) continue;
          const name = `${before.name} → ${after.name}`;
          const row = element("p", name + " · ", "muted");
          $("map-routes").append(row);
          if ([before, after].some(item => ["transfer", "confirmed"].includes(item.kind))) {
            row.append("铁路或用户安排段，路径未查询");
            continue;
          }
          const mode = after.transfer_mode === "taxi" ? "taxi" : "walk";
          const alternative = after.transfer_mode === "public";
          const button = element("button", alternative ? "查询步行备选" : mode === "walk" ? "查询步行路线" : "查询驾车路线");
          button.type = "button";
          row.append(button);
          if (alternative) row.append(" · 公交路径未核实");
          const runId = activeRun;
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              const result = await json(`/api/runs/${runId}/route?day=${day.number}&start=${index - 1}&end=${index}&mode=${mode}`);
              if (revision !== mapRevision) return;
              if (!result.route) {
                row.append(" · " + result.reason);
                button.remove();
                return;
              }
              const route = result.route;
              const info = element("span", `${alternative ? "步行备选" : mode === "walk" ? "步行" : "驾车"} · ${route.distance_m} 米 · ${Math.ceil(route.duration_s / 60)} 分钟 · OSRM / OSM`);
              L.polyline(route.coordinates, {color: "#216a57", weight: 4}).bindPopup(info).addTo(routeLayers);
              button.replaceWith(info);
            } catch {
              button.disabled = false;
              button.textContent = "查询失败，重试";
            }
          });
        }
      }
    }
    points.forEach((point, index) => {
      const icon = L.divIcon({className: "map-pin", html: String(index + 1), iconSize: [26, 26]});
      const popup = element("div");
      popup.append(element("strong", point.name),
        element("p", `${provenance[point.evidence.confidence]} · ${point.evidence.source}`));
      if (point.evidence.confidence !== "user") {
        const external = element("a", "在 OpenStreetMap 查看位置");
        external.href = `https://www.openstreetmap.org/?mlat=${point.lat}&mlon=${point.lon}#map=17/${point.lat}/${point.lon}`;
        external.target = "_blank";
        external.rel = "noopener noreferrer";
        popup.append(external);
      }
      L.marker(coordinates[index], {icon, title: point.name}).bindPopup(popup).addTo(mapLayers);
    });
    leafletMap.invalidateSize();
    leafletMap.fitBounds(coordinates, {padding: [35, 35], maxZoom: 15});
  }

  filter.onchange = draw;
  draw();
}

function show(result) {
  activeRun = result.run_id;
  document.querySelector(".continuation").hidden = !activeRun;
  $("notice").textContent = labels[result.status] || result.status;
  $("total").textContent = result.plan ?
    `${result.status === "partial" ? "已计入 " : ""}${result.plan.total.toLocaleString()} ${result.plan.currency}` : "";
  const itinerary = $("itinerary");
  itinerary.replaceChildren();
  for (const text of [...(result.questions || []), ...(result.conflicts || []).map(issue => issue.detail)]) {
    itinerary.append(element("p", text));
  }
  for (const day of result.plan?.days || []) {
    const card = element("article", undefined, "day");
    card.append(element("h3", `第 ${day.number} 天 · ${day.city}${day.date ? " · " + day.date : ""}`));
    const rows = element("ul");
    for (const item of day.items) {
      const row = element("li");
      row.append(element("strong", `${formatTime(item.start)}–${formatTime(item.end)} · ${item.name}`),
        element("small", `${item.cost_cny} CNY · ${item.reason}${item.travel_minutes ?
          " · 本地转场估算 " + item.travel_minutes + " 分钟" : ""}`));
      if (item.price_jpy != null) row.append(element("small", `官方参考价 ¥${item.price_jpy} JPY / 人`));
      const source = item.evidence;
      const description = `${provenance[source.confidence]} · ${source.provider}${source.stale ? " · 过期缓存" : ""}`;
      row.append(details(description, source));
      rows.append(row);
    }
    card.append(rows);
    itinerary.append(card);
  }
  for (const weather of result.weather || []) {
    itinerary.append(details(`${weather.city} · ${weather.date} · ${weather.low}–${weather.high} °C`, weather.evidence));
  }
  if (result.unscheduled_candidates?.length) {
    const candidates = element("details");
    candidates.append(element("summary", "已发现地点 · 营业时间或费用待核实"));
    for (const place of result.unscheduled_candidates) {
      candidates.append(details(`${place.city} · ${place.name}`, place.evidence));
    }
    itinerary.append(candidates);
  }
  const published = (result.attraction_catalog || []).filter(place => place.price_jpy != null);
  if (published.length) {
    const catalog = element("details");
    catalog.append(element("summary", "景点官网营业时间与成人票价"));
    for (const place of published) {
      const week = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
      const section = element("p");
      const link = element("a", `${place.city} · ${place.name}`);
      link.href = place.evidence.source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      section.append(link, element("small", ` · ¥${place.price_jpy} JPY / 人 · ${place.notes}`));
      section.append(element("small", week.map((label, day) => {
        const windows = place.hours?.[day];
        return label + " " + (windows?.map(([start, end]) => `${formatTime(start)}–${formatTime(end)}`).join(" / ") || "常规休馆");
      }).join(" · ")));
      if (place.valid_dates?.length) {
        section.append(element("small", place.valid_dates.map(([start, end]) => `${start} 至 ${end}`).join(" · ")));
      }
      catalog.append(section);
    }
    itinerary.append(catalog);
  }
  if (result.assumptions?.length) itinerary.append(details("规划口径", result.assumptions));
  map(result.plan?.days || [], result.unscheduled_candidates || [], result.city_anchors || []);
}

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function replay(id) {
  const data = await json("/api/runs/" + id);
  $("graph").replaceChildren();
  $("events").replaceChildren();
  data.events.forEach(trace);
  if (data.final_plan) show(data.final_plan);
  $("audit").replaceChildren(details("版本 Manifest", data.manifest),
    details("数据快照与哈希", data.evidence));
}

async function history() {
  const runs = await json("/api/runs");
  if (!runs.length) {
    $("runs").replaceChildren(element("p", "暂无运行。", "muted"));
    return;
  }
  const list = element("ul");
  for (const run of runs) {
    const row = element("li");
    const button = element("button", `${run.destinations.join(" → ") || run.run_id.slice(0, 8)} · ${labels[run.status]}`);
    button.type = "button";
    button.addEventListener("click", () => replay(run.run_id).catch(() => {
      $("notice").textContent = "无法读取运行记录。";
    }));
    row.append(button);
    list.append(row);
  }
  $("runs").replaceChildren(list);
}

async function consume(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let result;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    buffer += value;
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const [event, data] = block.split("\n");
      const payload = JSON.parse(data.slice(6));
      if (event === "event: trace") trace(payload);
      if (event === "event: result") result = payload;
    }
  }
  if (!result) throw new Error("Stream ended without result");
  return result;
}

$("sample").addEventListener("click", () => { $("request").value = sample; });
$("planner").addEventListener("submit", async event => {
  event.preventDefault();
  $("submit").disabled = true;
  $("notice").textContent = "正在规划…";
  $("events").replaceChildren();
  $("graph").replaceChildren();
  $("audit").replaceChildren();
  try {
    const response = await fetch("/api/plan/stream", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: $("request").value, previous_run: $("continue").checked ? activeRun : null})});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await consume(response);
    show(result);
    await Promise.all([replay(result.run_id), history()]);
  } catch (error) {
    $("notice").textContent = "本次请求未完成，请检查输入或重试。";
  } finally {
    $("submit").disabled = false;
  }
});
history().catch(() => { $("runs").textContent = "无法加载运行历史。"; });
