# Adaptive Travel Agent

A Traceable, Capability-Aware Multi-Agent Travel Planning System with Dynamic Expert Routing

一个 Python + FastAPI 课程项目。输入旅行需求后，Supervisor 生成任务图。Gateway 按能力、覆盖地区、契约、成本和执行历史挑选专家，确定性排程器与验证器处理时段、预算和步行限制。单城行程不执行跨城专家。跨城任务会从 Registry 激活合适的候补专家。

## 启动

需要 Python 3.11 或更新版本。

```sh
python -m venv .venv
```

Windows PowerShell 使用 ` .\.venv\Scripts\Activate.ps1`，macOS 或 Linux 使用 `source .venv/bin/activate`。然后安装并启动。

```sh
python -m pip install -r requirements.txt
python run.py
```

网页地址为 [127.0.0.1:8765](http://127.0.0.1:8765)。可用 `--no-browser` 关闭自动打开，用 `--port` 选择端口。

## 展示和截图

1. 点击「填入示例」再点击「生成行程」。检查五天行程、步行限制、预算和「补充专家」事件。
2. 保持「延续当前行程」勾选，输入「我还想加入京都，但总预算不变。」。确认第五天包含京都，历史中出现新运行。
3. 点击第一条东京 → 大阪运行，展开「版本 Manifest」「数据快照与哈希」和一条候选评分事件。
4. 用浏览器全页截图保存结果，或运行浏览器测试并设置 `TRAVEL_SCREENSHOT` 为本地 PNG 路径。测试截图是在回放首个行程时生成的。
5. 设 `TRAVEL_DISABLED=japan-rail,road-estimate` 后重启，重新提交双城示例，观察缺口与待补充状态。

启用 `TRAVEL_DEMO=1` 后，可输入「东京三日游，预算 3500 元」或「东京 2026-11-16 玩一天，预算 1 元，不用住宿」。后者触发一次有界预算返工，仍超预算时列出冲突。

## 数据和配置

可复制 `.env.example` 到 `.env`，`run.py` 会加载配置。已有进程环境变量优先。

| 设置 | 用途 |
| --- | --- |
| `TRAVEL_ONLINE=0` | 停用在线查询。默认启用地理编码、天气、汇率与按需路径查询。 |
| `TRAVEL_DEMO=1` | 显式切换到课程演示数据，复现预算返工和完整演示。默认关闭。 |
| `TRAVEL_OVERPASS_URL` | 可选的自建或获授权 Overpass endpoint。默认留空。 |
| `TRAVEL_LLM_URL`、`TRAVEL_LLM_MODEL`、`TRAVEL_LLM_KEY` | 可选的 OpenAI 兼容结构化需求解析。配置后会发送当前文本和上次结构化需求给该服务。 |
| `TRAVEL_DISABLED` | 逗号分隔的禁用专家 ID。 |
| `TRAVEL_RUNS`、`TRAVEL_CACHE` | 自定义本地运行与缓存目录。 |

东京、大阪、京都默认使用人工核对的景点官网和官方旅游机构快照、SmartEX 官方平季普通指定席参考价与 Frankfurter 汇率。节假日特例、临时闭馆、真实班次与余票需按出游日期核实。餐饮与酒店报价缺失时结果标为「待补充」，合计仅包括已计入项目。其他城市可通过 [Open-Meteo / GeoNames](https://open-meteo.com/en/docs/geocoding-api) 查找。配置获授权的 Overpass 后可查在线候选地点。

交互地图使用 Leaflet 和 [OpenStreetMap 在线底图](https://operations.osmfoundation.org/policies/tiles/)。可按天查看，并按需向 [FOSSGIS OSRM](https://routing.openstreetmap.de/about.html) 查询步行或驾车路径。仅在返回道路几何后绘制实线。公交段可查询步行备选。未核实的铁路或公交路径保留点位并提示。OSRM 结果用于地图查看，排程的市内转场仍采用估算。可用 `TRAVEL_OSRM_FOOT` 和 `TRAVEL_OSRM_CAR` 替换路线接口。

天气由 [Open-Meteo](https://open-meteo.com/en/docs) 提供，课程使用适用其[非商业条款](https://open-meteo.com/en/terms)。币种转换使用 [Frankfurter](https://frankfurter.dev/) 参考汇率。Overpass 返回 OpenStreetMap contributors 数据，适用 [OSM 版权与许可](https://www.openstreetmap.org/copyright)。外部请求串行节流，使用 TTL 缓存。服务不可用时可回读缓存，过期缓存会标记。

## 测试

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest -q -m "not online"
```

使用现有 Microsoft Edge 时，可设 `TRAVEL_BROWSER_CHANNEL=msedge` 并跳过 Chromium 下载。仅执行 API 和核心测试用 `python -m pytest -q -m "not browser and not online"`。设 `TRAVEL_LIVE_TEST=1` 后，用 `python -m pytest -q -m online` 单独验证在线里斯本地理编码。普通测试中的外部数据由 Mock 提供。

## 工程文档

- [架构、专家注册与审计](docs/architecture.md)
- [API 说明](docs/api.md)
- 启动服务后可查看 [Swagger UI](http://127.0.0.1:8765/docs)。

## 功能边界

- 规则解析支持文档列出的常见中文与英文句式。复杂日期范围、歧义城市、签证资格、无障碍和过敏要求需要澄清。LLM 是可选解析器，其配置和回退记录在审计中。
- 新城市能解析和发现地点。完整的可核实行程仍需该城市的票价、营业时间、餐饮、住宿和交通数据。演示覆盖范围外会显式降级。
- 排程采用按输入顺序分配城市和天数的启发式。每天 09:00–20:00，预算含两顿正餐、需要的住宿、活动和当地交通，首尾大交通另计。确认安排可由 API 传入。住宿按两人一间预估，无法处理真实库存或复杂预订。无确切日期时只安排各星期都适用的时段。
- 跨城覆盖日本演示铁路和部分同国专车估算。公共交通真实时刻表、导航、实时票价和自动预订尚未接入。
- 本地单用户展示服务。日志包含结构化旅行偏好与安排，默认只绑定回环地址。公开部署前需要另行实现认证、会话隔离和数据保留控制。

## 许可证

项目代码和文档采用 [MIT 许可证](LICENSE)。第三方 Leaflet 组件的许可证见 [Leaflet LICENSE](web/vendor/leaflet/LICENSE)。
