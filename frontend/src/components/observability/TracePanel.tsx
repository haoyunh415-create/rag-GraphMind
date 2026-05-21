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
  GitBranch,
  Hash,
  Layers,
  Quote,
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
  citation_selected?: boolean;
  citation_reason?: string;
  citation_rejection_reason?: string;
  citation_relative_score?: number;
  citation_query_coverage?: number;
  citation_sources?: string[];
  rrf_score?: number;
  rerank_score?: number;
  rerank_features?: Record<string, number>;
  graph_context?: {
    entity?: string;
    entity_type?: string;
    matched_entity?: string;
    matched_entity_type?: string;
    neighbours?: string[];
    path_entities?: string[];
    path_relations?: string[];
  };
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

interface PerformanceWarning {
  code: string;
  step: string;
  severity: "warn" | "slow";
  duration_ms: number;
  budget_ms: number;
  over_by_ms: number;
}

export interface TraceStep {
  name: string;
  started_ms?: number;
  elapsed_ms: number;
  duration_ms?: number;
  sub_queries?: string[];
  counts?: Record<string, number>;
  filtered_counts?: Record<string, number>;
  errors?: string[];
  input_count?: number;
  output_count?: number;
  tokens?: number;
  reason?: string;
  backends?: Record<string, { available: boolean; detail: string }>;
  details?: RetrievalDetail[];
  results?: TraceResult[];
  candidates?: TraceResult[];
  rejection_counts?: Record<string, number>;
  settings?: Record<string, number | string>;
  reranker?: {
    enabled: boolean;
    weights: Record<string, number>;
  };
  overall_score?: number;
  label?: "pass" | "warn" | "fail";
  groundedness?: number;
  answer_relevance?: number;
  citation_coverage?: number;
  retrieval_quality?: number;
  issues?: string[];
  performance_warnings?: PerformanceWarning[];
}

export interface Trace {
  query_id: string;
  original_query: string;
  sub_queries: string[];
  steps: TraceStep[];
  total_ms: number;
  timings?: {
    accounted_ms: number;
    untracked_ms: number;
    slowest_step?: {
      name: string;
      duration_ms: number;
    } | null;
    performance_warnings?: PerformanceWarning[];
    performance_warning_count?: number;
  };
}

interface Props {
  traces: Trace[];
  latestQueryId: string | null;
}

const STEP_CONFIG: Record<
  string,
  { icon: ElementType; color: string; bg: string; label: string }
> = {
  evaluate: { icon: Gauge, color: "text-lime-400", bg: "bg-lime-500/25", label: "质量" },
  intent: { icon: CircleDot, color: "text-sky-400", bg: "bg-sky-500/25", label: "意图" },
  document_filter: { icon: CheckCircle2, color: "text-teal-400", bg: "bg-teal-500/25", label: "文档" },
  backend_health: { icon: Server, color: "text-cyan-400", bg: "bg-cyan-500/25", label: "后端" },
  decompose: { icon: Search, color: "text-blue-400", bg: "bg-blue-500/25", label: "分解" },
  retrieve: { icon: Layers, color: "text-amber-400", bg: "bg-amber-500/25", label: "检索" },
  rank: { icon: Gauge, color: "text-purple-400", bg: "bg-purple-500/25", label: "排序" },
  cite: { icon: Quote, color: "text-rose-400", bg: "bg-rose-500/25", label: "引用" },
  generate: { icon: Sparkles, color: "text-emerald-400", bg: "bg-emerald-500/25", label: "生成" },
  blocked_empty_kb: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "阻止" },
  blocked_no_evidence: { icon: AlertTriangle, color: "text-amber-400", bg: "bg-amber-500/25", label: "阻止" },
};

function formatMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function stepDuration(step: TraceStep) {
  return step.duration_ms ?? step.elapsed_ms;
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    vector: "向量",
    bm25: "BM25",
    graph: "图谱",
  };
  return labels[source] || source;
}

