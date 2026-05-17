"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Database,
  Layers,
  Loader2,
  MessageSquare,
} from "lucide-react";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { UploadPanel } from "@/components/knowledge/UploadPanel";
import {
  TracePanel,
  type EvaluationState,
  type Trace,
} from "@/components/observability/TracePanel";
import { API_BASE, fetchApiHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

type Tab = "chat" | "knowledge" | "trace";

const tabs = [
  { id: "chat" as const, label: "Chat", icon: MessageSquare },
  { id: "knowledge" as const, label: "Knowledge", icon: Database },
  { id: "trace" as const, label: "Trace", icon: Activity },
];

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("chat");
  const [traces, setTraces] = useState<Trace[]>([]);
  const [evaluations, setEvaluations] = useState<Record<string, EvaluationState>>({});
  const [traceCount, setTraceCount] = useState(0);
  const [latestQueryId, setLatestQueryId] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<"checking" | "ok" | "error">("checking");
  const [apiError, setApiError] = useState("");

  const handleNewTrace = useCallback((trace: Trace) => {
    setTraces((prev) => [trace, ...prev].slice(0, 50));
    setTraceCount((n) => n + 1);
    setLatestQueryId(trace.query_id);
  }, []);

  const handleEvaluation = useCallback((queryId: string, evaluation: EvaluationState) => {
    setEvaluations((prev) => ({ ...prev, [queryId]: evaluation }));
  }, []);

  const refreshApiHealth = useCallback(async () => {
    setApiStatus("checking");
    setApiError("");
    try {
      await fetchApiHealth();
      setApiStatus("ok");
    } catch (error) {
      setApiStatus("error");
      setApiError(error instanceof Error ? error.message : "Cannot connect to backend API");
    }
  }, []);

  useEffect(() => {
    void refreshApiHealth();
  }, [refreshApiHealth]);

  return (
    <div
      className="h-screen flex flex-col bg-background text-foreground"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDrop={(e) => {
        if (e.dataTransfer.files.length) e.preventDefault();
      }}
    >
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-lg focus:text-sm"
      >
        Skip to main content
      </a>

      <header className="border-b border-border/80 px-5 py-3 flex items-center justify-between shrink-0 bg-background/92 backdrop-blur-sm">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-lg bg-primary/12 ring-1 ring-primary/25 flex items-center justify-center">
            <Layers className="w-5 h-5 text-primary" />
          </div>
          <div className="flex flex-col min-w-0">
            <span className="font-semibold text-sm leading-tight truncate">Graph RAG Platform</span>
            <span className="text-[11px] text-muted-foreground leading-tight truncate">
              Hybrid retrieval, graph grounding, and quality scoring
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void refreshApiHealth()}
            title={apiStatus === "error" ? apiError : `API: ${API_BASE}`}
            className={cn(
              "hidden h-8 items-center gap-1.5 rounded-md border px-2 text-[11px] font-medium transition-colors sm:inline-flex",
              apiStatus === "ok" && "border-success/30 bg-success/10 text-success",
              apiStatus === "checking" && "border-border/70 bg-card/60 text-muted-foreground",
              apiStatus === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
            )}
          >
            {apiStatus === "ok" && <CheckCircle2 className="h-3.5 w-3.5" />}
            {apiStatus === "checking" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {apiStatus === "error" && <AlertCircle className="h-3.5 w-3.5" />}
            <span>{apiStatus === "ok" ? "API OK" : apiStatus === "checking" ? "Checking" : "API Error"}</span>
          </button>

          <nav
            className="flex items-center gap-1 rounded-lg border border-border/70 bg-card/70 p-1"
            role="tablist"
            aria-label="Main navigation"
          >
            {tabs.map((tab) => {
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => {
                    setActiveTab(tab.id);
                    if (tab.id === "trace") setTraceCount(0);
                  }}
                  className={cn(
                    "relative flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors",
                    "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none",
                    isActive
                      ? "bg-primary/16 text-primary"
                      : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
                  )}
                >
                  <tab.icon className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>{tab.label}</span>
                  {tab.id === "trace" && traceCount > 0 && (
                    <span
                      className={cn(
                        "ml-0.5 min-w-4 rounded-full px-1 text-[10px] leading-4",
                        isActive ? "bg-primary/20 text-primary" : "bg-accent text-accent-foreground",
                      )}
                    >
                      {traceCount > 99 ? "99+" : traceCount}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>
      </header>

      <main id="main-content" className="flex-1 flex overflow-hidden">
        <div className={cn("flex-1 flex flex-col", activeTab !== "chat" && "hidden")} role="tabpanel" aria-label="Chat">
          <ChatPanel onTrace={handleNewTrace} onEvaluation={handleEvaluation} />
        </div>
        <div className={cn("flex-1 overflow-y-auto", activeTab !== "knowledge" && "hidden")} role="tabpanel" aria-label="Knowledge base">
          <UploadPanel traces={traces} evaluations={evaluations} />
        </div>
        <div className={cn("flex-1 overflow-y-auto", activeTab !== "trace" && "hidden")} role="tabpanel" aria-label="Trace">
          <TracePanel traces={traces} latestQueryId={latestQueryId} evaluations={evaluations} />
        </div>
      </main>
    </div>
  );
}
