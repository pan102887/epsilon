# 当前阶段质量硬化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不扩大业务功能面的前提下，优先补齐当前项目最值得做的 10 个质量、安全、测试、运维硬化点，把项目从“中上可交付”推进到“准生产可持续迭代”。

**Architecture:** 保持现有 DDD + 六边形架构，不把质量能力塞进业务层。安全配置、日志、静态门禁、CI、评估回归和前端契约校验分别落在各自最小边界内；所有后端依赖变更使用 `uv`，所有前端变更在写代码前先阅读 `epsilon-client/node_modules/next/dist/docs/` 下与 Next.js 16 相关的说明。

**Tech Stack:** FastAPI、Python 3.11+、uv、pytest、Next.js 16、React 19、TypeScript strict、Bun、GitHub Actions、OpenTelemetry、Prometheus。

---

## 执行原则

- 优先做“高风险、小改动、高收益”的事项：secret、日志脱敏、CI 门禁、架构守卫。
- 每个任务独立提交，避免一次性大改。
- 后端命令在 `epsilon-boot/` 下运行，必须使用 `uv`。
- 前端命令在 `epsilon-client/` 下运行，优先使用 `bun`。
- 涉及前端代码前，先阅读仓库指令 `epsilon-client/AGENTS.md`，再阅读 Next.js 16 对应文档。
- 修改配置时遵循 `docs/steering/config-source.md`：主配置先写 `epsilon-boot/config.properties`，环境变量只做覆盖。
- 修改代码时遵循 `docs/steering/code-documentation.md`：模块、公开类、公开函数/方法使用中文 docstring。

---

## 文件结构总览

计划可能涉及以下文件或目录：

- `epsilon-boot/config.properties`：移除敏感默认值，调整安全默认配置。
- `docs/configuration.md`：同步配置治理、生产 Secret 注入和日志策略说明。
- `docs/development.md`：同步新增质量门禁命令。
- `.github/workflows/ci.yml`：补齐后端/前端/安全/评估门禁。
- `epsilon-boot/pyproject.toml`：新增后端静态检查、架构守卫或测试依赖。
- `epsilon-boot/src/application/api/middlewares/request_logging.py`：请求/响应体日志脱敏和生产安全默认值。
- `epsilon-boot/src/application/api/middlewares/logging_config.py`：日志配置项扩展。
- `epsilon-boot/test/application/api/`：补日志脱敏与敏感字段测试。
- `epsilon-boot/test/static/`：补架构导入守卫、配置安全和 prompt/tool 安全静态测试。
- `epsilon-boot/src/infrastructure/agent/`：补 prompt injection / 工具滥用检测入口。
- `epsilon-boot/src/infrastructure/tools/`：补高风险工具参数验证。
- `epsilon-boot/src/infrastructure/model_access/`：补 provider 健康探测、退避、熔断。
- `docs/evaluation/`：接入评估回归脚本与固定基线说明。
- `epsilon-client/package.json`：新增 `typecheck`、`test`、`e2e` 脚本。
- `epsilon-client/src/lib/chat-api.ts`：补运行时 API 契约校验。
- `epsilon-client/src/hooks/use-chat.ts`、`epsilon-client/src/hooks/use-run.ts`：为可测试性做最小解耦。
- `epsilon-client/src/**/*.test.ts(x)`：新增前端单元/组件测试。
- `epsilon-client/e2e/`：新增主链路 E2E。
- `docs/operations/`：新增 SLO、告警、Redis Run 后端、部署烟测和发布手册。

---

## 10 个最值得做的点

### Task 1: 移除敏感默认值并建立 Secret 门禁

**为什么值得做：** 当前 `epsilon-boot/config.properties` 中存在 `DB_PASSWORD=root123` 形态的明文默认密码。即使它只是本地默认值，也会降低团队的 Secret 纪律，并给后续误提交真实凭证留下路径。

**Files:**
- Modify: `epsilon-boot/config.properties`
- Modify: `docs/configuration.md`
- Modify: `.github/workflows/ci.yml`
- Create: `epsilon-boot/test/static/test_config_secret_hygiene.py`

- [x] **Step 1: 写配置安全静态测试**

在 `epsilon-boot/test/static/test_config_secret_hygiene.py` 中新增测试，扫描主配置中禁止出现的弱口令形态：

```python
"""配置文件敏感默认值静态检查。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_ROOT / "config.properties"


def test_config_properties_does_not_ship_known_weak_passwords() -> None:
    """主配置文件不得提交常见弱口令或示例真实密码。"""

    content = CONFIG_FILE.read_text(encoding="utf-8")
    forbidden_fragments = [
        "DB_PASSWORD=root123",
        "DB_PASSWORD=password",
        "DB_PASSWORD=admin",
        "DB_PASSWORD=123456",
        "API_KEY=sk-",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in content
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd epsilon-boot
uv run pytest test/static/test_config_secret_hygiene.py -v
```

