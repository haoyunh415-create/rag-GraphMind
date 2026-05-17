export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8001`
    : "http://localhost:8001");

function isAbortError(err: unknown) {
  return (
    typeof DOMException !== "undefined" &&
    err instanceof DOMException &&
    err.name === "AbortError"
  );
}

function connectError(err: unknown) {
  const detail = err instanceof Error ? err.message : "unknown error";
  return new Error(
    `无法连接后端 API（${API_BASE}）。请先运行 .\\scripts\\start-dev.ps1，确认 8001 端口可访问。原始错误：${detail}`,
  );
}

async function readJson(response: Response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 300) };
  }
}

function responseError(response: Response, data: any, fallback: string) {
  const detail = data?.detail || data?.message || fallback;
  return new Error(`${detail}（HTTP ${response.status}，API：${API_BASE}）`);
}

export async function streamChat(
  query: string,
  conversationId: string | null,
  mode: "auto" | "kb" | "chat",
  signal: AbortSignal | undefined,
  onChunk: (text: string) => void,
  onCitation: (citations: any[]) => void,
  onStatus: (status: string, detail: string, data?: any) => void,
  onTrace: (trace: any) => void,
  onDone: () => void,
  onError: (err: Error) => void,
) {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, conversation_id: conversationId, mode }),
      signal,
    });
  } catch (err) {
    if (isAbortError(err)) {
      onDone();
      return;
    }
    onError(connectError(err));
    return;
  }

  if (!response.ok || !response.body) {
    const data = await readJson(response);
    onError(responseError(response, data, "对话请求失败"));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let result: ReadableStreamReadResult<Uint8Array>;
    try {
      result = await reader.read();
    } catch (err) {
      if (isAbortError(err)) {
        onDone();
        return;
      }
      onError(err instanceof Error ? err : new Error("流式响应读取失败"));
      return;
    }
    const { done, value } = result;
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        switch (data.type) {
          case "chunk":
            onChunk(data.content || data.data || "");
            break;
          case "citation":
          case "citations":
            onCitation(data.data || []);
            break;
          case "status":
            onStatus(data.data?.status || "", data.data?.detail || "", data.data);
            break;
          case "trace":
            onTrace(data.data);
            break;
        }
      } catch {
        // ignore parse errors on partial lines
      }
    }
  }
  onDone();
}

export async function uploadDocument(file: File) {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/documents/upload`, {
      method: "POST",
      body: form,
    });
  } catch (err) {
    throw connectError(err);
  }
  const data = await readJson(res);
  if (!res.ok || data.status === "error") {
    throw responseError(res, data, `上传失败：${file.name}`);
  }
  return data;
}

export interface KnowledgeDocument {
  document_id: string;
  document_name: string;
  chunk_count: number;
  status: string;
  index_statuses: Record<string, string>;
  errors: string[];
}

export interface KnowledgeChunk {
  chunk_id: string;
  document_id: string;
  document_name: string;
  chunk_index: number;
  text: string;
}

export async function fetchKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/kb/documents`, {
      cache: "no-store",
    });
  } catch (err) {
    throw connectError(err);
  }
  const data = await readJson(res);
  if (!res.ok) {
    throw responseError(res, data, "读取知识库文档失败");
  }
  return data.documents || [];
}

export async function fetchDocumentChunks(documentId: string): Promise<KnowledgeChunk[]> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/documents/${documentId}/chunks`, {
      cache: "no-store",
    });
  } catch (err) {
    throw connectError(err);
  }
  const data = await readJson(res);
  if (!res.ok) {
    throw responseError(res, data, "读取文档片段失败");
  }
  return data.chunks || [];
}

export async function deleteDocument(documentId: string) {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/documents/${documentId}`, {
      method: "DELETE",
    });
  } catch (err) {
    throw connectError(err);
  }
  const data = await readJson(res);
  if (!res.ok || data.status === "error") {
    throw responseError(res, data, "删除文档失败");
  }
  return data;
}

export async function fetchApiHealth() {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
  } catch (err) {
    throw connectError(err);
  }
  const data = await readJson(res);
  if (!res.ok) {
    throw responseError(res, data, "后端健康检查失败");
  }
  return data;
}
