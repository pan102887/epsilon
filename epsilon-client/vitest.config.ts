/**
 * Vitest 配置文件。
 *
 * 为前端 Hook 测试启用 jsdom 运行时，并复用项目的 @ 路径别名。
 */
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "**/node_modules/**", "**/dist/**", "**/.next/**"],
    globals: true,
  },
});