Expected: 测试因 `DB_PASSWORD=root123` 失败。

- [x] **Step 3: 移除明文默认密码**

将 `epsilon-boot/config.properties` 中数据库密码调整为显式空值：

```properties
DB_PASSWORD=
```

- [x] **Step 4: 同步配置文档**

在 `docs/configuration.md` 的 MySQL 配置说明附近补充规则：

```markdown
- `DB_PASSWORD` 在仓库默认配置中必须为空；生产、预发和个人环境通过环境变量或 Secret 管理系统覆盖。
- 禁止把真实 API Key、数据库密码、Redis 密码写入 `config.properties`、`.env` 示例或文档代码块。
```

- [x] **Step 5: CI 增加 Secret 静态检查入口**

在 `.github/workflows/ci.yml` 后端测试之前增加静态测试会随 pytest 自动执行；不需要单独 job。确保全量命令覆盖该文件：

```bash
uv run pytest -m "not benchmark"
```

- [x] **Step 6: 验证**

Run:

```bash
cd epsilon-boot
uv run pytest test/static/test_config_secret_hygiene.py -v
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-boot
uv run pytest test/static/test_config_secret_hygiene.py -v
```

---

### Task 2: 请求/响应日志默认安全化与字段级脱敏

**为什么值得做：** `RequestLoggingMiddleware` 当前会记录请求体和响应体全文截断结果。AI Agent 请求体可能包含 prompt、工具参数、审批内容、token 或用户敏感信息，只截断不脱敏不足以支撑生产默认开启。

**Files:**
- Modify: `epsilon-boot/src/application/api/middlewares/logging_config.py`
- Modify: `epsilon-boot/src/application/api/middlewares/request_logging.py`
- Modify: `epsilon-boot/config.properties`
- Create: `epsilon-boot/test/application/api/test_request_logging_redaction.py`

- [x] **Step 1: 写日志脱敏测试**

新增测试覆盖 JSON body 中的敏感字段：

```python
"""请求日志脱敏策略测试。"""

from application.api.middlewares.request_logging import _safe_decode_body


def test_safe_decode_body_redacts_sensitive_json_fields() -> None:
    """日志输出不得暴露常见敏感字段值。"""

    raw = b'{"api_key":"sk-test-secret","password":"root123","message":"hello"}'

    decoded = _safe_decode_body(raw)

    assert "sk-test-secret" not in decoded
    assert "root123" not in decoded
    assert '"api_key":"***"' in decoded or '"api_key": "***"' in decoded
    assert '"password":"***"' in decoded or '"password": "***"' in decoded
    assert "hello" in decoded
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd epsilon-boot
uv run pytest test/application/api/test_request_logging_redaction.py -v
```

Expected: FAIL，当前 `_safe_decode_body` 不会脱敏 JSON 字段。

- [x] **Step 3: 扩展日志配置**

在 `logging_config.py` 中增加敏感字段配置，默认至少包含：

```python
sensitive_body_fields: str = "password,api_key,token,access_token,refresh_token,secret,authorization,cookie"
```

并提供集合读取方法：

```python
def get_sensitive_body_fields_set(self) -> set[str]:
    """返回需要从请求体和响应体日志中脱敏的字段名集合。"""

    return {
        item.strip().lower()
        for item in self.sensitive_body_fields.split(",")
        if item.strip()
    }
```

- [x] **Step 4: 实现 JSON 字段脱敏**

在 `request_logging.py` 中让 `_safe_decode_body` 对 JSON object/list 递归脱敏；无法解析 JSON 时保留原有截断逻辑。

核心行为要求：

```python
SENSITIVE_REPLACEMENT = "***"
```

- dict 中 key 小写后命中敏感字段集合，value 替换为 `***`。
- list 中每个元素递归处理。
- 非 JSON 文本只做截断，不尝试正则误伤。

- [x] **Step 5: 生产默认关闭 body 日志**

在 `config.properties` 中新增或调整：

```properties
LOGGING_REQUEST_BODY_ENABLED=false
LOGGING_RESPONSE_BODY_ENABLED=false
```

如果现有配置模型没有对应字段，按 Task 2 Step 3 同步增加。

- [x] **Step 6: 验证**

Run:

```bash
cd epsilon-boot
uv run pytest test/application/api/test_request_logging_redaction.py -v
uv run pytest test/test_log_exception_newline.py -v
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-boot
uv run pytest test/application/api/test_request_logging_redaction.py -v
uv run pytest test/test_log_exception_newline.py -v
uv run pytest test/application/test_api_adapter_layout.py -v
uv run pytest test/static/test_config_secret_hygiene.py -v
```

---

### Task 3: Prompt Injection 与高风险工具参数防御分层

