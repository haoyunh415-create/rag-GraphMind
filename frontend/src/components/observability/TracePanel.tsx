"use client";

import { useEffect, useMemo, useState, type ElementType, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  Gauge,
  Hash,
  Layers,
  Loader2,
  Search,
  Server,
  Sparkles,
  XCircle,
} from "lucide-react";
import type { EvaluationResult } from "@/lib/api";
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

export type EvaluationState =
  | { status: "running" }
  | { status: "done"; result: EvaluationResult }
  | { status: "error"; error: string };

interface Props {
  traces: Trace[];
  latestQueryId: string | null;
  evaluations: Record<string, EvaluationState>;
}

const STEP_CONFIG: Record<string, { icon: ElementType; color: string; bg: string; label: string }> = {
  intent: { icon: CircleDot, color: "text-sky-400", bg: "bg-sky-500/25", label: "Intent" },
  backend_health: { icon: Server, color: "text-cyan-400", bg: "bg-cyan-500/25", label: "Health" },
  decompose: { icon: Search, color: "text-blue-400", bg: "bg-blue-500/25", label: "Split" },
  retrieve: { icon: Layers, color: "text-amber-400", bg: "bg-amber-500/25", label: "Search" },
  rank: { icon: Gauge, color: "text-purple-400", bg: "bg-purple-500/25", label: "Rank" },
  generate: { icon: Sparkles, color: "text-emerald-400", bg: "bg-emerald-500/25", label: "Answer" },
  chat: { icon: Sparkles, color: "text-emerald-400", bg: "bg-emerald-500/25", label: "Chat" },
  chat_empty_kb: { icon: Sparkles, color: "text-emerald-400", bg: "bg-emerald-500/25", label: "Chat" },
  blocked_empty_kb: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "Blocked" },
  blocked_no_evidence: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "Blocked" },
};

function formatMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    vector: "Vector",
    bm25: "BM25",
    graph: "Graph",
  };
  return labels[source] || source;
}

function percent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function scoreTone(value: number) {
  if (value >= 0.75) return "text-success";
  if (value >= 0.45) return "text-warning";
  return "text-destructive";
}

