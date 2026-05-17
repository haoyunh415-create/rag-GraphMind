"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import {
  AlertCircle,
  Send,
  Plus,
  Sparkles,
  ChevronDown,
  Upload,
  MessageSquare,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
  Square,
  X,
} from "lucide-react";
import { ChatMessage, type Message } from "./ChatMessage";
import type { Trace } from "@/components/observability/TracePanel";
import { streamChat, uploadDocument } from "@/lib/api";
import { cn } from "@/lib/utils";

let nextId = 0;
const HISTORY_STORAGE_KEY = "rag-platform.chat-history.v1";

function newConversationId() {
  nextId += 1;
  return `conv-${Date.now()}-${nextId}`;
}

interface ConversationRecord {
  id: string;
  title: string;
  updatedAt: number;
  messages: Message[];
}

interface Props {
  onTrace?: (trace: Trace) => void;
}

type StreamStepKey = "queued" | "routing" | "decomposing" | "retrieving" | "ranking" | "generating";
type ChatMode = "auto" | "kb" | "chat";
type BackendStatuses = Record<string, { available: boolean; detail?: string }>;
type ErrorBanner = { title: string; message: string };

const STREAM_STEPS: Array<{ key: StreamStepKey; label: string }> = [
  { key: "queued", label: "准备" },
  { key: "routing", label: "路由" },
  { key: "decomposing", label: "分解" },
  { key: "retrieving", label: "检索" },
  { key: "ranking", label: "排序" },
  { key: "generating", label: "生成" },
];

const STATUS_TO_STEP: Record<string, StreamStepKey> = {
  routing: "routing",
  chat: "generating",
  decomposing: "decomposing",
  retrieving: "retrieving",
  ranking: "ranking",
  generating: "generating",
};

function formatLatency(ms: number | null) {
  if (ms == null) return "--";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
}

function StreamStatusBar({
  activeStep,
  firstTokenMs,
  statusText,
  backendStatuses,
}: {
  activeStep: StreamStepKey | "done" | null;
  firstTokenMs: number | null;
  statusText: string;
  backendStatuses: BackendStatuses | null;
}) {
  if (!activeStep && firstTokenMs == null && !statusText) return null;

  const activeIndex =
    activeStep === "done"
      ? STREAM_STEPS.length
      : STREAM_STEPS.findIndex((step) => step.key === activeStep);

  return (
    <div className="border-t border-border bg-muted/45 px-4 py-2 text-xs text-muted-foreground animate-slide-down">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          {STREAM_STEPS.map((step, index) => {
            const isDone = activeIndex > index;
            const isActive = activeIndex === index;
            return (
              <div
                key={step.key}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2 py-1 transition-colors",
                  isActive
                    ? "border-primary/45 bg-primary/10 text-primary"
                    : isDone
                      ? "border-success/35 bg-success/10 text-success"
                      : "border-border bg-background/45",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    isActive
                      ? "animate-pulse bg-primary"
                      : isDone
                        ? "bg-success"
                        : "bg-muted-foreground/35",
                  )}
                />
                <span>{step.label}</span>
              </div>
            );
          })}
          {statusText && (
            <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground/80">
              {statusText}
            </span>
          )}
          {backendStatuses && (
            <div className="flex flex-wrap items-center gap-1.5">
              {(["vector", "bm25", "graph"] as const).map((name) => {
                const backend = backendStatuses[name];
                const available = backend?.available;
                return (
                  <span
                    key={name}
                    title={backend?.detail || ""}
                    className={cn(
                      "rounded-md border px-1.5 py-0.5 font-mono text-[11px]",
                      available
                        ? "border-success/35 bg-success/10 text-success"
                        : "border-warning/35 bg-warning/10 text-warning",
                    )}
                  >
                    {name}:{available ? "on" : "off"}
                  </span>
                );
              })}
            </div>
          )}
        </div>
        <div className="shrink-0 rounded-md border border-border bg-background/55 px-2 py-1 font-mono text-[11px] text-foreground">
          首字 {formatLatency(firstTokenMs)}
        </div>
      </div>
    </div>
  );
}

