# 部署烟测手册

以下命令用于后端服务部署后的最小可用性验证。假设服务监听
`http://localhost:7777`；生产环境请替换为对应内网地址或 ingress 地址。

## 基础探针

当前仓库实际暴露的健康检查端点为 `/health.json`、`/readiness` 和
`/prometheus`。

```bash
BASE_URL=http://localhost:7777

curl -fsS "$BASE_URL/health.json"
curl -fsS "$BASE_URL/readiness"
curl -fsS "$BASE_URL/v1/models"
```

如果部署平台模板仍使用 `/health/liveness` 或 `/health/readiness`，需要同步改为
当前 API 端点，或在网关层显式配置兼容路由。

## 创建 Run

```bash
BASE_URL=http://localhost:7777

RUN_ID="$(
  curl -fsS "$BASE_URL/api/runs" \
    -H 'Content-Type: application/json' \
    -d '{
      "kind": "chat",
      "client_request_id": "smoke-chat-001",
      "chat": {
        "session_id": "smoke-session-001",
        "message": "请用一句话回复 smoke test"
      }
    }' | jq -r '.run_id'
)"

test -n "$RUN_ID"
```

## 查询 Run

```bash
curl -fsS "$BASE_URL/api/runs/$RUN_ID"
curl -fsS "$BASE_URL/api/runs/$RUN_ID/events?limit=20"
```

## 取消 Run

取消命令适合验证 API 链路与状态机响应。若 Run 已经进入终态，接口可能返回业务
冲突错误；这种情况下应确认查询接口能看到终态，而不是把取消冲突视为部署失败。

```bash
curl -fsS -X POST "$BASE_URL/api/runs/$RUN_ID/cancel"
```

## 判定标准

- `/health.json` 返回 HTTP 200，响应体包含 `{"status":"UP"}`。
- `/readiness` 返回 HTTP 200；若返回 503，必须检查 Redis、本地持久化目录或其他
  实际装配依赖。
- `/v1/models` 返回 HTTP 200 且包含至少一个模型。
- 创建 Run 返回 `run_id`，查询 Run 返回相同 `run_id`。
- 事件查询接口可返回 JSON 响应；为空事件列表不代表部署失败。