function TraceCard({
  trace,
  isLatest,
  evaluation,
}: {
  trace: Trace;
  isLatest: boolean;
  evaluation?: EvaluationState;
}) {
  const [expanded, setExpanded] = useState(isLatest);

  useEffect(() => {
    if (isLatest) setExpanded(true);
  }, [isLatest]);

  const totalErrors = trace.steps.reduce((n, s) => n + (s.errors?.length || 0), 0);
  const hasErrors = totalErrors > 0;
  const maxMs = Math.max(...trace.steps.map((s) => s.elapsed_ms), 1);
  const totalTokens = trace.steps.reduce((n, s) => n + (s.tokens || 0), 0);
  const overallScore = evaluation?.status === "done"
    ? (evaluation.result.faithfulness + evaluation.result.answer_relevancy + evaluation.result.context_precision + evaluation.result.context_recall) / 4
    : null;

  return (
    <div className={cn("rounded-lg border bg-card/60 transition-colors", isLatest ? "border-primary/35 ring-1 ring-primary/10" : "border-border/60")}>
      <button onClick={() => setExpanded(!expanded)} className="flex w-full cursor-pointer items-center gap-2.5 px-4 py-3 text-left text-xs" aria-expanded={expanded}>
        <span className="text-muted-foreground">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">{trace.original_query}</span>
        {evaluation?.status === "running" && (
          <span className="flex shrink-0 items-center gap-1 text-primary">
            <Loader2 className="h-3 w-3 animate-spin" />
            scoring
          </span>
        )}
        {overallScore != null && (
          <span className={cn("shrink-0 rounded-md border border-border/70 px-1.5 py-0.5 font-mono text-[11px]", scoreTone(overallScore))}>
            score {percent(overallScore)}
          </span>
        )}
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
          <EvaluationView evaluation={evaluation} />

          {trace.sub_queries.length > 0 && (
            <div className="mb-3 mt-3">
              <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Sub-queries</div>
              <div className="mt-1 space-y-1">
                {trace.sub_queries.map((sq, i) => (
                  <div key={i} className="rounded-md border border-border/40 bg-background/50 px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
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
                  <span className="text-warning">{totalErrors} issue(s)</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3 w-3 text-success" />
                  <span className="text-success">completed</span>
                </>
              )}
            </div>
            <div className="flex items-center gap-3 font-mono text-muted-foreground">
              {totalTokens > 0 && <span>{totalTokens.toLocaleString()} tokens</span>}
              <span>{trace.steps.length} steps</span>
              <span>{formatMs(trace.total_ms)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EvaluationView({ evaluation }: { evaluation?: EvaluationState }) {
  if (!evaluation) {
    return (
      <div className="mt-3 rounded-lg border border-border/60 bg-background/35 p-3 text-xs text-muted-foreground">
        Quality score will appear after the answer trace is complete.
      </div>
    );
  }

  if (evaluation.status === "running") {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs text-primary">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Evaluating answer quality...
      </div>
    );
  }

  if (evaluation.status === "error") {
    return (
      <div className="mt-3 flex items-start gap-2 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-xs text-destructive">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{evaluation.error}</span>
      </div>
    );
  }

  const result = evaluation.result;
  const metrics = [
    ["Faithfulness", result.faithfulness],
    ["Relevancy", result.answer_relevancy],
    ["Recall", result.context_recall],
    ["Precision", result.context_precision],
  ] as const;
  const overall = metrics.reduce((sum, [, value]) => sum + value, 0) / metrics.length;

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-background/35 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-medium text-foreground">
          <BarChart3 className="h-3.5 w-3.5 text-primary" />
          Answer quality
        </div>
        <div className={cn("font-mono text-[11px]", scoreTone(overall))}>
          overall {percent(overall)} · eval {formatMs(result.latency_ms)}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {metrics.map(([label, value]) => (
          <MetricBar key={label} label={label} value={value} />
        ))}
      </div>
    </div>
  );
}

function MetricBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border/50 bg-card/40 px-2 py-2">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className={cn("font-mono", scoreTone(value))}>{percent(value)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: percent(value) }} />
      </div>
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
      <button type="button" onClick={() => hasDetails && setOpen((value) => !value)} className="flex w-full items-center gap-2 text-left" aria-expanded={open}>
        <Icon className={cn("h-3.5 w-3.5 shrink-0", config.color)} />
        <span className="w-12 shrink-0 text-[11px] font-medium text-muted-foreground">{config.label}</span>
        <div className="h-5 flex-1 overflow-hidden rounded-md bg-card">
          <div className={cn("h-full rounded-md transition-all", config.bg)} style={{ width: `${Math.max(pct, 3)}%` }} />
        </div>
        <span className="w-14 shrink-0 text-right font-mono text-[11px] text-muted-foreground">{formatMs(step.elapsed_ms)}</span>
        {hasDetails && <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />}
      </button>

      <div className="ml-5 mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        {step.counts && (
          <>
            <Chip>Vector {step.counts.vector || 0}</Chip>
            <Chip>BM25 {step.counts.bm25 || 0}</Chip>
            <Chip>Graph {step.counts.graph || 0}</Chip>
          </>
        )}
        {step.input_count != null && <Chip>{`${step.input_count} in -> ${step.output_count || 0} out`}</Chip>}
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
          {step.results && step.results.length > 0 && <ResultList title="Ranked results" results={step.results} />}
          {step.errors?.map((error, index) => (
            <div key={index} className="flex items-start gap-1.5 rounded-md border border-amber-400/20 bg-amber-400/5 px-2 py-1.5 font-mono text-[10px] text-amber-300">
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
        <div key={name} className={cn("rounded-md border px-2 py-2", backend.available ? "border-success/25 bg-success/5" : "border-amber-400/25 bg-amber-400/5")}>
          <div className="text-[11px] font-medium text-foreground">{sourceLabel(name)}</div>
          <div className={cn("mt-1 text-[10px]", backend.available ? "text-success" : "text-amber-300")}>
            {backend.available ? "available" : "offline"}
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
              <ResultList key={source} title={`${sourceLabel(source)}: ${sourceDetail.count} hits`} results={sourceDetail.results || []} error={sourceDetail.error} />
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
      {!error && results.length === 0 && <div className="text-[10px] text-muted-foreground">No results</div>}
      {!error && results.length > 0 && (
        <div className="space-y-1.5">
          {results.map((result, index) => (
            <div key={`${result.id}-${index}`} className="rounded border border-border/40 bg-card/35 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-[11px] font-medium text-foreground">{result.document_name || "unknown document"}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{(result.score * 100).toFixed(1)}%</span>
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
  return <span className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-card/50 px-1.5 py-0.5">{children}</span>;
}

export function TracePanel({ traces, latestQueryId, evaluations }: Props) {
  const averageScore = useMemo(() => {
    const scores = traces
      .map((trace) => evaluations[trace.query_id])
      .filter((item): item is { status: "done"; result: EvaluationResult } => item?.status === "done")
      .map((item) => (item.result.faithfulness + item.result.answer_relevancy + item.result.context_precision + item.result.context_recall) / 4);
    if (scores.length === 0) return null;
    return scores.reduce((sum, score) => sum + score, 0) / scores.length;
  }, [evaluations, traces]);

  return (
    <div className="mx-auto max-w-4xl p-4">
      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
          <Activity className="h-4 w-4 text-primary" />
        </div>
        <h2 className="text-sm font-semibold">Trace and quality</h2>
        {traces.length > 0 && (
          <span className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {traces.length} traces
          </span>
        )}
        {averageScore != null && (
          <span className={cn("rounded-full bg-muted px-2.5 py-0.5 font-mono text-[10px]", scoreTone(averageScore))}>
            avg quality {percent(averageScore)}
          </span>
        )}
      </div>

      {traces.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-muted/30">
            <Activity className="h-7 w-7 text-muted-foreground/30" />
          </div>
          <p className="text-sm font-medium text-muted-foreground">No traces yet</p>
          <p className="mt-1.5 max-w-xs text-xs text-muted-foreground/70">
            Ask a question to see routing, retrieval, ranking, generation, and answer quality scores.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {traces.map((trace) => (
            <TraceCard key={trace.query_id} trace={trace} isLatest={trace.query_id === latestQueryId} evaluation={evaluations[trace.query_id]} />
          ))}
        </div>
      )}
    </div>
  );
}