**为什么值得做：** 历史评估 `docs/evaluation/report.md` 已把 Prompt Injection 防御列为 P0。Agent 系统的高风险面不是普通 Web 入参，而是“用户输入诱导模型调用工具”。

**Files:**
- Modify: `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
- Modify: `epsilon-boot/src/infrastructure/tools/shell_exec/`
- Modify: `epsilon-boot/src/infrastructure/tools/http_request/`
- Create: `epsilon-boot/test/static/test_tool_guardrail_policy.py`
- Create: `docs/security/agent-tool-guardrails.md`

- [x] **Step 1: 建立工具参数安全清单文档**

新增 `docs/security/agent-tool-guardrails.md`，写入以下硬规则：

```markdown
# Agent 工具安全护栏

## ShellExecTool

默认关闭。开启后必须满足：

- 禁止执行 `rm -rf /`、`mkfs`、`dd if=`、`:(){ :|:& };:` 等破坏性命令。
- 禁止读取常见密钥路径，例如 `~/.ssh/id_rsa`、`.env`、`/etc/shadow`。
- 禁止下载并直接执行远程脚本，例如 `curl ... | sh`、`wget ... | bash`。
- 所有命令必须落入 Workspace 风险策略和 HITL 审批策略。

## HttpRequestTool

默认只允许 http/https URL。生产环境建议配置 allowlist：

- 禁止访问 metadata endpoint，例如 `169.254.169.254`。
- 禁止访问 localhost / RFC1918 网段，除非显式 allowlist。
- 禁止把 Authorization、Cookie、API Key 放入模型可控参数。
```

- [x] **Step 2: 写静态策略测试**

新增测试扫描高风险工具实现必须包含阻断关键词或策略函数：

```python
"""高风险工具护栏静态检查。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_shell_exec_tool_has_dangerous_command_policy() -> None:
    """Shell 执行工具必须包含破坏性命令阻断策略。"""

    tool_dir = PROJECT_ROOT / "src" / "infrastructure" / "tools" / "shell_exec"
    content = "\n".join(path.read_text(encoding="utf-8") for path in tool_dir.glob("*.py"))

    required_markers = ["rm -rf", "curl", "wget", "BLOCKED"]
    for marker in required_markers:
        assert marker in content


def test_http_request_tool_has_private_network_policy() -> None:
    """HTTP 请求工具必须包含私网和 metadata endpoint 防护策略。"""

    tool_dir = PROJECT_ROOT / "src" / "infrastructure" / "tools" / "http_request"
    content = "\n".join(path.read_text(encoding="utf-8") for path in tool_dir.glob("*.py"))

    required_markers = ["169.254.169.254", "localhost", "private"]
    for marker in required_markers:
        assert marker in content
```

- [x] **Step 3: 运行测试确认当前缺口**

Run:

```bash
cd epsilon-boot
uv run pytest test/static/test_tool_guardrail_policy.py -v
```

Expected: 如果工具已有部分策略，可能部分 PASS；未覆盖项必须 FAIL。

- [x] **Step 4: 实现最小阻断策略**

在 Shell / HTTP 工具各自 adapter 内增加私有校验函数：

```python
def _is_blocked_shell_command(command: str) -> bool:
    """判断命令是否命中破坏性或凭证读取风险模式。"""

    lowered = command.lower()
    blocked_fragments = (
        "rm -rf /",
        "mkfs",
        "dd if=",
        "curl ",
        "| sh",
        "| bash",
        "wget ",
        "/etc/shadow",
        ".ssh/id_rsa",
    )
    return any(fragment in lowered for fragment in blocked_fragments)
```

HTTP 工具增加：

```python
def _is_blocked_url_target(host: str) -> bool:
    """判断 URL host 是否命中 metadata、localhost 或私网风险目标。"""

    lowered = host.lower().strip("[]")
    return lowered in {"localhost", "127.0.0.1", "169.254.169.254"}
```

- [x] **Step 5: 验证**

Run:

```bash
cd epsilon-boot
uv run pytest test/static/test_tool_guardrail_policy.py -v
uv run pytest -m "not benchmark"
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-boot
uv run pytest test/static/test_tool_guardrail_policy.py -v
uv run pytest -m "not benchmark"
```

---

### Task 4: 工具调用滥用检测与 OpenTelemetry 事件

**为什么值得做：** Prompt Injection 防御只能挡一部分风险；Agent 运行中还需要发现“一轮内高频调用同一工具”“异常参数模式”“连续失败重试”等滥用或失控迹象。

**Files:**
- Modify: `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
- Modify: `epsilon-boot/src/infrastructure/telemetry/`
- Create: `epsilon-boot/test/infrastructure/agent/test_tool_abuse_detection.py`

- [x] **Step 1: 写滥用检测测试**

新增测试覆盖同一工具高频调用被标记：

```python
"""Agent 工具调用滥用检测测试。"""


def test_tool_abuse_detector_flags_repeated_tool_calls() -> None:
    """同一轮运行中同一工具调用次数超过阈值时应命中滥用检测。"""

    from infrastructure.agent.tool_abuse_detector import ToolAbuseDetector

    detector = ToolAbuseDetector(max_same_tool_calls=5)

    verdicts = [detector.record_tool_call("shell_exec", {"command": "pwd"}) for _ in range(6)]

    assert verdicts[-1].abuse_detected is True
    assert verdicts[-1].reason == "same_tool_call_limit_exceeded"
```

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd epsilon-boot
uv run pytest test/infrastructure/agent/test_tool_abuse_detection.py -v
```

Expected: FAIL，`ToolAbuseDetector` 尚不存在。

- [x] **Step 3: 新增检测器**

创建 `epsilon-boot/src/infrastructure/agent/tool_abuse_detector.py`：

```python
"""Agent 工具调用滥用检测。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolAbuseVerdict:
    """单次工具调用滥用检测结果。"""

    abuse_detected: bool
    reason: str | None = None


