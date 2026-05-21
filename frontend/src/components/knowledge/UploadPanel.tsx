"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  CheckCircle,
  ChevronDown,
  Database,
  FileText,
  FlaskConical,
  Loader2,
  PauseCircle,
  Power,
  RefreshCw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import {
  cancelDocumentIngestion,
  deleteDocument,
  fetchIngestionDeadLetterJobs,
  fetchIngestionQueueHealth,
  fetchEvaluations,
  fetchDocumentChunks,
  fetchKnowledgeDocuments,
  type DocumentLifecycleStatus,
  type EvaluationResult,
  type IngestionDeadLetterJob,
  type IngestionQueueHealth,
  type KnowledgeChunk,
  type KnowledgeDocument,
  retryDocumentIngestion,
  updateDocumentStatus,
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

const ALLOWED_EXTENSIONS = [
  ".pdf",
  ".docx",
  ".txt",
  ".md",
  ".markdown",
  ".html",
  ".htm",
  ".csv",
];

type UploadStatus = "uploading" | "done" | "error";

interface UploadItem {
  name: string;
  status: UploadStatus;
  message?: string;
}

interface Props {
  qualityRefreshKey?: number;
}

export function UploadPanel({ qualityRefreshKey = 0 }: Props) {
  const [files, setFiles] = useState<UploadItem[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationResult[]>([]);
  const [queueHealth, setQueueHealth] = useState<IngestionQueueHealth | null>(null);
  const [deadLetterJobs, setDeadLetterJobs] = useState<IngestionDeadLetterJob[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [loadingQuality, setLoadingQuality] = useState(true);
  const [loadingQueueHealth, setLoadingQueueHealth] = useState(true);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [qualityError, setQualityError] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [expandedDocumentId, setExpandedDocumentId] = useState<string | null>(null);
  const [chunksByDocument, setChunksByDocument] = useState<Record<string, KnowledgeChunk[]>>({});
  const [chunkLoadingId, setChunkLoadingId] = useState<string | null>(null);
  const [chunkError, setChunkError] = useState<string | null>(null);
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(null);
  const [updatingStatusDocumentId, setUpdatingStatusDocumentId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<DocumentLifecycleStatus | "all">("all");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refreshDocuments = useCallback(async () => {
    setLoadingDocuments(true);
    setDocumentError(null);
    try {
      setDocuments(await fetchKnowledgeDocuments());
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "读取失败");
    } finally {
      setLoadingDocuments(false);
    }
  }, []);

  const refreshQuality = useCallback(async () => {
    setLoadingQuality(true);
    setQualityError(null);
    try {
      setEvaluations(await fetchEvaluations(20));
    } catch (error) {
      setQualityError(error instanceof Error ? error.message : "读取质量评估失败");
    } finally {
      setLoadingQuality(false);
    }
  }, []);

  const refreshIngestionQueue = useCallback(async () => {
    setLoadingQueueHealth(true);
    setQueueError(null);
    try {
      const [health, deadLetters] = await Promise.all([
        fetchIngestionQueueHealth(),
        fetchIngestionDeadLetterJobs(5),
      ]);
      setQueueHealth(health);
      setDeadLetterJobs(deadLetters);
    } catch (error) {
      setQueueError(error instanceof Error ? error.message : "读取入库队列失败");
    } finally {
      setLoadingQueueHealth(false);
    }
  }, []);

  useEffect(() => {
    void refreshDocuments();
  }, [refreshDocuments]);

  useEffect(() => {
    void refreshIngestionQueue();
  }, [refreshIngestionQueue]);

  useEffect(() => {
    const hasActiveIngestion = documents.some((doc) =>
      ["queued", "processing"].includes(doc.status),
    );
    if (!hasActiveIngestion) return;

    const timer = window.setInterval(() => {
      void refreshDocuments();
      void refreshIngestionQueue();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [documents, refreshDocuments, refreshIngestionQueue]);

  useEffect(() => {
    void refreshQuality();
  }, [refreshQuality, qualityRefreshKey]);

  const uploadFile = async (file: File) => {
    const ext = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
    const isValidExt = ALLOWED_EXTENSIONS.includes(ext);
    if (!ALLOWED_TYPES.includes(file.type) && !isValidExt) {
      setFiles((prev) => [
        ...prev,
        { name: file.name, status: "error", message: "不支持的文件类型" },
      ]);
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
                message:
                  result.status === "duplicate"
                    ? "已存在"
                    : result.status === "queued"
                      ? "已加入队列"
                      : `${result.chunk_count || 0} 个片段`,
              }
            : f,
        ),
      );
      await refreshDocuments();
      await refreshQuality();
      await refreshIngestionQueue();
    } catch (error) {
      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name
            ? {
                ...f,
                status: "error",
                message: error instanceof Error ? error.message : "上传失败",
              }
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
      setChunkError(error instanceof Error ? error.message : "读取片段失败");
    } finally {
      setChunkLoadingId(null);
    }
  };

  const handleDelete = async (doc: KnowledgeDocument) => {
    const confirmed = window.confirm(`确定删除「${doc.document_name}」吗？删除后它将不再参与检索。`);
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
      await refreshIngestionQueue();
      await refreshQuality();
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "删除失败");
    } finally {
      setDeletingDocumentId(null);
    }
  };

  const handleRetryIngestion = async (doc: KnowledgeDocument) => {
    setRetryingDocumentId(doc.document_id);
    setDocumentError(null);
    try {
      const updated = await retryDocumentIngestion(doc.document_id);
      setDocuments((prev) =>
        prev.map((item) =>
          item.document_id === doc.document_id
            ? {
                ...item,
                status: updated.status,
                job_id: updated.job_id,
                attempt_count: updated.attempt_count,
                max_attempts: updated.max_attempts,
                last_error: "",
                is_retrievable: false,
                index_statuses: {
                  ...item.index_statuses,
                  ingestion: "queued",
                },
                errors: [],
              }
            : item,
        ),
      );
      await refreshDocuments();
      await refreshIngestionQueue();
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "重试入库失败");
    } finally {
      setRetryingDocumentId(null);
    }
  };

  const handleStatusChange = async (
    doc: KnowledgeDocument,
    lifecycleStatus: DocumentLifecycleStatus,
  ) => {
    if (doc.lifecycle_status === lifecycleStatus) return;

    setUpdatingStatusDocumentId(doc.document_id);
    setDocumentError(null);
    try {
      const updated = await updateDocumentStatus(doc.document_id, lifecycleStatus);
      setDocuments((prev) =>
        prev.map((item) =>
          item.document_id === doc.document_id
            ? {
                ...item,
                lifecycle_status: updated.lifecycle_status,
                is_retrievable: updated.is_retrievable,
              }
            : item,
        ),
      );
      await refreshQuality();
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "更新文档状态失败");
    } finally {
      setUpdatingStatusDocumentId(null);
    }
  };

  const handleCancelIngestion = async (doc: KnowledgeDocument) => {
    setCancellingDocumentId(doc.document_id);
    setDocumentError(null);
    try {
      await cancelDocumentIngestion(doc.document_id);
      setDocuments((prev) =>
        prev.map((item) =>
          item.document_id === doc.document_id
            ? {
                ...item,
                status: "cancelled",
                is_retrievable: false,
                index_statuses: {
                  ...item.index_statuses,
                  ingestion: "cancelled",
                },
              }
            : item,
        ),
      );
      await refreshDocuments();
    } catch (error) {
      setDocumentError(error instanceof Error ? error.message : "取消入库失败");
    } finally {
      setCancellingDocumentId(null);
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

  const doneCount = files.filter((f) => f.status === "done").length;
  const errorCount = files.filter((f) => f.status === "error").length;
  const totalChunks = documents.reduce((sum, doc) => sum + doc.chunk_count, 0);
  const retrievableDocuments = documents.filter((doc) => doc.is_retrievable).length;
  const activeIngestionCount = documents.filter((doc) =>
    ["queued", "processing"].includes(doc.status),
  ).length;
  const blockedIngestionCount =
    documents.filter((doc) => ["error", "cancelled"].includes(doc.status)).length +
    (queueHealth?.dead_letter_length ?? 0);
  const visibleDocuments =
    statusFilter === "all"
      ? documents
      : documents.filter((doc) => doc.lifecycle_status === statusFilter);
  const averageQuality =
    evaluations.length > 0
      ? evaluations.reduce((sum, item) => sum + item.overall_score, 0) / evaluations.length
      : null;
  const lowQualityCount = evaluations.filter((item) => item.label === "fail").length;
  const warningQualityCount = evaluations.filter((item) => item.label === "warn").length;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-5">
      <section className="grid gap-3 sm:grid-cols-4">
        <Metric label="文档总数" value={documents.length} />
        <Metric label="可检索" value={retrievableDocuments} />
        <Metric label="入库中" value={activeIngestionCount} />
        <Metric label="待处理失败" value={blockedIngestionCount} />
      </section>

      <QualityOverview
        evaluations={evaluations}
        averageQuality={averageQuality}
        lowQualityCount={lowQualityCount}
        warningQualityCount={warningQualityCount}
        loading={loadingQuality}
        error={qualityError}
        onRefresh={() => void refreshQuality()}
      />

      <IngestionOpsOverview
        health={queueHealth}
        deadLetters={deadLetterJobs}
        loading={loadingQueueHealth}
        error={queueError}
        onRefresh={() => void refreshIngestionQueue()}
      />

      <section className="rounded-lg border border-border/70 bg-card/55 p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">知识库文档</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              上传后会自动解析、切分并建立检索索引。
            </p>
          </div>
          {files.length > 0 && (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              {doneCount} 个完成{errorCount > 0 ? `，${errorCount} 个失败` : ""}
            </span>
          )}
        </div>

        <input
          ref={fileInputRef}
          data-testid="knowledge-file-input"
          type="file"
          multiple
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={handleFileChange}
          className="hidden"
          aria-label="选择知识库文档"
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
            dragOver
              ? "border-primary bg-primary/8"
              : "border-border hover:border-primary/40 hover:bg-muted/30",
          )}
          aria-label="点击或拖拽上传文档"
        >
          <div
            className={cn(
              "mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-lg transition-colors",
              dragOver ? "bg-primary/15" : "bg-muted/50",
            )}
          >
            <Upload
              className={cn(
                "h-7 w-7 transition-colors",
                dragOver ? "text-primary" : "text-muted-foreground",
              )}
            />
          </div>
          <p className="mb-1 text-sm font-medium text-foreground">
            {dragOver ? "松开即可上传" : "点击或拖拽上传文档"}
          </p>
          <p className="text-xs text-muted-foreground">
            PDF、Word、Markdown、HTML、TXT、CSV
          </p>
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
            <h2 className="text-sm font-semibold">已入库文档</h2>
          </div>
          <button
            type="button"
            data-testid="knowledge-refresh"
            onClick={() => {
              void refreshDocuments();
              void refreshQuality();
            }}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/70 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
            aria-label="刷新文档列表"
            disabled={loadingDocuments || loadingQuality}
          >
            <RefreshCw className={cn("h-4 w-4", (loadingDocuments || loadingQuality) && "animate-spin")} />
          </button>
        </div>

        {!documentError && !loadingDocuments && documents.length > 0 && (
          <DocumentStatusFilters
            value={statusFilter}
            documents={documents}
            onChange={setStatusFilter}
          />
        )}

        {documentError && (
          <ErrorCallout
            title="无法读取入库文档"
            message={documentError}
            onRetry={() => void refreshDocuments()}
          />
        )}

        {!documentError && loadingDocuments && (
          <div className="flex items-center gap-2 px-1 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            读取中
          </div>
        )}

        {!documentError && !loadingDocuments && documents.length === 0 && (
          <div className="rounded-lg border border-dashed border-border/80 px-4 py-8 text-center text-sm text-muted-foreground">
            暂无入库文档
          </div>
        )}

        {!documentError && !loadingDocuments && documents.length > 0 && visibleDocuments.length === 0 && (
          <div className="rounded-lg border border-dashed border-border/80 px-4 py-8 text-center text-sm text-muted-foreground">
            当前筛选条件下没有文档
          </div>
        )}

        {!documentError && !loadingDocuments && visibleDocuments.length > 0 && (
          <div className="divide-y divide-border/60 overflow-hidden rounded-lg border border-border/70">
            {visibleDocuments.map((doc) => {
              const expanded = expandedDocumentId === doc.document_id;
              const chunks = chunksByDocument[doc.document_id] || [];
              const loadingChunks = chunkLoadingId === doc.document_id;
              const deleting = deletingDocumentId === doc.document_id;
              const cancelling = cancellingDocumentId === doc.document_id;
              const retrying = retryingDocumentId === doc.document_id;
              const updatingStatus = updatingStatusDocumentId === doc.document_id;
              const canCancelIngestion = ["queued", "processing"].includes(doc.status);
              const canRetryIngestion = ["error", "cancelled"].includes(doc.status);

              return (
                <div key={doc.document_id} className="bg-background/45" data-testid="knowledge-document-row">
                  <div className="flex items-center gap-3 px-3 py-3">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-foreground">
                        {doc.document_name}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {doc.document_id}
                      </div>
                    </div>
                    <span className="hidden shrink-0 rounded-md border border-border/70 px-2 py-1 text-xs text-muted-foreground sm:inline-flex">
                      {doc.chunk_count} 片段
                    </span>
                    <DocumentIngestionStatus
                      status={doc.status}
                      attemptCount={doc.attempt_count}
                      maxAttempts={doc.max_attempts}
                    />
                    <DocumentStatusSelect
                      value={doc.lifecycle_status}
                      disabled={updatingStatus}
                      onChange={(status) => void handleStatusChange(doc, status)}
                    />
                    <div className="hidden shrink-0 items-center gap-1 lg:flex">
                      <IndexStatusChips statuses={doc.index_statuses} />
                    </div>
                    <button
                      type="button"
                      onClick={() => void togglePreview(doc.document_id)}
                      className="inline-flex h-8 items-center gap-1 rounded-md border border-border/70 px-2 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      aria-expanded={expanded}
                    >
                      预览
                      <ChevronDown
                        className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-180")}
                      />
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleCancelIngestion(doc)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-amber-400/30 text-amber-300 transition-colors hover:bg-amber-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label={`取消入库 ${doc.document_name}`}
                      disabled={!canCancelIngestion || cancelling}
                      title="取消入库"
                    >
                      {cancelling ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleRetryIngestion(doc)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-sky-400/30 text-sky-300 transition-colors hover:bg-sky-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label={`重试入库 ${doc.document_name}`}
                      disabled={!canRetryIngestion || retrying}
                      title="重试入库"
                    >
                      {retrying ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(doc)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-destructive/30 text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive disabled:cursor-not-allowed disabled:opacity-60"
                      aria-label={`删除 ${doc.document_name}`}
                      disabled={deleting}
                    >
                      {deleting ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>

                  {expanded && (
                    <div className="border-t border-border/60 px-3 py-3">
                      <div className="mb-3 flex flex-wrap items-center gap-1.5 lg:hidden">
                        <IndexStatusChips statuses={doc.index_statuses} />
                      </div>
                      {(doc.last_error || doc.errors.length > 0) && (
                        <div className="mb-3 space-y-1">
                          {doc.last_error && (
                            <div className="rounded-md border border-destructive/20 bg-destructive/5 px-2 py-1 text-[11px] text-destructive">
                              {doc.last_error}
                            </div>
                          )}
                          {doc.errors.map((error, index) => (
                            <div
                              key={index}
                              className="rounded-md border border-amber-400/20 bg-amber-400/5 px-2 py-1 text-[11px] text-amber-300"
                            >
                              {error}
                            </div>
                          ))}
                        </div>
                      )}
                      {loadingChunks && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          读取片段中
                        </div>
                      )}
                      {chunkError && !loadingChunks && (
                        <div className="flex items-center gap-2 text-xs text-destructive">
                          <AlertCircle className="h-3.5 w-3.5" />
                          {chunkError}
                        </div>
                      )}
                      {!loadingChunks && !chunkError && chunks.length === 0 && (
                        <div className="text-xs text-muted-foreground">暂无片段</div>
                      )}
                      {!loadingChunks && !chunkError && chunks.length > 0 && (
                        <div className="space-y-2">
                          {chunks.map((chunk) => (
                            <div
                              key={chunk.chunk_id}
                              className="rounded-md border border-border/70 bg-card/60 p-3"
                            >
                              <div className="mb-2 flex items-center justify-between gap-2">
                                <span className="text-xs font-medium text-primary">
                                  Chunk #{chunk.chunk_index + 1}
                                </span>
                                <span className="truncate font-mono text-[10px] text-muted-foreground">
                                  {chunk.chunk_id}
                                </span>
                              </div>
                              <p className="whitespace-pre-wrap text-xs leading-6 text-muted-foreground">
                                {chunk.text}
                              </p>
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

function ErrorCallout({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="mb-3 flex items-start gap-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2.5 text-xs text-destructive">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <div className="mt-1 break-words leading-5 text-destructive/90">{message}</div>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-md border border-destructive/30 px-2 py-1 font-medium transition-colors hover:bg-destructive/10"
      >
        重试
      </button>
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm transition-colors",
        item.status === "error"
          ? "border-destructive/25 bg-destructive/5"
          : "border-border/60 bg-background/55",
      )}
    >
      <FileText
        className={cn(
          "h-4 w-4 shrink-0",
          item.status === "error" ? "text-destructive" : "text-muted-foreground",
        )}
      />
      <span className="min-w-0 flex-1 truncate text-foreground">{item.name}</span>
      {item.status === "uploading" && (
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          上传中
        </span>
      )}
      {item.status === "done" && (
        <span className="flex items-center gap-1 text-xs font-medium text-success">
          <CheckCircle className="h-3.5 w-3.5" />
          {item.message || "完成"}
        </span>
      )}
      {item.status === "error" && (
        <span className="flex max-w-[40%] items-center gap-1 truncate text-xs font-medium text-destructive">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {item.message || "失败"}
        </span>
      )}
    </div>
  );
}

const DOCUMENT_STATUS_OPTIONS: Array<{
  value: DocumentLifecycleStatus;
  label: string;
  icon: typeof Power;
}> = [
  { value: "enabled", label: "启用", icon: Power },
  { value: "disabled", label: "停用", icon: PauseCircle },
  { value: "test", label: "测试", icon: FlaskConical },
  { value: "archived", label: "归档", icon: Archive },
];

function DocumentStatusFilters({
  value,
  documents,
  onChange,
}: {
  value: DocumentLifecycleStatus | "all";
  documents: KnowledgeDocument[];
  onChange: (value: DocumentLifecycleStatus | "all") => void;
}) {
  const counts = documents.reduce<Record<string, number>>(
    (acc, doc) => {
      acc.all += 1;
      acc[doc.lifecycle_status] = (acc[doc.lifecycle_status] || 0) + 1;
      return acc;
    },
    { all: 0, enabled: 0, disabled: 0, test: 0, archived: 0 },
  );
  const items = [
    { value: "all" as const, label: "全部", icon: Database },
    ...DOCUMENT_STATUS_OPTIONS,
  ];

  return (
    <div className="mb-3 flex flex-wrap gap-1.5">
      {items.map((item) => {
        const Icon = item.icon;
        const active = value === item.value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange(item.value)}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors",
              active
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-border/70 text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {item.label}
            <span className="font-mono text-[10px] opacity-75">{counts[item.value] || 0}</span>
          </button>
        );
      })}
    </div>
  );
}

function DocumentStatusSelect({
  value,
  disabled,
  onChange,
}: {
  value: DocumentLifecycleStatus;
  disabled: boolean;
  onChange: (value: DocumentLifecycleStatus) => void;
}) {
  return (
    <label className="relative shrink-0">
      <span className="sr-only">文档状态</span>
      <select
        data-testid="knowledge-document-status"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as DocumentLifecycleStatus)}
        className={cn(
          "h-8 rounded-md border bg-background px-2 pr-7 text-xs outline-none transition-colors",
          "focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60",
          documentStatusTone(value),
        )}
      >
        {DOCUMENT_STATUS_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {disabled && (
        <Loader2 className="pointer-events-none absolute right-2 top-2 h-3.5 w-3.5 animate-spin text-muted-foreground" />
      )}
    </label>
  );
}

function DocumentIngestionStatus({
  status,
  attemptCount = 0,
  maxAttempts = 0,
}: {
  status: string;
  attemptCount?: number;
  maxAttempts?: number;
}) {
  const label = ingestionStatusLabel(status);
  const active = status === "queued" || status === "processing";
  const failed = status === "error";
  const attemptLabel = maxAttempts > 0 && (active || failed) ? ` ${attemptCount}/${maxAttempts}` : "";
  return (
    <span
      className={cn(
        "inline-flex h-8 shrink-0 items-center gap-1 rounded-md border px-2 text-xs",
        active && "border-sky-400/35 bg-sky-400/10 text-sky-300",
        failed && "border-destructive/35 bg-destructive/10 text-destructive",
        status === "partial" && "border-amber-400/35 bg-amber-400/10 text-amber-300",
        status === "cancelled" && "border-border/70 bg-muted/30 text-muted-foreground",
        (status === "ready" || status === "duplicate") && "border-success/35 bg-success/10 text-success",
      )}
    >
      {active && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {!active && failed && <AlertCircle className="h-3.5 w-3.5" />}
      {!active && !failed && <CheckCircle className="h-3.5 w-3.5" />}
      {label}
      {attemptLabel && <span className="font-mono opacity-80">{attemptLabel}</span>}
    </span>
  );
}

function ingestionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    processing: "入库中",
    ready: "已就绪",
    partial: "部分就绪",
    error: "失败",
    duplicate: "已存在",
    cancelled: "已取消",
  };
  return labels[status] || status;
}

function documentStatusTone(status: DocumentLifecycleStatus) {
  const tones: Record<DocumentLifecycleStatus, string> = {
    enabled: "border-success/35 text-success",
    disabled: "border-amber-400/35 text-amber-300",
    test: "border-sky-400/35 text-sky-300",
    archived: "border-border/70 text-muted-foreground",
  };
  return tones[status];
}

function IngestionOpsOverview({
  health,
  deadLetters,
  loading,
  error,
  onRefresh,
}: {
  health: IngestionQueueHealth | null;
  deadLetters: IngestionDeadLetterJob[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  return (
    <section
      className="rounded-lg border border-border/70 bg-card/55 p-4"
      data-testid="knowledge-ingestion-ops"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">入库队列</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            监控异步入库队列、失败队列和 worker 模式。
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/70 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
          aria-label="刷新入库队列"
          disabled={loading}
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-4">
        <QualityMetric label="模式" value={health?.mode || "--"} />
        <QualityMetric label="等待" value={String(health?.queue_length ?? "--")} />
        <QualityMetric
          label="DLQ"
          value={String(health?.dead_letter_length ?? "--")}
          tone={(health?.dead_letter_length || 0) > 0 ? "bad" : "ok"}
        />
        <QualityMetric
          label="Redis"
          value={health ? (health.redis_available ? "OK" : "离线") : "--"}
          tone={health?.redis_available ? "ok" : "neutral"}
        />
      </div>

      {health && (
        <div className="mt-3 truncate text-[11px] text-muted-foreground">
          {health.queue_name} / {health.dlq_name}
        </div>
      )}

      {deadLetters.length > 0 && (
        <div className="mt-3 divide-y divide-border/60 overflow-hidden rounded-lg border border-border/70">
          {deadLetters.map((job, index) => (
            <div key={`${job.job_id || index}`} className="bg-background/45 px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-foreground">
                  {job.filename || job.document_id || "unknown job"}
                </span>
                <span className="shrink-0 font-mono text-[11px] text-destructive">
                  {job.attempt ? `#${job.attempt}` : "DLQ"}
                </span>
              </div>
              {job.last_error && (
                <div className="mt-1 truncate text-[11px] text-muted-foreground">
                  {job.last_error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function QualityOverview({
  evaluations,
  averageQuality,
  lowQualityCount,
  warningQualityCount,
  loading,
  error,
  onRefresh,
}: {
  evaluations: EvaluationResult[];
  averageQuality: number | null;
  lowQualityCount: number;
  warningQualityCount: number;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const passCount = evaluations.filter((item) => item.label === "pass").length;
  const recent = evaluations.slice(0, 4);
  const issueCounts = evaluations.reduce<Record<string, number>>((acc, item) => {
    for (const issue of item.issues || []) {
      acc[issue] = (acc[issue] || 0) + 1;
    }
    return acc;
  }, {});
  const commonIssues = Object.entries(issueCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);

  return (
    <section
      className="rounded-lg border border-border/70 bg-card/55 p-4"
      data-testid="knowledge-quality-summary"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">回答质量</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            最近问答会按事实支撑、回答相关、引用覆盖和检索质量进行评分。
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/70 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
          aria-label="刷新回答质量"
          disabled={loading}
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <QualityMetric
          label="平均质量"
          value={averageQuality == null ? "--" : formatPercent(averageQuality)}
          testId="knowledge-quality-average"
        />
        <QualityMetric label="通过" value={passCount.toString()} />
        <QualityMetric label="预警" value={warningQualityCount.toString()} />
        <QualityMetric label="失败" value={lowQualityCount.toString()} tone={lowQualityCount > 0 ? "bad" : "ok"} />
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {!error && loading && evaluations.length === 0 && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在读取质量评分
        </div>
      )}

      {!error && !loading && evaluations.length === 0 && (
        <div className="mt-3 rounded-md border border-dashed border-border/70 px-3 py-3 text-xs text-muted-foreground">
          暂无已评分回答。
        </div>
      )}

      {commonIssues.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {commonIssues.map(([issue, count]) => (
            <span
              key={issue}
              className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-300"
            >
              {qualityIssueLabel(issue)} x{count}
            </span>
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <div className="mt-3 divide-y divide-border/60 overflow-hidden rounded-lg border border-border/70">
          {recent.map((item) => (
            <div
              key={item.evaluation_id || `${item.query}-${item.created_at}`}
              className="grid gap-2 bg-background/45 px-3 py-2 text-xs sm:grid-cols-[minmax(0,1fr)_auto]"
              data-testid="knowledge-quality-row"
            >
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{item.query}</div>
                <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
                  <span>事实支撑 {formatPercent(item.groundedness)}</span>
                  <span>引用覆盖 {formatPercent(item.citation_coverage)}</span>
                  <span>检索质量 {formatPercent(item.retrieval_quality)}</span>
                </div>
              </div>
              <span
                className={cn(
                  "inline-flex h-7 items-center justify-center rounded-md border px-2 font-mono text-[11px]",
                  item.label === "pass" && "border-success/30 bg-success/10 text-success",
                  item.label === "warn" && "border-amber-400/30 bg-amber-400/10 text-amber-300",
                  item.label === "fail" && "border-destructive/30 bg-destructive/10 text-destructive",
                )}
              >
                {formatPercent(item.overall_score)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function QualityMetric({
  label,
  value,
  tone = "neutral",
  testId,
}: {
  label: string;
  value: string;
  tone?: "neutral" | "ok" | "bad";
  testId?: string;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-background/45 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-lg font-semibold",
          tone === "ok" && "text-success",
          tone === "bad" && "text-destructive",
          tone === "neutral" && "text-foreground",
        )}
        data-testid={testId}
      >
        {value}
      </div>
    </div>
  );
}

function formatPercent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border/70 bg-card/55 px-4 py-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function IndexStatusChips({ statuses }: { statuses: Record<string, string> }) {
  const items = [
    ["vector", "向量"],
    ["bm25", "BM25"],
    ["graph", "图谱"],
  ] as const;

  return (
    <>
      {items.map(([key, label]) => {
        const status = statuses?.[key] || "unknown";
        const statusText = indexStatusLabel(status);
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
            title={`${label}：${statusText}`}
          >
            {label}：{statusText}
          </span>
        );
      })}
    </>
  );
}

function indexStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队",
    processing: "处理中",
    ready: "就绪",
    skipped: "跳过",
    error: "失败",
    cancelled: "已取消",
    unknown: "未知",
  };
  return labels[status] || status;
}
