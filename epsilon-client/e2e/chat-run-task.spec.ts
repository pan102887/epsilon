/**
 * 前端主链路 Playwright 烟测。
 *
 * 验证首页壳层能够正常渲染关键标题，作为最小可用性回归保护。
 */
import { expect, test, type Page } from "@playwright/test";

function runSnapshot(runId: string, kind: "chat" | "task", status = "running") {
  return {
    code: 0,
    run_id: runId,
    kind,
    status,
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
    created_at: "2026-06-18T00:00:00Z",
    updated_at: "2026-06-18T00:00:01Z",
    version: 1,
  };
}

function taskResponse(content: string, canContinue: boolean) {
  return {
    code: 0,
    content,
    status: canContinue ? "paused" : "success",
    model: "mock-model",
    usage: { prompt_tokens: 3, completion_tokens: 5 },
    trace: [{ step: 1, action: "mock", detail: "mock trace", timestamp_ms: 1_765_497_600_000 }],
    latency_ms: 120,
    prompt_id: "prompt-1",
    terminated_reason: canContinue ? "max_rounds" : "completed",
    can_continue: canContinue,
    segment_index: canContinue ? 1 : 2,
    segment_count: canContinue ? 2 : 2,
    auto_continue_attempted: false,
    segment_stop_reason: canContinue ? "max_continuations_reached" : "completed",
    budget_usage: {
      segment_count: canContinue ? 1 : 2,
      continuation_count: canContinue ? 0 : 1,
      total_tokens: 8,
      elapsed_ms: 120,
      consecutive_paused_count: 0,
      no_progress_count: 0,
      repeated_tool_call_count: 0,
    },
  };
}

async function mockBackend(page: Page) {
  await page.route("**/v1/models", async (route) => {
    await route.fulfill({ json: { data: [] } });
  });

  await page.route("**/api/chat", async (route) => {
    const body = route.request().postDataJSON() as { message?: string };
    if (body.message === "please keep running") {
      await new Promise((resolve) => setTimeout(resolve, 2_000));
      await route.fulfill({
        contentType: "text/event-stream",
        body: 'data: {"delta_content":"late reply","finished":false}\n\n',
      });
      return;
    }

    await route.fulfill({
      contentType: "text/event-stream",
      body: [
        'data: {"delta_content":"streamed assistant reply","finished":false}\n\n',
        'data: {"delta_content":"","finished":true,"status":"completed","can_continue":false}\n\n',
        "data: [DONE]\n\n",
      ].join(""),
    });
  });

  await page.route("**/api/task/execute", async (route) => {
    await route.fulfill({ json: taskResponse("需要继续的任务结果", true) });
  });

  await page.route("**/api/task/sessions/*/continue", async (route) => {
    await route.fulfill({ json: taskResponse("继续后的任务结果", false) });
  });

  await page.route("**/api/runs", async (route) => {
    const body = route.request().postDataJSON() as { kind: "chat" | "task" };
    const runId = body.kind === "chat" ? "run-chat-1" : "run-task-1";
    await route.fulfill({ json: runSnapshot(runId, body.kind) });
  });

  await page.route("**/api/runs/*/events?**", async (route) => {
    const url = new URL(route.request().url());
    const runId = url.pathname.split("/").at(-2) ?? "run-chat-1";
    await route.fulfill({
      json: {
        code: 0,
        events: [
          {
            run_id: runId,
            cursor: 1,
            event_type: "run_created",
            payload: { status: "running" },
            created_at: "2026-06-18T00:00:01Z",
          },
        ],
        latest_cursor: 1,
      },
    });
  });

  await page.route("**/api/runs/*", async (route) => {
    const url = new URL(route.request().url());
    const runId = url.pathname.split("/").at(-1) ?? "run-chat-1";
    await route.fulfill({ json: runSnapshot(runId, runId.includes("task") ? "task" : "chat") });
  });

  await page.route("**/api/runs/*/events/stream**", async (route) => {
    const url = new URL(route.request().url());
    const runId = url.pathname.split("/").at(-3) ?? "run-chat-1";
    await route.fulfill({
      contentType: "text/event-stream",
      body: [
        "event: message\n",
        `data: ${JSON.stringify({
          run_id: runId,
          cursor: 2,
          event_type: "run_heartbeat",
          payload: { status: "running" },
          created_at: "2026-06-18T00:00:02Z",
        })}\n\n`,
      ].join(""),
    });
  });
}

test("renders the agent console shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("Epsilon")).toBeVisible();
  await expect(
    page.getByText(
      "Agent console for chat, task runs, and execution visibility.",
    ),
  ).toBeVisible();
});

test("covers chat stream, task continuation, run events, and abort controls", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");

  await page.getByLabel("聊天消息输入框").fill("hello agent");
  await page.getByLabel("发送消息").click();

  await expect(page.getByText("hello agent")).toBeVisible();
  await expect(page.getByText("streamed assistant reply")).toBeVisible();

  await page.getByLabel("聊天消息输入框").fill("please keep running");
  await page.getByLabel("发送消息").click();
  await expect(page.getByLabel("停止生成")).toBeVisible();
  await page.getByLabel("停止生成").click();

  await page.getByLabel("聊天消息输入框").fill("create background chat run");
  await page.getByLabel("后台运行聊天").click();
  await expect(page.getByText("Run_ID")).toBeVisible();
  await expect(page.getByText("run-chat-1")).toBeVisible();
  await expect(page.getByText("Run_Event log")).toBeVisible();
  await expect(page.getByText("Heartbeat")).toBeVisible();

  await page.getByLabel("Task goal").fill("summarize the context");
  await page.getByRole("button", { name: "运行任务" }).click();
  await expect(page.getByText("需要继续的任务结果")).toBeVisible();
  await expect(page.getByRole("button", { name: "继续任务" })).toBeVisible();
  await page.getByRole("button", { name: "继续任务" }).click();
  await expect(page.getByText("继续后的任务结果")).toBeVisible();

  await page.getByLabel("Task goal").fill("run as background task");
  await page.getByRole("button", { name: "后台运行", exact: true }).click();
  await expect(page.getByText("run-task-1")).toBeVisible();
});