@dataclass
class ToolAbuseDetector:
    """统计单个 Agent run 内的工具调用模式并识别滥用迹象。"""

    max_same_tool_calls: int = 5
    _counts: dict[str, int] = field(default_factory=dict)

    def record_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> ToolAbuseVerdict:
        """记录一次工具调用并返回是否命中滥用策略。"""

        del arguments
        count = self._counts.get(tool_name, 0) + 1
        self._counts[tool_name] = count
        if count > self.max_same_tool_calls:
            return ToolAbuseVerdict(True, "same_tool_call_limit_exceeded")
        return ToolAbuseVerdict(False)
```

- [x] **Step 4: 接入 ReAct 工具执行节点**

在 `react_agent_adapter.py` 的工具执行前调用 detector；命中时：

- 记录 OpenTelemetry event：`agent.tool_abuse_detected`
- 记录结构化日志字段：`tool_name`、`reason`
- 中止当前自动工具执行或转入审批/guardrail 阻断路径

- [x] **Step 5: 验证**

Run:

```bash
cd epsilon-boot
uv run pytest test/infrastructure/agent/test_tool_abuse_detection.py -v
uv run pytest test/infrastructure/agent -k "tool" -v
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-boot
uv run pytest test/infrastructure/agent/test_tool_abuse_detection.py -v
uv run pytest test/infrastructure/agent -k tool -v
uv run python -m compileall src/infrastructure/agent/tool_abuse_detector.py src/infrastructure/agent/react_agent_adapter.py test/infrastructure/agent/test_tool_abuse_detection.py
```

Evaluator verdict: PASS。

---

### Task 5: CI 补齐前端、后端、安全和构建门禁

**为什么值得做：** 当前 CI 主要跑后端 pytest，不能覆盖前端 lint/build/typecheck、安全扫描和评估回归。质量体系必须从“文档建议”升级为“PR 自动阻断”。

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `epsilon-client/package.json`
- Modify: `docs/development.md`

- [x] **Step 1: 前端增加 typecheck 脚本**

修改 `epsilon-client/package.json` scripts：

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit"
  }
}
```

- [x] **Step 2: 本地验证前端脚本**

Run:

```bash
cd epsilon-client
bun run lint
bun run typecheck
bun run build
```

Expected: 全部 PASS；如果受限沙箱导致 Next/Turbopack 本地端口绑定失败，在变更记录中写明环境限制，并在正常开发机或 CI 上重跑。

- [x] **Step 3: CI 增加前端 job**

在 `.github/workflows/ci.yml` 增加 `frontend` job：

```yaml
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: epsilon-client
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Setup Bun
        uses: oven-sh/setup-bun@v2
      - name: Install dependencies
        run: bun install --frozen-lockfile
      - name: Lint
        run: bun run lint
      - name: Typecheck
        run: bun run typecheck
      - name: Build
        run: bun run build
```

- [x] **Step 4: CI 后端 job 保持 uv 冻结依赖**

确认后端 job 继续使用：

```yaml
- name: Sync dependencies
  run: uv sync --frozen
- name: Run tests
  run: uv run pytest -m "not benchmark"
```

- [x] **Step 5: 更新开发文档**

在 `docs/development.md` 前端命令块补充：

```bash
bun run typecheck  # TypeScript 类型检查
```

- [x] **Step 6: 验证 CI YAML 结构**

Run:

```bash
git diff -- .github/workflows/ci.yml epsilon-client/package.json docs/development.md
```

Expected: 仅包含 CI、脚本、文档变更。

**已验证：**

```bash
cd epsilon-client
bun run typecheck
bun run lint
bun run build
```

