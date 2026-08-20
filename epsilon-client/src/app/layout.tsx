/**
 * 根布局组件。
 *
 * 定义全局 HTML 结构、字体变量和元数据。
 * 字体族由全局 CSS 使用系统字体声明，避免构建期依赖外部字体下载。
 */

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Console - epsilon",
  description: "面向聊天与任务执行的 AI Agent 控制台",
};

/**
 * 应用根布局，包裹所有页面。
 *
 * @param children - 子页面内容
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="h-full">{children}</body>
    </html>
  );
}