function conversationTitle(messages: Message[], fallback = "新对话") {
  const firstUser = messages.find((msg) => msg.role === "user");
  if (!firstUser?.content.trim()) return fallback;
  const title = firstUser.content.trim().replace(/\s+/g, " ");
  return title.length > 28 ? `${title.slice(0, 28)}...` : title;
}

function loadConversations(): ConversationRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item?.id && Array.isArray(item.messages))
      .map((item) => ({
        id: String(item.id),
        title: String(item.title || "新对话"),
        updatedAt: Number(item.updatedAt || Date.now()),
        messages: item.messages,
      }));
  } catch {
    return [];
  }
}

function saveConversations(conversations: ConversationRecord[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    HISTORY_STORAGE_KEY,
    JSON.stringify(conversations.slice(0, 30)),
  );
}

function formatHistoryTime(timestamp: number) {
  const date = new Date(timestamp);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  if (isToday) {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

export function ChatPanel({ onTrace }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [chatMode, setChatMode] = useState<ChatMode>("auto");
  const [streaming, setStreaming] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [activeStep, setActiveStep] = useState<StreamStepKey | "done" | null>(null);
  const [firstTokenMs, setFirstTokenMs] = useState<number | null>(null);
  const [backendStatuses, setBackendStatuses] = useState<BackendStatuses | null>(null);
  const [errorBanner, setErrorBanner] = useState<ErrorBanner | null>(null);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationRecord[]>([]);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef<Message[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const uploadTasksRef = useRef<Promise<void>[]>([]);
  const streamStartedAtRef = useRef(0);
  const firstTokenSeenRef = useRef(false);

  useEffect(() => {
    const stored = loadConversations();
    setConversations(stored);
    if (stored.length > 0) {
      setConversationId(stored[0].id);
      setMessages(stored[0].messages);
      messagesRef.current = stored[0].messages;
    }
  }, []);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (shouldAutoScroll) {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [messages, shouldAutoScroll]);

  useEffect(() => {
    const inputEl = inputRef.current;
    if (!inputEl) return;
    inputEl.style.height = "0px";
    inputEl.style.height = `${Math.min(inputEl.scrollHeight, 132)}px`;
  }, [input]);

  useEffect(() => {
    return () => abortControllerRef.current?.abort();
  }, []);

  const updateConversation = useCallback((id: string, nextMessages: Message[]) => {
    if (nextMessages.length === 0) return;
    setConversations((prev) => {
      const existing = prev.find((item) => item.id === id);
      const nextRecord: ConversationRecord = {
        id,
        title: conversationTitle(nextMessages, existing?.title || "新对话"),
        updatedAt: Date.now(),
        messages: nextMessages,
      };
      const next = [nextRecord, ...prev.filter((item) => item.id !== id)];
      saveConversations(next);
      return next;
    });
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 80;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    setShouldAutoScroll(atBottom);
  }, []);

  const newChat = useCallback(() => {
    if (streaming) return;
    setMessages([]);
    messagesRef.current = [];
    setConversationId(null);
    setStatusText("");
    setActiveStep(null);
    setFirstTokenMs(null);
    setBackendStatuses(null);
    setErrorBanner(null);
    setShouldAutoScroll(true);
  }, [streaming]);

  const selectConversation = useCallback(
    (conversation: ConversationRecord) => {
      if (streaming) return;
      setConversationId(conversation.id);
      setMessages(conversation.messages);
      messagesRef.current = conversation.messages;
      setStatusText("");
      setActiveStep(null);
      setFirstTokenMs(null);
      setBackendStatuses(null);
      setErrorBanner(null);
      setShouldAutoScroll(true);
    },
    [streaming],
  );

  const deleteConversation = useCallback(
    (id: string) => {
      if (streaming) return;
      setConversations((prev) => {
        const next = prev.filter((item) => item.id !== id);
        saveConversations(next);
        if (conversationId === id) {
          setConversationId(next[0]?.id || null);
          setMessages(next[0]?.messages || []);
          messagesRef.current = next[0]?.messages || [];
          setStatusText("");
          setActiveStep(null);
          setFirstTokenMs(null);
          setBackendStatuses(null);
          setErrorBanner(null);
        }
        return next;
      });
    },
    [conversationId, streaming],
  );

  const stopStreaming = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setStreaming(false);
    setStatusText("已停止生成");
    setActiveStep("done");
    setMessages((prev) => {
      const next = prev.map((msg, index) =>
        index === prev.length - 1 && msg.role === "assistant"
          ? { ...msg, streaming: false }
          : msg,
      );
      messagesRef.current = next;
      if (conversationId) updateConversation(conversationId, next);
      return next;
    });
  }, [conversationId, updateConversation]);

  const uploadFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    setChatMode("kb");
    setUploadingCount((count) => count + files.length);
    setStatusText(`上传中：${files.length} 个文档，请稍候再提问`);

    const tasks = files.map(async (file) => {
      try {
        const result = await uploadDocument(file);
        setStatusText(`已上传：${file.name}，${result.chunk_count || 0} 个片段`);
      } catch (error) {
        setStatusText(
          `上传失败：${file.name} - ${
            error instanceof Error ? error.message : "未知错误"
          }`,
        );
      } finally {
        setUploadingCount((count) => Math.max(0, count - 1));
      }
    });

    uploadTasksRef.current = [...uploadTasksRef.current, ...tasks];
    try {
      await Promise.allSettled(tasks);
    } finally {
      uploadTasksRef.current = uploadTasksRef.current.filter((task) => !tasks.includes(task));
    }
  };

  const handleSend = async () => {
    const query = input.trim();
    if (!query || streaming) return;

    if (uploadTasksRef.current.length > 0) {
      setStatusText("正在等待文档入库完成");
      await Promise.allSettled(uploadTasksRef.current);
    }

    const convId = conversationId || newConversationId();
    if (!conversationId) setConversationId(convId);

    setInput("");
    setShouldAutoScroll(true);
    setStreaming(true);
    setStatusText("queued: Preparing request");
    setActiveStep("queued");
    setFirstTokenMs(null);
    setBackendStatuses(null);
    setErrorBanner(null);
    firstTokenSeenRef.current = false;
    streamStartedAtRef.current = performance.now();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const assistantIdx = messagesRef.current.length + 1;
    const initialMessages: Message[] = [
      ...messagesRef.current,
      { role: "user", content: query },
      { role: "assistant", content: "", streaming: true },
    ];
    messagesRef.current = initialMessages;
    setMessages(initialMessages);
    updateConversation(convId, initialMessages);

    await streamChat(
      query,
      convId,
      chatMode,
      controller.signal,
      (text) => {
        if (!firstTokenSeenRef.current && text) {
          firstTokenSeenRef.current = true;
          setFirstTokenMs(performance.now() - streamStartedAtRef.current);
          setActiveStep("generating");
        }
        setMessages((prev) => {
          const next = [...prev];
          next[assistantIdx] = {
            ...next[assistantIdx],
            content: next[assistantIdx].content + text,
          };
          messagesRef.current = next;
          updateConversation(convId, next);
          return next;
        });
      },
      (citations) => {
        setMessages((prev) => {
          const next = [...prev];
          next[assistantIdx] = { ...next[assistantIdx], citations };
          messagesRef.current = next;
          updateConversation(convId, next);
          return next;
        });
      },
      (status: string, detail: string, data?: any) => {
        setActiveStep(STATUS_TO_STEP[status] || "queued");
        setStatusText(`${status}: ${detail}`);
        if (data?.backends) {
          setBackendStatuses(data.backends);
        }
      },
      (trace: Trace) => {
        onTrace?.(trace);
      },
      () => {
        const wasAborted = controller.signal.aborted;
        setMessages((prev) => {
          const next = [...prev];
          next[assistantIdx] = { ...next[assistantIdx], streaming: false };
          messagesRef.current = next;
          updateConversation(convId, next);
          return next;
        });
        setStreaming(false);
        abortControllerRef.current = null;
        setActiveStep("done");
        setStatusText(wasAborted ? "已停止生成" : "");
      },
      (err) => {
        const message = err.message || "未知错误";
        setErrorBanner({ title: "请求失败", message });
        setMessages((prev) => {
          const next = [...prev];
          next[assistantIdx] = {
            ...next[assistantIdx],
            content: `请求失败：${message}`,
            streaming: false,
          };
          messagesRef.current = next;
          updateConversation(convId, next);
          return next;
        });
        setStreaming(false);
        abortControllerRef.current = null;
        setActiveStep(null);
        setStatusText("请求失败");
      },
    );
  };

  return (
    <div
      className="relative flex h-full"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          e.stopPropagation();
          setDragOver(true);
        }
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.currentTarget === e.target) setDragOver(false);
      }}
      onDrop={async (e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
        await uploadFiles(e.dataTransfer.files);
      }}
    >
      <aside
        className={cn(
          "hidden shrink-0 border-r border-border/80 bg-card/45 md:flex md:flex-col",
          historyOpen ? "w-64" : "w-12",
        )}
      >
        <div className="flex h-14 items-center justify-between border-b border-border/70 px-3">
          {historyOpen && (
            <span className="text-xs font-semibold text-foreground">历史记录</span>
          )}
          <button
            type="button"
            onClick={() => setHistoryOpen((open) => !open)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
            aria-label={historyOpen ? "收起历史记录" : "展开历史记录"}
          >
            {historyOpen ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <PanelLeftOpen className="h-4 w-4" />
            )}
          </button>
        </div>

        <div className="p-2">
          <button
            type="button"
            onClick={newChat}
            disabled={streaming}
            className={cn(
              "flex h-9 w-full items-center justify-center gap-2 rounded-md border border-border bg-background/60 text-xs font-medium transition-colors",
              "hover:border-primary/40 hover:bg-muted/60 disabled:cursor-not-allowed disabled:opacity-60",
            )}
          >
            <Plus className="h-4 w-4" />
            {historyOpen && <span>新对话</span>}
          </button>
        </div>

        {historyOpen && (
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {conversations.length === 0 ? (
              <div className="px-2 py-8 text-center text-xs leading-5 text-muted-foreground">
                还没有历史会话
              </div>
            ) : (
              <div className="space-y-1">
                {conversations.map((conversation) => {
                  const isActive = conversation.id === conversationId;
                  return (
                    <div
                      key={conversation.id}
                      className={cn(
                        "group flex items-center gap-2 rounded-md px-2 py-2 transition-colors",
                        isActive
                          ? "bg-primary/12 text-primary"
                          : "text-muted-foreground hover:bg-muted/55 hover:text-foreground",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => selectConversation(conversation)}
                        disabled={streaming}
                        className="min-w-0 flex flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                      >
                        <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-xs font-medium">
                          {conversation.title}
                        </span>
                        <span className="shrink-0 text-[10px] text-muted-foreground/70">
                          {formatHistoryTime(conversation.updatedAt)}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteConversation(conversation.id)}
                        disabled={streaming}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground/60 opacity-0 transition-all hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100 disabled:cursor-not-allowed"
                        aria-label="删除历史会话"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {dragOver && (
          <div className="pointer-events-none absolute inset-4 z-20 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-background/82 backdrop-blur-sm">
            <div className="flex flex-col items-center text-center">
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-primary/15">
                <Upload className="h-6 w-6 text-primary" />
              </div>
              <p className="text-sm font-medium text-foreground">松开即可上传文档</p>
              <p className="mt-1 text-xs text-muted-foreground">
                支持 PDF、Word、Markdown、HTML、TXT、CSV
              </p>
            </div>
          </div>
        )}

        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-4 py-6"
        >
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
            {messages.length === 0 ? (
              <div className="flex min-h-[55vh] flex-col items-center justify-center px-4 text-center animate-fade-in">
                <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-lg bg-primary/12 ring-1 ring-primary/25">
                  <Sparkles className="h-7 w-7 text-primary" />
                </div>
                <p className="text-lg font-semibold text-foreground">
                  开始一次知识库问答
                </p>
                <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                  可以直接提问，也可以把文档拖到这里上传后进行知识库问答。
                </p>
              </div>
            ) : (
              <div className="flex items-center justify-between pb-1">
                <span className="text-xs text-muted-foreground">
                  {messages.length} 条消息
                </span>
                <button
                  onClick={newChat}
                  disabled={streaming}
                  className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Plus className="h-3.5 w-3.5" />
                  新对话
                </button>
              </div>
            )}

            {messages.map((msg, i) => (
              <ChatMessage key={i} message={msg} />
            ))}
          </div>
        </div>

        {!shouldAutoScroll && messages.length > 0 && (
          <button
            onClick={() => {
              setShouldAutoScroll(true);
              scrollRef.current?.scrollTo({
                top: scrollRef.current.scrollHeight,
                behavior: "smooth",
              });
            }}
            className="absolute bottom-36 left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-card p-1.5 shadow-lg transition-colors hover:bg-muted"
            aria-label="滚动到底部"
          >
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          </button>
        )}

        <StreamStatusBar
          activeStep={activeStep}
          firstTokenMs={firstTokenMs}
          statusText={statusText}
          backendStatuses={backendStatuses}
        />

        <div className="border-t border-border bg-background/92 p-4 backdrop-blur-sm">
          <div className="mx-auto max-w-5xl">
            {errorBanner && (
              <div className="mb-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/8 px-3 py-2.5 text-sm text-destructive">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{errorBanner.title}</div>
                  <div className="mt-1 break-words text-xs leading-5 text-destructive/90">
                    {errorBanner.message}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setErrorBanner(null)}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-destructive/80 transition-colors hover:bg-destructive/10 hover:text-destructive"
                  aria-label="关闭错误提示"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            <div className="flex items-end gap-2 rounded-lg border border-border bg-card/80 p-2 shadow-sm transition-colors focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/15">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.markdown,.html,.htm,.csv"
                onChange={(e) => {
                  if (!e.target.files) return;
                  void uploadFiles(e.target.files);
                  e.target.value = "";
                }}
                className="hidden"
                aria-label="上传知识库文档"
              />
              <div className="flex shrink-0 flex-col gap-1 sm:flex-row">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={streaming || uploadingCount > 0}
                  title={uploadingCount > 0 ? "文档上传中" : "上传文档"}
                  aria-label="上传文档"
                  className={cn(
                    "flex h-8 w-8 items-center justify-center rounded-md border text-muted-foreground transition-colors",
                    "border-border bg-background/45 hover:bg-muted/60 hover:text-foreground",
                    "disabled:cursor-not-allowed disabled:opacity-60",
                  )}
                >
                  {uploadingCount > 0 ? (
                    <Upload className="h-3.5 w-3.5 animate-pulse" />
                  ) : (
                    <Upload className="h-3.5 w-3.5" />
                  )}
                </button>
                {([
                  ["auto", "自动"],
                  ["kb", "知识库"],
                  ["chat", "聊天"],
                ] as Array<[ChatMode, string]>).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setChatMode(mode)}
                    disabled={streaming}
                    className={cn(
                      "h-8 rounded-md border px-2 text-[11px] font-medium transition-colors",
                      chatMode === mode
                        ? "border-primary/45 bg-primary/12 text-primary"
                        : "border-border bg-background/45 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      "disabled:cursor-not-allowed disabled:opacity-60",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="输入问题，按 Enter 发送..."
                aria-label="输入问题"
                rows={1}
                className={cn(
                  "max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6",
                  "placeholder:text-muted-foreground/60",
                  "focus:outline-none",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                )}
                disabled={streaming || uploadingCount > 0}
              />
              <button
                onClick={streaming ? stopStreaming : handleSend}
                disabled={!streaming && (!input.trim() || uploadingCount > 0)}
                title={streaming ? "停止生成" : "发送"}
                aria-label="发送"
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-md transition-colors",
                  "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none",
                  streaming
                    ? "bg-destructive text-primary-foreground hover:bg-destructive/90"
                    : input.trim()
                      ? "bg-primary text-primary-foreground hover:bg-primary/90"
                      : "bg-muted text-muted-foreground",
                  "disabled:cursor-not-allowed",
                )}
              >
                {streaming ? (
                  <Square className="h-4 w-4 fill-current" />
                ) : (
                  <Send className="h-5 w-5" />
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