```bash
git diff -- .github/workflows/ci.yml epsilon-client/package.json docs/development.md
git diff --name-only
```

验证结果：前端 `typecheck`、`lint`、`build` 均 PASS；`build` 仅出现非阻塞 Next.js workspace-root warning。`spec-evaluator` verdict: PASS。最终 tracked implementation diff 仅包含 `.github/workflows/ci.yml`、`epsilon-client/package.json`、`docs/development.md`。

**状态：** 已完成并随提交 `4cc0ec2` 入库。

---

### Task 6: 后端静态检查与 DDD import guard

**为什么值得做：** 当前存在 `common -> infrastructure`、`infrastructure -> application` 的边界泄漏。架构规范如果只写在文档里，后续还会反复破窗。

**Files:**
- Modify: `epsilon-boot/pyproject.toml`
- Create: `epsilon-boot/test/static/test_architecture_import_boundaries.py`
- Modify: `.github/workflows/ci.yml`

- [x] **Step 1: 写 import boundary 测试**

新增 `test/static/test_architecture_import_boundaries.py`：

```python
"""DDD 分层导入边界静态测试。"""

import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_domain_does_not_import_application_or_infrastructure() -> None:
    """领域层不得依赖应用层或基础设施层。"""

    for path in (SRC_ROOT / "domain").rglob("*.py"):
        imports = _imports_for(path)
        forbidden = [name for name in imports if name.startswith(("application", "infrastructure"))]
        assert forbidden == [], f"{path} imports forbidden modules: {forbidden}"


def test_common_does_not_import_application_or_infrastructure() -> None:
    """公共层不得依赖应用层或基础设施层。"""

    allowed_legacy = {"common/tools/common_tools.py"}
    for path in (SRC_ROOT / "common").rglob("*.py"):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative in allowed_legacy:
            continue
        imports = _imports_for(path)
        forbidden = [name for name in imports if name.startswith(("application", "infrastructure"))]
        assert forbidden == [], f"{path} imports forbidden modules: {forbidden}"
```

- [x] **Step 2: 运行静态测试**

Run:

```bash
cd epsilon-boot
uv run pytest test/static/test_architecture_import_boundaries.py -v
```

Expected: domain 测试 PASS；common 测试允许当前 legacy 薄壳，不放大破窗。

- [x] **Step 3: 增加 ruff 依赖和脚本**

Run:

```bash
cd epsilon-boot
uv add --dev ruff
```

在 `pyproject.toml` 增加最小 ruff 配置：

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = []
```

- [x] **Step 4: CI 增加静态检查**

在后端 job 中 pytest 前增加：

```yaml
- name: Lint backend
  run: uv run ruff check src test
```

- [x] **Step 5: 验证**

Run:

```bash
cd epsilon-boot
uv run ruff check src test
uv run pytest test/static/test_architecture_import_boundaries.py -v
```

Expected: PASS；若 ruff 首次暴露大量历史问题，先用 `uv run ruff check src test --fix` 修复安全项，再人工复核 diff。

**已验证：**

```bash
cd epsilon-boot
uv run ruff check src test
uv run pytest test/static/test_architecture_import_boundaries.py -v
```

验证结果：`ruff check src test` PASS；架构边界静态测试 `4 passed`。`spec-evaluator` verdict: PASS。已完成并随提交 `4cc0ec2` 入库。

---

### Task 7: 评估回归接入 CI 或 Nightly

**为什么值得做：** `docs/evaluation/report.md` 已有评估体系，但报告自己说明是历史快照。Agent 项目的质量不能只看单元测试，还要看工具调用成功率、委派正确性、上下文压缩和 prompt 版本变化。

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/evaluation/README.md` 或新增 `docs/evaluation/ci.md`
- Modify: `docs/development.md`

- [x] **Step 1: 盘点评估入口命令**

在仓库内确认实际评估脚本入口，优先查找：

```bash
find . -path '*evaluation*' -type f | sort
```

选择现有可复跑且不依赖外部真实 LLM 的最小命令作为 PR 门禁；依赖真实模型的完整评估放 nightly。

- [x] **Step 2: 文档化评估门禁策略**

新增或更新 `docs/evaluation/ci.md`：

```markdown
# Evaluation CI 策略

- PR 必跑：不依赖外部 Provider 的 schema、fixture、聚合和静态评估。
- Nightly 跑：需要真实模型或外部服务的完整评估。
- 任一核心指标相对固定基线回退超过 5 个百分点时，阻断合并或标红告警。
- 固定基线文件必须放在 `docs/evaluation/results/`，更新基线需要在 PR 描述中说明原因。
```

- [x] **Step 3: CI 增加 PR 轻量评估 job**

如果现有脚本支持本地 fixture，添加 job：

