import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG 知识库助手",
  description: "用于文档检索、知识图谱增强问答和引用追踪的本地 RAG 工作台。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
