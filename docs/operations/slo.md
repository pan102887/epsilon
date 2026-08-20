# SLO 与告警基线

本文档定义当前后端运行时的最小 SLO、SLI 与告警基线。指标应优先来自
`/prometheus` 暴露的 Prometheus 指标、OpenTelemetry trace/metric，以及 Run
runtime 的状态事件。

## 核心 SLO

- Chat 请求成功率：99% / 30 天。
- Task Run 成功率：95% / 30 天。
- Run lost 比例：< 1% / 7 天。
- 首 token 延迟 p95：< 5s。
- Provider 5xx 或超时率：< 3% / 1 小时。
- 工具调用失败率：< 5% / 1 小时。

## P0 告警

- Run lost 比例 15 分钟内超过 5%。
- 所有 Provider 不可用持续 5 分钟。
- Redis Run store 连接失败持续 3 分钟。
- API 5xx 比例 10 分钟内超过 5%。

## 建议 SLI 口径

- Chat 请求成功率：`/api/chat` 与 `/v1/chat/completions` 的非 5xx 完成请求数 /
  总请求数；由客户端断开导致的取消应单独标注，不计入 Provider 故障。
- Task Run 成功率：终态为 `succeeded` 的 task run 数 / 进入终态的 task run 总数。
- Run lost 比例：终态或当前状态为 `lost` 的 run 数 / 创建 run 总数。
- 首 token 延迟 p95：流式接口从请求进入应用到第一个非空内容分片写出的耗时。
- Provider 5xx 或超时率：模型 adapter 捕获的 5xx、连接失败和超时次数 /
  Provider 调用总次数。
- 工具调用失败率：工具执行异常次数 / 工具调用总次数；权限拒绝、参数校验失败等
  用户输入类错误应单独分组。

## 告警处理原则

- P0 告警面向用户可见不可用或数据恢复风险，必须有明确值班响应。
- Provider 单点故障优先降级到健康 Provider；当所有 Provider 冷却不可用时，
  API 应返回明确业务错误，不应静默选择不健康 Provider。
- Redis Run store 告警需要同步检查 `SESSION_STORE_BACKEND` 是否为 `redis`、
  Redis 连接池错误、网络策略和 Secret 注入状态。
- 错误预算被持续消耗时，应暂停非紧急发布并优先处理可靠性缺陷。
