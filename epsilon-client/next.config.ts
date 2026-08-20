/**
 * Next.js 配置文件。
 *
 * 主要配置：
 * - reactCompiler: 启用 React 编译器优化
 * - rewrites: 将 /api/* 请求代理到 FastAPI 后端（开发环境解决跨域）
 */
import path from "node:path";
import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:7777";

const nextConfig: NextConfig = {
  reactCompiler: true,
  turbopack: {
    root: path.resolve(__dirname),
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: "/v1/:path*",
        destination: `${BACKEND_URL}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
