/**
 * Playwright 配置文件。
 *
 * 启动本地 Next 开发服务器后执行主链路烟测，避免为单页可见性测试引入额外部署脚本。
 */
import { defineConfig } from "@playwright/test";

const port = Number(process.env.PORT ?? 3000);
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  webServer: {
    command: `bun run dev -- --webpack --hostname 127.0.0.1 --port ${port}`,
    cwd: __dirname,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
