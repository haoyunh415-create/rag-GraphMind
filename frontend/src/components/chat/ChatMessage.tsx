"use client";

import { useState } from "react";
import { Bot, ChevronDown, FileText, Quote, User } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Citation {
  source: string;
  document_name: string;
  text: string;
  score: number;
  page?: number;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  citations?: Citation[];
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    bm25: "BM25",
    vector: "向量",
    graph: "图谱",
  };
  return labels[source] || source || "来源";
}

function InlineMarkdown({ text }: { text: string }) {
  const cleanText = text.replace(/\s*\[[0-9]+\]/g, "");
  const parts = cleanText.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);

  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
}

function MarkdownAnswer({ content }: { content: string }) {
  const blocks: JSX.Element[] = [];
  let listItems: string[] = [];

  const flushList = () => {
    if (listItems.length === 0) return;
    const items = listItems;
    listItems = [];
    blocks.push(
      <ul key={`list-${blocks.length}`} className="space-y-2 pl-4">
        {items.map((item, index) => (
          <li key={index} className="list-disc pl-1 text-[14px] leading-7">
            <InlineMarkdown text={item} />
          </li>
        ))}
      </ul>,
    );
  };

  content.split("\n").forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      return;
    }

    if (line.startsWith("### ")) {
      flushList();
      blocks.push(
        <h3
          key={`heading-${blocks.length}`}
          className="mb-2 mt-4 first:mt-0 text-[13px] font-semibold text-primary"
        >
          {line.slice(4)}
        </h3>,
      );
      return;
    }

    if (line.startsWith("- ")) {
      listItems.push(line.slice(2));
      return;
    }

    flushList();
    blocks.push(
      <p key={`paragraph-${blocks.length}`} className="text-[14px] leading-7">
        <InlineMarkdown text={line} />
      </p>,
    );
  });

  flushList();
  return <div className="space-y-3">{blocks}</div>;
}

function CitationCard({ citation }: { citation: Citation }) {
  const [expanded, setExpanded] = useState(false);
  const score = Math.max(0, Math.min(100, citation.score * 100));

  return (
    <div className="rounded-lg border border-border/70 bg-background/55 p-3 transition-colors hover:border-primary/30">
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="truncate text-xs font-medium text-foreground">
            {citation.document_name || "未知文档"}
          </span>
        </div>
        <span className="shrink-0 rounded-md border border-border bg-muted/60 px-1.5 py-0.5 text-[11px] text-muted-foreground">
          相关度 {score.toFixed(0)}%
        </span>
      </div>

      <blockquote
        className={cn(
          "border-l-2 border-primary/35 pl-3 text-xs leading-6 text-muted-foreground",
          !expanded && "line-clamp-3",
        )}
      >
        {citation.text}
      </blockquote>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground/75">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-md bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
            {sourceLabel(citation.source)}
          </span>
          {citation.page != null && <span className="font-mono">p.{citation.page}</span>}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="inline-flex items-center gap-1 rounded-md border border-border/70 px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-expanded={expanded}
        >
          {expanded ? "收起" : "展开引用"}
          <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
        </button>
      </div>
    </div>
  );
}

export function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div
      data-testid={isUser ? "message-user" : "message-assistant"}
      className={cn(
        "flex gap-3 animate-slide-up",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {!isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
          <Bot className="h-4 w-4 text-primary" />
        </div>
      )}

      <div
        className={cn(
          "min-w-0 rounded-lg text-sm",
          isUser
            ? "max-w-[72%] bg-primary px-4 py-3 text-primary-foreground"
            : "w-full max-w-4xl border border-border/80 bg-card/95 px-5 py-4 shadow-sm",
        )}
      >
        <div
          className={cn(
            message.streaming && "cursor-blink",
            !message.content && !message.streaming && "text-muted-foreground italic",
          )}
        >
          {message.content ? (
            isUser ? (
              <p className="whitespace-pre-wrap leading-6">{message.content}</p>
            ) : (
              <MarkdownAnswer content={message.content} />
            )
          ) : message.streaming ? (
            ""
          ) : (
            "..."
          )}
        </div>

        {message.citations && message.citations.length > 0 && (
          <div className="mt-4 border-t border-border/80 pt-3" data-testid="message-citations">
            <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Quote className="h-3.5 w-3.5" />
              <span className="font-medium">引用来源</span>
              <span className="font-mono text-[11px]">({message.citations.length})</span>
            </div>
            <div className="grid gap-2">
              {message.citations.map((c, i) => (
                <CitationCard key={i} citation={c} />
              ))}
            </div>
          </div>
        )}
      </div>

      {isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 ring-1 ring-accent/20">
          <User className="h-4 w-4 text-accent" />
        </div>
      )}
    </div>
  );
}