```yaml
  evaluation:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: epsilon-boot
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - name: Sync dependencies
        run: uv sync --frozen
      - name: Run lightweight evaluation checks
        run: uv run python ../scripts/evaluation/verify_report.py
```

如果脚本路径不同，以 Step 1 找到的实际入口替换命令。

- [x] **Step 4: Nightly 增加完整评估计划**

在 CI 中增加 schedule，但默认不要求真实密钥即可通过 PR：

```yaml
on:
  schedule:
    - cron: "17 19 * * *"
```

完整评估 job 使用 `if: github.event_name == 'schedule'` 限定。

- [x] **Step 5: 验证**

Run:

```bash
git diff -- .github/workflows/ci.yml docs/evaluation docs/development.md
```

Expected: PR 轻量评估和 nightly 完整评估边界清晰，不让外部 Provider 密钥成为普通 PR 的硬依赖。

---

### Task 8: 前端 API 契约运行时校验

**为什么值得做：** `chat-api.ts` 靠 TypeScript interface 和 `as` 断言，无法防止后端运行时返回字段漂移。RunView、SSE、Task 这类复杂协议需要在边界处快速失败。

**Files:**
- Modify: `epsilon-client/package.json`
- Modify: `epsilon-client/src/lib/chat-api.ts`
- Create: `epsilon-client/src/lib/chat-api.schema.ts`
- Create: `epsilon-client/src/lib/chat-api.schema.test.ts`

- [x] **Step 1: 增加运行时 schema 依赖**

优先选择轻量 schema 库。示例使用 `zod`：

```bash
cd epsilon-client
bun add zod
```

- [x] **Step 2: 新增 schema 文件**

创建 `src/lib/chat-api.schema.ts`，先覆盖 `RunSnapshot` 和 `TaskExecuteResponse` 两条高风险协议：

```ts
import { z } from "zod";

export const runStatusSchema = z.enum([
  "queued",
  "running",
  "paused",
  "awaiting_approval",
  "cancel_requested",
  "cancelled",
  "succeeded",
  "failed",
  "lost",
]);

export const runSnapshotSchema = z.object({
  code: z.number(),
  run_id: z.string(),
  kind: z.enum(["chat", "task"]),
  status: runStatusSchema,
  client_request_id: z.string().nullable(),
  payload_hash: z.string().nullable(),
  latest_event_cursor: z.number().nullable(),
  result: z.record(z.string(), z.unknown()).nullable(),
  error: z.record(z.string(), z.unknown()).nullable(),
  approval_id: z.string().nullable(),
  can_continue: z.boolean(),
  terminal_reason: z.string().nullable(),
  segment_metadata: z.record(z.string(), z.unknown()).nullable(),
  latest_checkpoint_id: z.string().nullable(),
  recoverable: z.boolean(),
  recovery_attempt_count: z.number(),
  last_recovery_error: z.record(z.string(), z.unknown()).nullable(),
  task_classification: z.string().nullable(),
  guardrail_summary: z.record(z.string(), z.unknown()).nullable(),
  workflow_name: z.string().nullable(),
  workflow_run_state: z.record(z.string(), z.unknown()).nullable(),
  collaboration_summary: z.record(z.string(), z.unknown()).nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number(),
});
```

- [x] **Step 3: 写 schema 单测**

创建 `src/lib/chat-api.schema.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import { runSnapshotSchema } from "./chat-api.schema";

it("accepts a valid run snapshot", () => {
  const parsed = runSnapshotSchema.parse({
    code: 0,
    run_id: "run-1",
    kind: "task",
    status: "running",
    client_request_id: null,
    payload_hash: null,
    latest_event_cursor: 1,
    result: null,
    error: null,
    approval_id: null,
    can_continue: false,
    terminal_reason: null,
    segment_metadata: null,
    latest_checkpoint_id: null,
    recoverable: false,
    recovery_attempt_count: 0,
    last_recovery_error: null,
    task_classification: null,
    guardrail_summary: null,
    workflow_name: null,
    workflow_run_state: null,
    collaboration_summary: null,
    created_at: "2026-06-16T00:00:00Z",
    updated_at: "2026-06-16T00:00:00Z",
    version: 1,
  });

  expect(parsed.run_id).toBe("run-1");
});

describe("runSnapshotSchema", () => {
  it("rejects unknown run status", () => {
    expect(() =>
      runSnapshotSchema.parse({
        code: 0,
        run_id: "run-1",
        kind: "task",
        status: "unknown",
        client_request_id: null,
        payload_hash: null,
        latest_event_cursor: null,
        result: null,
        error: null,
        approval_id: null,
        can_continue: false,
        terminal_reason: null,
        segment_metadata: null,
        latest_checkpoint_id: null,
        recoverable: false,
        recovery_attempt_count: 0,
        last_recovery_error: null,
        task_classification: null,
        guardrail_summary: null,
        workflow_name: null,
        workflow_run_state: null,
        collaboration_summary: null,
        created_at: "2026-06-16T00:00:00Z",
        updated_at: "2026-06-16T00:00:00Z",
        version: 1,
      }),
    ).toThrow();
  });
});
```

