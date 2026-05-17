"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  CheckCircle,
  ChevronDown,
  Database,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import type { EvaluationState, Trace } from "@/components/observability/TracePanel";
import {
  deleteDocument,
  fetchEvaluationHistory,
  fetchDocumentChunks,
  fetchKnowledgeDocuments,
  type EvaluationRecord,
  type KnowledgeChunk,
  type KnowledgeDocument,
  uploadDocument,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ALLOWED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
  "text/html",
  "text/csv",
];

const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm", ".csv"];

type UploadStatus = "uploading" | "done" | "error";

interface UploadItem {
  name: string;
  status: UploadStatus;
  message?: string;
}

interface Props {
  traces?: Trace[];
  evaluations?: Record<string, EvaluationState>;
}

function percent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function scoreTone(value: number) {
  if (value >= 0.75) return "text-success";
  if (value >= 0.45) return "text-warning";
  return "text-destructive";
}

function evaluationScore(evaluation: EvaluationState | undefined) {
  if (evaluation?.status !== "done") return null;
  const result = evaluation.result;
  return (result.faithfulness + result.answer_relevancy + result.context_recall + result.context_precision) / 4;
}

export function UploadPanel({ traces = [], evaluations = {} }: Props) {
  const [files, setFiles] = useState<UploadItem[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [evaluationHistory, setEvaluationHistory] = useState<EvaluationRecord[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [expandedDocumentId, setExpandedDocumentId] = useState<string | null>(null);
  const [chunksByDocument, setChunksByDocument] = useState<Record<string, KnowledgeChunk[]>>({});
  const [chunkLoadingId, setChunkLoadingId] = useState<string | null>(null);
  const [chunkError, setChunkError] = useState<string | null>(null);
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refreshDocuments = useCallback(async () => {
    setLoadingDocuments(true);
    setDocumentError(null);
    try {
      setDocuments(await fetchKnowledgeDocuments());
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "Failed to read documents");
    } finally {
      setLoadingDocuments(false);
    }
  }, []);

  useEffect(() => {
    void refreshDocuments();
  }, [refreshDocuments]);

  useEffect(() => {
    void fetchEvaluationHistory(20)
      .then(setEvaluationHistory)
      .catch(() => setEvaluationHistory([]));
  }, [evaluations]);

  const uploadFile = async (file: File) => {
    const ext = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
    const isValidExt = ALLOWED_EXTENSIONS.includes(ext);
    if (!ALLOWED_TYPES.includes(file.type) && !isValidExt) {
      setFiles((prev) => [...prev, { name: file.name, status: "error", message: "Unsupported file type" }]);
      return;
    }

    setFiles((prev) => [...prev, { name: file.name, status: "uploading" }]);
    try {
      const result = await uploadDocument(file);
      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name
            ? {
                ...f,
                status: "done",
                message: result.status === "duplicate" ? "Duplicate skipped" : `${result.chunk_count || 0} chunks`,
              }
            : f,
        ),
      );
      await refreshDocuments();
    } catch (error) {
      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name
            ? { ...f, status: "error", message: error instanceof Error ? error.message : "Upload failed" }
            : f,
        ),
      );
    }
  };

  const togglePreview = async (documentId: string) => {
    if (expandedDocumentId === documentId) {
      setExpandedDocumentId(null);
      return;
    }

    setExpandedDocumentId(documentId);
    setChunkError(null);
    if (chunksByDocument[documentId]) return;

    setChunkLoadingId(documentId);
    try {
      const chunks = await fetchDocumentChunks(documentId);
      setChunksByDocument((prev) => ({ ...prev, [documentId]: chunks }));
    } catch (error) {
      setChunkError(error instanceof Error ? error.message : "Failed to read chunks");
    } finally {
      setChunkLoadingId(null);
    }
  };

  const handleDelete = async (doc: KnowledgeDocument) => {
    const confirmed = window.confirm(`Delete "${doc.document_name}" from available indexes?`);
    if (!confirmed) return;

    setDeletingDocumentId(doc.document_id);
    setDocumentError(null);
    try {
      await deleteDocument(doc.document_id);
      setDocuments((prev) => prev.filter((item) => item.document_id !== doc.document_id));
      setChunksByDocument((prev) => {
        const next = { ...prev };
        delete next[doc.document_id];
        return next;
      });
      if (expandedDocumentId === doc.document_id) setExpandedDocumentId(null);
      await refreshDocuments();
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "Delete failed");
    } finally {
      setDeletingDocumentId(null);
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    for (const file of Array.from(e.dataTransfer.files)) {
      await uploadFile(file);
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    for (const file of Array.from(e.target.files)) {
      await uploadFile(file);
    }
    e.target.value = "";
  };

  const quality = useMemo(() => {
    const done = traces
      .map((trace) => ({ trace, evaluation: evaluations[trace.query_id], score: evaluationScore(evaluations[trace.query_id]) }))
      .filter((item): item is { trace: Trace; evaluation: Extract<EvaluationState, { status: "done" }>; score: number } => item.score != null);
    const running = traces.filter((trace) => evaluations[trace.query_id]?.status === "running").length;
    const average = done.length ? done.reduce((sum, item) => sum + item.score, 0) / done.length : null;
    return { done, running, average, recent: done.slice(0, 5) };
  }, [evaluations, traces]);

  const doneCount = files.filter((f) => f.status === "done").length;
  const errorCount = files.filter((f) => f.status === "error").length;
  const totalChunks = documents.reduce((sum, doc) => sum + doc.chunk_count, 0);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-5">
      <section className="grid gap-3 sm:grid-cols-4">
        <Metric label="Documents" value={documents.length} />
        <Metric label="Chunks" value={totalChunks} />
        <Metric label="Uploads" value={files.length} />
        <Metric label="Avg Quality" value={quality.average == null ? "--" : percent(quality.average)} tone={quality.average == null ? "" : scoreTone(quality.average)} />
      </section>

      <QualityPanel quality={quality} history={evaluationHistory} />

      <section className="rounded-lg border border-border/70 bg-card/55 p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Import knowledge</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Uploaded files are parsed, chunked, and indexed for grounded answers.
            </p>
          </div>
          {files.length > 0 && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {doneCount} complete{errorCount > 0 ? `, ${errorCount} failed` : ""}
            </span>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={handleFileChange}
          className="hidden"
          aria-label="Choose knowledge files"
        />

        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setDragOver(false);
          }}
          onDrop={handleDrop}
          className={cn(
            "w-full rounded-lg border-2 border-dashed p-8 text-center transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            dragOver ? "border-primary bg-primary/8" : "border-border hover:border-primary/40 hover:bg-muted/30",
          )}
          aria-label="Drop or choose files"
        >
          <div className={cn("mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-lg transition-colors", dragOver ? "bg-primary/15" : "bg-muted/50")}>
            <Upload className={cn("h-7 w-7 transition-colors", dragOver ? "text-primary" : "text-muted-foreground")} />
          </div>
          <p className="mb-1 text-sm font-medium text-foreground">{dragOver ? "Drop to upload" : "Drop files here, or click to choose"}</p>
          <p className="text-xs text-muted-foreground">PDF, Word, Markdown, HTML, TXT, CSV</p>
        </button>

        {files.length > 0 && (
          <div className="mt-4 space-y-2">
            {files.map((f, index) => (
              <UploadRow key={`${f.name}-${index}`} item={f} />
            ))}
          </div>
        )}
      </section>

      <section className="rounded-lg border border-border/70 bg-card/55 p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">Indexed documents</h2>
          </div>
          <button
            type="button"
            onClick={() => void refreshDocuments()}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/70 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="Refresh documents"
            disabled={loadingDocuments}
          >
            <RefreshCw className={cn("h-4 w-4", loadingDocuments && "animate-spin")} />
          </button>
        </div>

        {documentError && <ErrorCallout title="Failed to read knowledge base" message={documentError} onRetry={() => void refreshDocuments()} />}

        {!documentError && loadingDocuments && (
          <div className="flex items-center gap-2 px-1 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading...
          </div>
        )}

        {!documentError && !loadingDocuments && documents.length === 0 && (
          <div className="rounded-lg border border-dashed border-border/80 px-4 py-8 text-center text-sm text-muted-foreground">
            No documents indexed yet
          </div>
        )}

        {!documentError && !loadingDocuments && documents.length > 0 && (
          <div className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/70">
            {documents.map((doc) => {
              const expanded = expandedDocumentId === doc.document_id;
              const chunks = chunksByDocument[doc.document_id] || [];
              const loadingChunks = chunkLoadingId === doc.document_id;
              const deleting = deletingDocumentId === doc.document_id;

              return (
                <div key={doc.document_id} className="bg-background/45">
                  <div className="flex items-center gap-3 px-3 py-3">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-foreground">{doc.document_name}</div>
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{doc.document_id}</div>
                    </div>
                    <span className="hidden shrink-0 rounded-md border border-border/70 px-2 py-1 text-xs text-muted-foreground sm:inline-flex">
                      {doc.chunk_count} chunks
                    </span>
                    <div className="hidden shrink-0 items-center gap-1 lg:flex">
                      <IndexStatusChips statuses={doc.index_statuses} />
                    </div>
                    <button
                      type="button"
                      onClick={() => void togglePreview(doc.document_id)}
                      className="inline-flex h-8 items-center gap-1 rounded-md border border-border/70 px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      aria-expanded={expanded}
                    >
                      Preview
                      <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-180")} />
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(doc)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-destructive/30 text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive disabled:cursor-not-allowed disabled:opacity-60"
                      aria-label={`Delete ${doc.document_name}`}
                      disabled={deleting}
                    >
                      {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    </button>
                  </div>

                  {expanded && (
                    <div className="border-t border-border/60 px-3 py-3">
                      <div className="mb-3 flex flex-wrap items-center gap-1.5 lg:hidden">
                        <IndexStatusChips statuses={doc.index_statuses} />
                      </div>
                      {doc.errors.length > 0 && (
                        <div className="mb-3 space-y-1">
                          {doc.errors.map((error, index) => (
                            <div key={index} className="rounded-md border border-amber-400/20 bg-amber-400/5 px-2 py-1 text-[11px] text-amber-300">
                              {error}
                            </div>
                          ))}
                        </div>
                      )}
                      {loadingChunks && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          Loading chunks...
                        </div>
                      )}
                      {chunkError && !loadingChunks && (
                        <div className="flex items-center gap-2 text-xs text-destructive">
                          <AlertCircle className="h-3.5 w-3.5" />
                          {chunkError}
                        </div>
                      )}
                      {!loadingChunks && !chunkError && chunks.length === 0 && <div className="text-xs text-muted-foreground">No chunks</div>}
                      {!loadingChunks && !chunkError && chunks.length > 0 && (
                        <div className="space-y-2">
                          {chunks.map((chunk) => (
                            <div key={chunk.chunk_id} className="rounded-md border border-border/70 bg-card/60 p-3">
                              <div className="mb-2 flex items-center justify-between gap-2">
                                <span className="text-xs font-medium text-primary">Chunk #{chunk.chunk_index + 1}</span>
                                <span className="truncate font-mono text-[10px] text-muted-foreground">{chunk.chunk_id}</span>
                              </div>
                              <p className="whitespace-pre-wrap text-xs leading-6 text-muted-foreground">{chunk.text}</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

function QualityPanel({
  quality,
  history,
}: {
  quality: {
    done: Array<{ trace: Trace; evaluation: Extract<EvaluationState, { status: "done" }>; score: number }>;
    running: number;
    average: number | null;
    recent: Array<{ trace: Trace; evaluation: Extract<EvaluationState, { status: "done" }>; score: number }>;
  };
  history: EvaluationRecord[];
}) {
  const historyScores = history.map((item) => ({
    ...item,
    score: (item.faithfulness + item.answer_relevancy + item.context_recall + item.context_precision) / 4,
  }));
  const fallbackAverage = historyScores.length
    ? historyScores.reduce((sum, item) => sum + item.score, 0) / historyScores.length
    : null;
  const visibleAverage = quality.average ?? fallbackAverage;

  return (
    <section className="rounded-lg border border-border/70 bg-card/55 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Recent answer quality</h2>
        </div>
        <span className="text-[11px] text-muted-foreground">
          {quality.done.length || historyScores.length} scored{quality.running > 0 ? `, ${quality.running} running` : ""}
        </span>
      </div>

      {visibleAverage == null ? (
        <div className="rounded-lg border border-dashed border-border/80 px-4 py-6 text-center text-sm text-muted-foreground">
          Ask a question to see quality scores here.
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-md border border-border/60 bg-background/45 px-3 py-2">
            <span className="text-xs text-muted-foreground">Average quality</span>
            <span className={cn("font-mono text-sm font-semibold", scoreTone(visibleAverage))}>{percent(visibleAverage)}</span>
          </div>
          {quality.recent.length > 0 && (
            <div className="space-y-2">
            {quality.recent.map(({ trace, evaluation, score }) => (
              <div key={trace.query_id} className="rounded-md border border-border/60 bg-background/45 px-3 py-2">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-xs font-medium text-foreground">{trace.original_query}</span>
                  <span className={cn("shrink-0 font-mono text-[11px]", scoreTone(score))}>{percent(score)}</span>
                </div>
                <div className="grid gap-1.5 sm:grid-cols-4">
                  <MiniMetric label="faith" value={evaluation.result.faithfulness} />
                  <MiniMetric label="rel" value={evaluation.result.answer_relevancy} />
                  <MiniMetric label="recall" value={evaluation.result.context_recall} />
                  <MiniMetric label="prec" value={evaluation.result.context_precision} />
                </div>
              </div>
            ))}
            </div>
          )}
          {quality.recent.length === 0 && historyScores.length > 0 && (
            <div className="space-y-2">
              {historyScores.slice(0, 5).map((item) => (
                <div key={item.id} className="rounded-md border border-border/60 bg-background/45 px-3 py-2">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate text-xs font-medium text-foreground">{item.query}</span>
                    <span className={cn("shrink-0 font-mono text-[11px]", scoreTone(item.score))}>{percent(item.score)}</span>
                  </div>
                  <div className="grid gap-1.5 sm:grid-cols-4">
                    <MiniMetric label="faith" value={item.faithfulness} />
                    <MiniMetric label="rel" value={item.answer_relevancy} />
                    <MiniMetric label="recall" value={item.context_recall} />
                    <MiniMetric label="prec" value={item.context_precision} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function MiniMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded border border-border/50 bg-card/35 px-2 py-1 text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-mono", scoreTone(value))}>{percent(value)}</span>
    </div>
  );
}

function ErrorCallout({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <div className="mb-3 flex items-start gap-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2.5 text-xs text-destructive">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <div className="mt-1 break-words leading-5 text-destructive/90">{message}</div>
      </div>
      <button type="button" onClick={onRetry} className="shrink-0 rounded-md border border-destructive/30 px-2 py-1 font-medium transition-colors hover:bg-destructive/10">
        Retry
      </button>
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  return (
    <div className={cn("flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm transition-colors", item.status === "error" ? "border-destructive/25 bg-destructive/5" : "border-border/60 bg-background/55")}>
      <FileText className={cn("h-4 w-4 shrink-0", item.status === "error" ? "text-destructive" : "text-muted-foreground")} />
      <span className="min-w-0 flex-1 truncate text-foreground">{item.name}</span>
      {item.status === "uploading" && (
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          uploading
        </span>
      )}
      {item.status === "done" && (
        <span className="flex items-center gap-1 text-xs font-medium text-success">
          <CheckCircle className="h-3.5 w-3.5" />
          {item.message || "done"}
        </span>
      )}
      {item.status === "error" && (
        <span className="flex max-w-[40%] items-center gap-1 truncate text-xs font-medium text-destructive">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {item.message || "failed"}
        </span>
      )}
    </div>
  );
}

function Metric({ label, value, tone = "" }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-card/55 px-4 py-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-xl font-semibold text-foreground", tone)}>{value}</div>
    </div>
  );
}

function IndexStatusChips({ statuses }: { statuses: Record<string, string> }) {
  const items = [
    ["vector", "Vector"],
    ["bm25", "BM25"],
    ["graph", "Graph"],
  ] as const;

  return (
    <>
      {items.map(([key, label]) => {
        const status = statuses?.[key] || "unknown";
        return (
          <span
            key={key}
            className={cn(
              "rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
              status === "ready" && "border-success/30 bg-success/10 text-success",
              status === "skipped" && "border-amber-400/30 bg-amber-400/10 text-amber-300",
              status === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
              status === "unknown" && "border-border/70 bg-muted/30 text-muted-foreground",
            )}
            title={`${label}: ${status}`}
          >
            {label}:{status}
          </span>
        );
      })}
    </>
  );
}
