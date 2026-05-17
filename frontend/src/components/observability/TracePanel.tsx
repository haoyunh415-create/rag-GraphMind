"use client";

import { useEffect, useState, type ElementType, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  Gauge,
  Hash,
  Layers,
  Search,
  Server,
  Sparkles,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface TraceResult {
  id: string;
  document_id: string;
  document_name: string;
  chunk_index: number;
  source: string;
  score: number;
  text: string;
}

interface RetrievalSourceDetail {
  count: number;
  error?: string;
  results: TraceResult[];
}

interface RetrievalDetail {
  query: string;
  sources: Record<string, RetrievalSourceDetail>;
}

export interface TraceStep {
  name: string;
  elapsed_ms: number;
  sub_queries?: string[];
  counts?: Record<string, number>;
  errors?: string[];
  input_count?: number;
  output_count?: number;
  tokens?: number;
  reason?: string;
  backends?: Record<string, { available: boolean; detail: string }>;
  details?: RetrievalDetail[];
  results?: TraceResult[];
}

export interface Trace {
  query_id: string;
  original_query: string;
  sub_queries: string[];
  steps: TraceStep[];
  total_ms: number;
}

interface Props {
  traces: Trace[];
  latestQueryId: string | null;
}

const STEP_CONFIG: Record<
  string,
  { icon: ElementType; color: string; bg: string; label: string }
> = {
  intent: { icon: CircleDot, color: "text-sky-400", bg: "bg-sky-500/25", label: "意图" },
  backend_health: { icon: Server, color: "text-cyan-400", bg: "bg-cyan-500/25", label: "后端" },
  decompose: { icon: Search, color: "text-blue-400", bg: "bg-blue-500/25", label: "分解" },
  retrieve: { icon: Layers, color: "text-amber-400", bg: "bg-amber-500/25", label: "检索" },
  rank: { icon: Gauge, color: "text-purple-400", bg: "bg-purple-500/25", label: "排序" },
  generate: { icon: Sparkles, color: "text-emerald-400", bg: "bg-emerald-500/25", label: "生成" },
  blocked_empty_kb: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "阻止" },
  blocked_no_evidence: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "阻止" },
};

function formatMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    vector: "向量",
    bm25: "BM25",
    graph: "图谱",
  };
  return labels[source] || source;
}