- [x] **Step 4: 增加测试脚本**

在 `package.json` 增加：

```json
"test": "vitest run"
```

并安装：

```bash
cd epsilon-client
bun add -d vitest
```

- [x] **Step 5: 接入 `chat-api.ts`**

在获取 Run snapshot 的函数中使用：

```ts
const json = await res.json();
return runSnapshotSchema.parse(json);
```

- [x] **Step 6: 验证**

Run:

```bash
cd epsilon-client
bun run test
bun run typecheck
bun run lint
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-client
bun run test
bun run typecheck
bun run lint
```

---

### Task 9: 前端主链路自动化测试

**为什么值得做：** 当前前端没有测试脚本。Chat SSE、Run 事件流、Task 状态变化、abort/continue 是高回归风险路径，必须用自动化测试保护。

**Files:**
- Modify: `epsilon-client/package.json`
- Create: `epsilon-client/src/hooks/use-chat.test.tsx`
- Create: `epsilon-client/src/hooks/use-run.test.tsx`
- Create: `epsilon-client/e2e/chat-run-task.spec.ts`
- Create: `epsilon-client/playwright.config.ts`

- [x] **Step 1: 安装测试依赖**

```bash
cd epsilon-client
bun add -d vitest @testing-library/react @testing-library/jest-dom jsdom @playwright/test
```

- [x] **Step 2: 增加脚本**

`package.json` 增加：

```json
"test": "vitest run",
"test:watch": "vitest",
"e2e": "playwright test"
```

- [x] **Step 3: 创建 Vitest 配置**

如果项目没有配置，新建 `vitest.config.ts`：

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

- [x] **Step 4: 写 `use-chat` 基础测试**

测试目标：mock `fetch`，验证发送消息后用户消息和助手消息都进入列表，错误时错误状态可见。

- [x] **Step 5: 写 `use-run` 基础测试**

测试目标：mock Run snapshot 和 event stream，验证 `refresh` 能更新状态，`cancel` 后能重新拉取 snapshot。

- [x] **Step 6: 写 Playwright 主链路测试**

`e2e/chat-run-task.spec.ts` 覆盖：

```ts
import { expect, test } from "@playwright/test";

test("renders the agent console shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("Epsilon")).toBeVisible();
  await expect(page.getByText("Agent console for chat, task runs, and execution visibility.")).toBeVisible();
});
```

- [x] **Step 7: 验证**

Run:

```bash
cd epsilon-client
bun run test
bun run e2e:install  # 首次运行或 Playwright 浏览器缺失时安装 Chromium
bun run e2e
bun run lint
bun run typecheck
bun run build
```

Expected: PASS。

**已验证：**

```bash
cd epsilon-client
bun run test
bun run lint
bun run typecheck
bun run build
bun run e2e
```

验证结果：Vitest `3 passed / 9 tests passed`；ESLint PASS（无 warning）；TypeScript `tsc --noEmit` PASS；Next build PASS；Playwright E2E `2 passed`，覆盖首页壳层、Chat SSE、abort 控件、Task continue 与 Run 事件/状态展示。

---

### Task 10: Provider 可靠性、SLO、告警和生产 Run 后端手册

**为什么值得做：** 当前项目已有 OTel、Prometheus、Run runtime 和 Redis/file 双后端，但还缺生产运维闭环：SLO、告警、provider 健康、退避熔断、Redis Run 配置和部署烟测。

**Files:**
- Modify: `epsilon-boot/src/infrastructure/model_access/`
- Modify: `epsilon-boot/src/infrastructure/telemetry/`
- Create: `docs/operations/slo.md`
- Create: `docs/operations/run-runtime-production.md`
- Create: `docs/operations/deployment-smoke-test.md`
- Create: `epsilon-boot/test/infrastructure/model_access/test_provider_health_policy.py`

- [x] **Step 1: 写 Provider 健康策略测试**

新增测试：连续失败达到阈值后，provider 在 TTL 内不可选。

```python
"""模型 Provider 健康策略测试。"""


def test_provider_health_policy_opens_circuit_after_consecutive_failures() -> None:
    """连续失败达到阈值后 Provider 应进入短期熔断状态。"""

    from infrastructure.model_access.provider_health_policy import ProviderHealthPolicy

    policy = ProviderHealthPolicy(max_consecutive_failures=3, cooldown_seconds=60)

    policy.record_failure("qwen")
    policy.record_failure("qwen")
    policy.record_failure("qwen")

    assert policy.is_available("qwen") is False
```

- [x] **Step 2: 实现最小 ProviderHealthPolicy**