function formatPercent(value: number | undefined) {
  if (value == null || Number.isNaN(value)) return "--";
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function qualityTone(label?: "pass" | "warn" | "fail") {
  if (label === "pass") return "border-success/30 bg-success/10 text-success";
  if (label === "warn") return "border-amber-400/30 bg-amber-400/10 text-amber-300";
  return "border-destructive/30 bg-destructive/10 text-destructive";
}

function qualityLabel(label?: "pass" | "warn" | "fail") {
  if (label === "pass") return "通过";
  if (label === "warn") return "预警";
  return "失败";
}

function qualityIssueLabel(issue: string) {
  const labels: Record<string, string> = {
    empty_answer: "回答为空",
    no_context: "缺少检索上下文",
    no_citations: "缺少引用来源",
    low_groundedness: "事实支撑度偏低",
    low_answer_relevance: "回答相关性偏低",
    low_relevance: "回答相关性偏低",
    low_citation_coverage: "引用覆盖不足",
    low_retrieval_quality: "检索质量偏弱",
    weak_retrieval: "检索质量偏弱",
  };
  return labels[issue] || issue.replace(/_/g, " ");
}

function TraceCard({ trace, isLatest }: { trace: Trace; isLatest: boolean }) {
  const [expanded, setExpanded] = useState(isLatest);

  useEffect(() => {
    if (isLatest) setExpanded(true);
  }, [isLatest]);

  const totalErrors = trace.steps.reduce((n, s) => n + (s.errors?.length || 0), 0);
  const totalPerformanceWarnings =
    trace.timings?.performance_warning_count ??
    trace.steps.reduce((n, s) => n + (s.performance_warnings?.length || 0), 0);
  const hasErrors = totalErrors > 0;
  const hasWarnings = hasErrors || totalPerformanceWarnings > 0;
  const maxMs = Math.max(...trace.steps.map(stepDuration), 1);
  const totalTokens = trace.steps.reduce((n, s) => n + (s.tokens || 0), 0);
  const qualityStep = trace.steps.find((step) => step.name === "evaluate");
  const slowestStepName = trace.timings?.slowest_step?.name;
  const slowestStepDuration = trace.timings?.slowest_step?.duration_ms;

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
        {hasWarnings && (
          <span className="flex shrink-0 items-center gap-1 text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            {totalErrors + totalPerformanceWarnings}
          </span>
        )}
        <span className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          {formatMs(trace.total_ms)}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border/50 px-4 pb-4">
          {qualityStep && <TraceQualitySummary step={qualityStep} />}

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
              {hasWarnings ? (
                <>
                  <AlertTriangle className="h-3 w-3 text-warning" />
                  <span className="text-warning">{totalErrors + totalPerformanceWarnings} 个警告</span>
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
              {slowestStepName && slowestStepDuration != null && (
                <span>最慢 {STEP_CONFIG[slowestStepName]?.label || slowestStepName} {formatMs(slowestStepDuration)}</span>
              )}
              <span>{trace.steps.length} 步</span>
              <span>{formatMs(trace.total_ms)} 总计</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TraceQualitySummary({ step }: { step: TraceStep }) {
  const metrics = [
    ["事实支撑", step.groundedness],
    ["回答相关", step.answer_relevance],
    ["引用覆盖", step.citation_coverage],
    ["检索质量", step.retrieval_quality],
  ] as const;

  return (
    <div
      className="mt-3 rounded-lg border border-border/60 bg-background/45 p-3"
      data-testid="trace-quality-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            回答质量
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center rounded-md border px-2 py-1 font-mono text-sm font-semibold",
                qualityTone(step.label),
              )}
              data-testid="trace-quality-overall"
            >
              {formatPercent(step.overall_score)}
            </span>
            <span className="text-xs text-muted-foreground">{qualityLabel(step.label)}</span>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {metrics.map(([label, value]) => (
            <div
              key={label}
              className="rounded-md border border-border/60 bg-card/40 px-2 py-1.5"
              data-testid="trace-quality-metric"
            >
              <div className="text-[10px] text-muted-foreground">{label}</div>
              <div className="font-mono text-xs font-semibold text-foreground">{formatPercent(value)}</div>
            </div>
          ))}
        </div>
      </div>
      {step.issues && step.issues.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {step.issues.map((issue) => (
            <span
              key={issue}
              className="rounded-md border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-300"
            >
              {qualityIssueLabel(issue)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function TraceStepView({ step, maxMs }: { step: TraceStep; maxMs: number }) {
  const [open, setOpen] = useState(step.name === "retrieve" || step.name === "rank" || step.name === "cite");
  const config = STEP_CONFIG[step.name] || {
    icon: CircleDot,
    color: "text-muted-foreground",
    bg: "bg-muted-foreground/20",
    label: step.name,
  };
  const Icon = config.icon;
  const durationMs = stepDuration(step);
  const pct = (durationMs / maxMs) * 100;
  const performanceWarnings = step.performance_warnings || [];
  const hasDetails = Boolean(
    step.backends ||
      step.details?.length ||
      step.results?.length ||
      step.candidates?.length ||
      step.errors?.length ||
      performanceWarnings.length ||
      step.rejection_counts ||
      step.settings ||
      step.reranker,
  );

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
          {formatMs(durationMs)}
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
        {step.filtered_counts && (
          <Chip>
            已过滤{" "}
            {Object.values(step.filtered_counts).reduce((sum, count) => sum + (count || 0), 0)}
          </Chip>
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
        {step.duration_ms != null && (
          <Chip>累计 {formatMs(step.elapsed_ms)}</Chip>
        )}
        {performanceWarnings.map((warning) => (
          <Chip key={`${warning.code}-${warning.budget_ms}`}>
            超预算 {formatMs(warning.over_by_ms)}
          </Chip>
        ))}
      </div>

      {open && hasDetails && (
        <div className="ml-5 mt-3 space-y-3">
          {step.backends && <BackendHealthView backends={step.backends} />}
          {step.details && <RetrievalDetailsView details={step.details} />}
          {step.name === "rank" && step.reranker && <RerankerSummary reranker={step.reranker} />}
          {step.name === "cite" && <CitationPruningSummary step={step} />}
          {performanceWarnings.length > 0 && <PerformanceWarningList warnings={performanceWarnings} />}
          {step.results && step.results.length > 0 && (
            <ResultList
              title={step.name === "cite" ? "保留引用" : "融合排序结果"}
              results={step.results}
            />
          )}
          {step.candidates && step.candidates.length > 0 && (
            <ResultList title="引用候选明细" results={step.candidates} showCitationDecision />
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

function PerformanceWarningList({ warnings }: { warnings: PerformanceWarning[] }) {
  return (
    <div className="space-y-1.5">
      {warnings.map((warning, index) => (
        <div
          key={`${warning.step}-${warning.budget_ms}-${index}`}
          className="flex items-start gap-1.5 rounded-md border border-amber-400/25 bg-amber-400/10 px-2 py-1.5 text-[10px] text-amber-200"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            性能预算 {formatMs(warning.budget_ms)}，实际 {formatMs(warning.duration_ms)}，
            超出 {formatMs(warning.over_by_ms)}
          </span>
        </div>
      ))}
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

function CitationPruningSummary({ step }: { step: TraceStep }) {
  const rejectionEntries = Object.entries(step.rejection_counts || {});
  const settings = step.settings || {};
  return (
    <div className="rounded-md border border-rose-400/20 bg-rose-400/5 p-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="font-medium text-foreground">引用裁剪</span>
        <Chip>{step.input_count || 0} 候选</Chip>
        <Chip>{step.output_count || 0} 保留</Chip>
        {settings.max_items != null && <Chip>最多 {settings.max_items} 条</Chip>}
        {settings.min_query_coverage != null && (
          <Chip>覆盖阈值 {formatPercent(Number(settings.min_query_coverage))}</Chip>
        )}
      </div>
      {rejectionEntries.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {rejectionEntries.map(([reason, count]) => (
            <span
              key={reason}
              className="rounded-md border border-amber-400/25 bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-300"
            >
              {citationDecisionLabel(reason)} x{count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RerankerSummary({ reranker }: { reranker: NonNullable<TraceStep["reranker"]> }) {
  const weights = Object.entries(reranker.weights || {});
  return (
    <div className="rounded-md border border-purple-400/20 bg-purple-400/5 p-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="font-medium text-foreground">Reranker 重排序</span>
        <Chip>{reranker.enabled ? "已启用" : "未启用"}</Chip>
        {weights.map(([name, value]) => (
          <Chip key={name}>
            {rerankWeightLabel(name)} {Number(value).toFixed(2)}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function ResultList({
  title,
  results,
  error,
  showCitationDecision = false,
}: {
  title: string;
  results: TraceResult[];
  error?: string;
  showCitationDecision?: boolean;
}) {
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
              {result.rerank_features && (
                <div className="mb-1.5 flex flex-wrap gap-1">
                  {Object.entries(result.rerank_features).map(([name, value]) => (
                    <span
                      key={name}
                      className="rounded border border-purple-400/20 bg-purple-400/5 px-1.5 py-0.5 text-[10px] text-purple-300"
                    >
                      {rerankWeightLabel(name)} {formatPercent(value)}
                    </span>
                  ))}
                </div>
              )}
              {hasGraphPath(result.graph_context) && (
                <GraphPathView context={result.graph_context} />
              )}
              {showCitationDecision && (
                <div className="mb-1.5 flex flex-wrap gap-1">
                  <span
                    className={cn(
                      "rounded border px-1.5 py-0.5 text-[10px]",
                      result.citation_selected
                        ? "border-success/30 bg-success/10 text-success"
                        : "border-amber-400/30 bg-amber-400/10 text-amber-300",
                    )}
                  >
                    {result.citation_selected
                      ? citationDecisionLabel(result.citation_reason || "selected")
                      : citationDecisionLabel(result.citation_rejection_reason || "not_selected")}
                  </span>
                  <span className="rounded border border-border/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    问题覆盖 {formatPercent(result.citation_query_coverage)}
                  </span>
                  <span className="rounded border border-border/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    相对分 {formatPercent(result.citation_relative_score)}
                  </span>
                  {result.citation_sources && result.citation_sources.length > 0 && (
                    <span className="rounded border border-border/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      来源 {result.citation_sources.map(sourceLabel).join(" / ")}
                    </span>
                  )}
                </div>
              )}
              <div className="text-[10px] leading-4 text-muted-foreground">{result.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function citationDecisionLabel(reason: string) {
  const labels: Record<string, string> = {
    selected: "保留",
    score_and_coverage: "相关度与覆盖率达标",
    direct_evidence: "直接命中问题证据",
    fallback_minimum_evidence: "兜底保留最相关证据",
    query_coverage_low: "问题覆盖不足",
    relative_score_low: "排序分偏低",
    per_document_limit: "同文档引用已达上限",
    max_items_limit: "引用数量已达上限",
    invalid_candidate: "候选无效",
    not_selected: "未被选中",
  };
  return labels[reason] || reason.replace(/_/g, " ");
}

function hasGraphPath(context?: TraceResult["graph_context"]) {
  return Boolean(
    context &&
      ((context.path_entities && context.path_entities.length > 0) ||
        (context.path_relations && context.path_relations.length > 0) ||
        context.entity ||
        context.matched_entity),
  );
}

function GraphPathView({ context }: { context?: TraceResult["graph_context"] }) {
  if (!context) return null;
  const entities = (context.path_entities || []).filter(Boolean);
  const relations = (context.path_relations || []).filter(Boolean);
  const hasPath = entities.length > 0;

  return (
    <div
      className="mb-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/5 p-2"
      data-testid="trace-graph-path"
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-cyan-200">
        <GitBranch className="h-3 w-3" />
        <span className="font-medium text-cyan-100">图谱路径</span>
        {context.entity && (
          <span className="rounded border border-cyan-400/25 bg-cyan-400/10 px-1.5 py-0.5">
            起点 {context.entity}
          </span>
        )}
        {context.matched_entity && context.matched_entity !== context.entity && (
          <span className="rounded border border-cyan-400/25 bg-cyan-400/10 px-1.5 py-0.5">
            命中 {context.matched_entity}
          </span>
        )}
      </div>

      {hasPath ? (
        <div className="flex flex-wrap items-center gap-1 text-[10px] leading-5">
          {entities.map((entity, index) => (
            <span key={`${entity}-${index}`} className="inline-flex items-center gap-1">
              <span className="rounded border border-cyan-400/30 bg-background/60 px-1.5 py-0.5 font-mono text-cyan-100">
                {entity}
              </span>
              {index < entities.length - 1 && (
                <span className="inline-flex items-center gap-1 text-cyan-300">
                  <span className="h-px w-3 bg-cyan-400/50" />
                  <span className="rounded bg-cyan-400/10 px-1 py-0.5 font-mono">
                    {relations[index] || "RELATES_TO"}
                  </span>
                  <span className="h-px w-3 bg-cyan-400/50" />
                </span>
              )}
            </span>
          ))}
        </div>
      ) : (
        <div className="text-[10px] text-cyan-200/80">
          {context.entity || "未知实体"} → {context.matched_entity || "关联证据"}
        </div>
      )}
    </div>
  );
}

function rerankWeightLabel(name: string) {
  const labels: Record<string, string> = {
    original: "融合分",
    query: "问题覆盖",
    query_coverage: "问题覆盖",
    phrase: "短语命中",
    source: "来源信号",
  };
  return labels[name] || name;
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
