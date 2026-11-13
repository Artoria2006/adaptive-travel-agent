# API

FastAPI 自动生成的 OpenAPI 在 `/openapi.json`，交互文档在 `/docs`。

| 方法和路径 | 用途 |
| --- | --- |
| `POST /api/plan` | 执行规划并返回 JSON |
| `POST /api/plan/stream` | 执行规划并推送 SSE |
| `GET /api/runs` | 最近 50 次完成的运行 |
| `GET /api/runs/{run_id}` | Audit Replay |
| `GET /api/runs/{run_id}/route` | 查询同一天相邻地点的道路路径。传 `day`、从零起的 `start` 与 `end` 序号和 `mode=walk` 或 `taxi` |
| `GET /api/experts` | 专家元数据与契约 |

```json
{
  "text": "我还想加入京都，但总预算不变。",
  "previous_run": "替换为上次返回的 32 位十六进制 run_id"
}
```

`text` 长度为 2–1200。首轮省略 `previous_run`。可附 `reservations` 数组，字段为 `city`、`name`、`day`、`start`、`duration`、`cost`、`lat`、`lon`。时间从当地午夜起以分钟计，费用为全组人民币总额。传空数组可清除既有确认安排。

流式接口发送 `start`、`trace`、`result` 事件。终态包括 `success`、`partial`、`clarification` 和 `failed`。费用以 `cost_cny` 表示，汇总同时提供请求币种。非法请求返回 422，未知 run ID 返回 404。返回 `success` 表示通过当前数据与模型口径下的校验。