创建 `provider_health_policy.py`，包含：

- `record_success(provider_name)`：清零失败计数。
- `record_failure(provider_name)`：失败计数 +1，达到阈值后设置 cooldown。
- `is_available(provider_name)`：cooldown 内返回 false。

- [x] **Step 3: 接入 provider registry 或路由层**

在模型路由选择 provider 前过滤不可用 provider；所有 provider 都不可用时返回明确业务错误，不静默 fallback 到错误 provider。

- [x] **Step 4: 定义 SLO 文档**

新增 `docs/operations/slo.md`：

```markdown
# SLO 与告警基线

## 核心 SLO

- Chat 请求成功率：99% / 30 天
- Task Run 成功率：95% / 30 天
- Run lost 比例：< 1% / 7 天
- 首 token 延迟 p95：< 5s
- Provider 5xx 或超时率：< 3% / 1 小时
- 工具调用失败率：< 5% / 1 小时

## P0 告警

- Run lost 比例 15 分钟内超过 5%
- 所有 Provider 不可用持续 5 分钟
- Redis Run store 连接失败持续 3 分钟
- API 5xx 比例 10 分钟内超过 5%
```

- [x] **Step 5: 写生产 Run 后端手册**

新增 `docs/operations/run-runtime-production.md`，明确：

```markdown
# Run Runtime 生产配置

- 多副本生产必须使用 `SESSION_STORE_BACKEND=redis`。
- 本地文件 Run store 只适合单主机单实例。
- 每个 Pod 使用单 uvicorn worker，通过 K8S replica 扩容。
- `RUN_WORKER_COUNT`、`RUN_MAX_RUNNING_RUNS` 按 Provider 限流和工具风险预算设置。
- 不承诺外部副作用 exactly-once；依赖 checkpoint ledger 做 bounded recovery。
```

- [x] **Step 6: 写部署烟测手册**

新增 `docs/operations/deployment-smoke-test.md`，至少包含：

```bash
curl -fsS http://localhost:7777/health/liveness
curl -fsS http://localhost:7777/health/readiness
curl -fsS http://localhost:7777/v1/models
```

以及创建 Run、查询 Run、取消 Run 的最小 API 验证命令。

- [x] **Step 7: 验证**

Run:

```bash
cd epsilon-boot
uv run pytest test/infrastructure/model_access/test_provider_health_policy.py -v
```

Expected: PASS。

**已验证：**

- `cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_provider_health_policy.py -v`：5 passed。
- `cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_provider_registry_bug_condition.py test/infrastructure/model_access/test_provider_registry_preservation.py -q`：10 passed。

---

## 建议执行顺序

已完成：

1. Task 1：移除敏感默认值并建立 Secret 门禁。
2. Task 2：请求/响应日志默认安全化与字段级脱敏。
3. Task 3：Prompt Injection 与高风险工具参数防御分层。
4. Task 4：工具调用滥用检测与 OpenTelemetry 事件。
5. Task 5：CI 补齐前端、后端、安全和构建门禁。
6. Task 6：后端静态检查与 DDD import guard。
7. Task 7：评估回归接入 CI 或 Nightly。
8. Task 8：前端 API 契约运行时校验。
9. Task 9：前端主链路自动化测试（hook/unit、lint、typecheck、build、E2E 已验证，并已接入前端 CI 门禁）。

下一阶段建议执行：

1. Task 10：Provider 可靠性、SLO、告警和生产 Run 后端手册。

这个顺序先完成最直接的安全与门禁，再补测试和契约，最后完善生产运维闭环。

---

## 完成定义

完成本计划后，应满足：

- 仓库默认配置不包含明文弱口令。
- 请求/响应 body 日志默认不暴露敏感字段。
- CI 覆盖后端测试、后端静态检查、前端 lint、前端 typecheck、前端 build。
- DDD 核心导入边界有自动化守卫。
- 前端关键 API 响应有运行时 schema 校验。
- 前端至少具备 hook/unit 测试和一个 E2E smoke test。
- 评估体系进入 CI 或 nightly，不再只停留在历史报告。
- 高风险工具具备参数阻断策略和滥用检测。
- Provider 失败具备健康策略，不再只靠单次调用错误暴露。
- `docs/operations/` 中存在 SLO、Run runtime 生产配置和部署烟测手册。

---

## 最小验收命令

后端：

```bash
cd epsilon-boot
uv sync --frozen
uv run ruff check src test
uv run pytest -m "not benchmark"
```

前端：

```bash
cd epsilon-client
bun install --frozen-lockfile
bun run lint
bun run typecheck
bun run test
bun run build
```

文档/配置复核：

```bash
git diff -- docs/plan2.md docs/configuration.md docs/development.md docs/operations .github/workflows/ci.yml epsilon-boot/config.properties epsilon-client/package.json
```