function TraceCard({ trace, isLatest }: { trace: Trace; isLatest: boolean }) {
  const [expanded, setExpanded] = useState(isLatest);

  useEffect(() => {
    if (isLatest) setExpanded(true);
  }, [isLatest]);

  const totalErrors = trace.steps.reduce((n, s) => n + (s.errors?.length || 0), 0);
  const hasErrors = totalErrors > 0;
  const maxMs = Math.max(...trace.steps.map((s) => s.elapsed_ms), 1);
  const totalTokens = trace.steps.reduce((n, s) => n + (s.tokens || 0), 0);

  return (
    <div
      data-testid="trace-card"
      className={cn(
        "rounded-lg border bg-card/60 transition-colors",
        isLatest ? "border-primary/35 ring-1 ring-primary/10" : "border-border/60",
      )}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full cursor-pointer items-center gap-2.5 px-4 py-3 text-left text-xs"
        aria-expanded={expanded}
      >
        <span className="text-muted-foreground">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">
          {trace.original_query}
        </span>
        {hasErrors && (
          <span className="flex shrink-0 items-center gap-1 text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            {totalErrors}
          </span>
        )}
        <span className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          {formatMs(trace.total_ms)}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border/50 px-4 pb-4">
          {trace.sub_queries.length > 0 && (
            <div className="mb-3 mt-3">
              <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                子查询
              </div>
              <div className="mt-1 space-y-1">
                {trace.sub_queries.map((sq, i) => (
                  <div
                    key={i}
                    className="rounded-md border border-border/40 bg-background/50 px-3 py-1.5 font-mono text-[11px] text-muted-foreground"
                  >
                    {sq}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-3 space-y-3">
            {trace.steps.map((step, i) => (
              <TraceStepView key={`${step.name}-${i}`} step={step} maxMs={maxMs} />
            ))}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-border/50 pt-3 text-[10px]">
            <div className="flex items-center gap-2">
              {hasErrors ? (
                <>
                  <AlertTriangle className="h-3 w-3 text-warning" />
                  <span className="text-warning">{totalErrors} 个警告</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3 w-3 text-success" />
                  <span className="text-success">流程正常</span>
                </>
              )}
            </div>
            <div className="flex items-center gap-3 font-mono text-muted-foreground">
              {totalTokens > 0 && <span>{totalTokens.toLocaleString()} tokens</span>}
              <span>{trace.steps.length} 步</span>
              <span>{formatMs(trace.total_ms)} 总计</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceStepView({ step, maxMs }: { step: TraceStep; maxMs: number }) {
  const [open, setOpen] = useState(step.name === "retrieve" || step.name === "rank");
  const config = STEP_CONFIG[step.name] || {
    icon: CircleDot,
    color: "text-muted-foreground",
    bg: "bg-muted-foreground/20",
    label: step.name,
  };
  const Icon = config.icon;
  const pct = (step.elapsed_ms / maxMs) * 100;
  const hasDetails = Boolean(step.backends || step.details?.length || step.results?.length || step.errors?.length);

  return (
    <div className="rounded-lg border border-border/50 bg-background/35 p-3 text-xs">
      <button
        type="button"
        onClick={() => hasDetails && setOpen((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
        aria-expanded={open}
      >
        <Icon className={cn("h-3.5 w-3.5 shrink-0", config.color)} />
        <span className="w-12 shrink-0 text-[11px] font-medium text-muted-foreground">
          {config.label}
        </span>
        <div className="h-5 flex-1 overflow-hidden rounded-md bg-card">
          <div
            className={cn("h-full rounded-md transition-all", config.bg)}
            style={{ width: `${Math.max(pct, 3)}%` }}
          />
        </div>
        <span className="w-14 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
          {formatMs(step.elapsed_ms)}
        </span>
        {hasDetails && (
          <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
        )}
      </button>

      <div className="ml-5 mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        {step.counts && (
          <>
            <Chip>向量 {step.counts.vector || 0}</Chip>
            <Chip>BM25 {step.counts.bm25 || 0}</Chip>
            <Chip>图谱 {step.counts.graph || 0}</Chip>
          </>
        )}
        {step.input_count != null && (
          <Chip>{step.input_count} 输入 → {step.output_count || 0} 结果</Chip>
        )}
        {step.tokens != null && (
          <Chip>
            <Hash className="h-3 w-3" />
            {step.tokens} tokens
          </Chip>
        )}
        {step.reason && <Chip>{step.reason}</Chip>}
      </div>

      {open && hasDetails && (
        <div className="ml-5 mt-3 space-y-3">
          {step.backends && <BackendHealthView backends={step.backends} />}
          {step.details && <RetrievalDetailsView details={step.details} />}
          {step.results && step.results.length > 0 && (
            <ResultList title="融合排序结果" results={step.results} />
          )}
          {step.errors?.map((error, index) => (
            <div
              key={index}
              className="flex items-start gap-1.5 rounded-md border border-amber-400/20 bg-amber-400/5 px-2 py-1.5 font-mono text-[10px] text-amber-300"
            >
              <XCircle className="mt-0.5 h-3 w-3 shrink-0" />
              {error}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BackendHealthView({ backends }: { backends: TraceStep["backends"] }) {
  if (!backends) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {Object.entries(backends).map(([name, backend]) => (
        <div
          key={name}
          className={cn(
            "rounded-md border px-2 py-2",
            backend.available
              ? "border-success/25 bg-success/5"
              : "border-amber-400/25 bg-amber-400/5",
          )}
        >
          <div className="text-[11px] font-medium text-foreground">{sourceLabel(name)}</div>
          <div className={cn("mt-1 text-[10px]", backend.available ? "text-success" : "text-amber-300")}>
            {backend.available ? "可用" : "不可用"}
          </div>
          <div className="mt-1 text-[10px] leading-4 text-muted-foreground">{backend.detail}</div>
        </div>
      ))}
    </div>
  );
}

function RetrievalDetailsView({ details }: { details: RetrievalDetail[] }) {
  return (
    <div className="space-y-3">
      {details.map((detail, index) => (
        <div key={`${detail.query}-${index}`} className="rounded-md border border-border/50 bg-card/35 p-2">
          <div className="mb-2 font-mono text-[11px] text-muted-foreground">{detail.query}</div>
          <div className="space-y-2">
            {Object.entries(detail.sources).map(([source, sourceDetail]) => (
              <ResultList
                key={source}
                title={`${sourceLabel(source)}：${sourceDetail.count} 条`}
                results={sourceDetail.results || []}
                error={sourceDetail.error}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ResultList({ title, results, error }: { title: string; results: TraceResult[]; error?: string }) {
  return (
    <div className="rounded-md border border-border/50 bg-background/35 p-2">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-foreground">{title}</span>
      </div>
      {error && <div className="text-[10px] text-destructive">{error}</div>}
      {!error && results.length === 0 && (
        <div className="text-[10px] text-muted-foreground">没有命中结果</div>
      )}
      {!error && results.length > 0 && (
        <div className="space-y-1.5">
          {results.map((result, index) => (
            <div key={`${result.id}-${index}`} className="rounded border border-border/40 bg-card/35 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-[11px] font-medium text-foreground">
                  {result.document_name || "未知文档"}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                  {(result.score * 100).toFixed(1)}%
                </span>
              </div>
              <div className="text-[10px] leading-4 text-muted-foreground">{result.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-card/50 px-1.5 py-0.5">
      {children}
    </span>
  );
}

export function TracePanel({ traces, latestQueryId }: Props) {
  return (
    <div className="mx-auto max-w-4xl p-4" data-testid="trace-panel">
      <div className="mb-4 flex items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
          <Activity className="h-4 w-4 text-primary" />
        </div>
        <h2 className="text-sm font-semibold">检索调试</h2>
        {traces.length > 0 && (
          <span className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {traces.length} 条
          </span>
        )}
      </div>

      {traces.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-muted/30">
            <Activity className="h-7 w-7 text-muted-foreground/30" />
          </div>
          <p className="text-sm font-medium text-muted-foreground">暂无检索追踪</p>
          <p className="mt-1.5 max-w-xs text-xs text-muted-foreground/70">
            在对话标签中发送问题后，可以查看后端健康、原始检索结果、融合排序和生成耗时。
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {traces.map((trace) => (
            <TraceCard
              key={trace.query_id}
              trace={trace}
              isLatest={trace.query_id === latestQueryId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
