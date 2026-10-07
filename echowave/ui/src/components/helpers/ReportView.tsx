"use client";

/**
 * A saved research report (handoff 6, Research; screen 04 "A saved report
 * matches its export"). Findings keep their basis -- from a source, or
 * Decibyl's inference -- with the sources they rest on; disagreement and
 * the sources that could not be read are shown, never dropped. Export
 * downloads the server's own rendering, the same text whose hash is shown
 * here, so what is downloaded is what was read.
 */

import { ArrowLeft, Download, Loader2, Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    exportReportApiV1HelpersReportsReportUuidExportGet,
    getReportApiV1HelpersReportsReportUuidGet,
    listReportsApiV1HelpersReportsGet,
    shareReportApiV1HelpersReportsReportUuidVisibilityPut,
} from "@/client/sdk.gen";
import type { ReportOut, ReportSummary } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { downloadText } from "@/lib/helpers";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; data: T };

function Cites({ numbers }: { numbers: number[] }) {
    if (!numbers.length) return null;
    return (
        <span className="text-muted-foreground">
            {numbers.map((n) => (
                <a key={n} href={`#source-${n}`} className="ml-1 underline">
                    [{n}]
                </a>
            ))}
        </span>
    );
}

export function ReportView({ uuid }: { uuid: string }) {
    const { user, loading: authLoading } = useAuth();
    const [load, setLoad] = useState<Load<ReportOut>>({ state: "loading" });
    const [busy, setBusy] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);

    const fetchReport = useCallback(async () => {
        setLoad({ state: "loading" });
        const response = await getReportApiV1HelpersReportsReportUuidGet({ path: { report_uuid: uuid } });
        if (response.error || !response.data) {
            setLoad({ state: "failed", message: detailFromResult(response, "The report did not load.") });
            return;
        }
        setLoad({ state: "ready", data: response.data });
    }, [uuid]);

    useEffect(() => {
        if (authLoading || !user) return;
        void fetchReport();
    }, [authLoading, user, fetchReport]);

    if (load.state === "loading") {
        return (
            <p className="flex items-center gap-2 px-4 py-8 text-sm text-muted-foreground" role="status">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading the report…
            </p>
        );
    }
    if (load.state === "failed") {
        return <ErrorState title="The report did not load" description={load.message} onRetry={() => void fetchReport()} />;
    }
    const report = load.data;

    const exportAs = async (format: "md" | "html") => {
        setBusy(format);
        setNote(null);
        const response = await exportReportApiV1HelpersReportsReportUuidExportGet({
            path: { report_uuid: uuid },
            query: { format },
            parseAs: "text",
        });
        setBusy(null);
        if (response.error) {
            setNote(detailFromResult(response, "The export did not download. Try again."));
            return;
        }
        const name = `${report.title.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "report"}-${report.content_hash.slice(0, 8)}.${format}`;
        downloadText(String(response.data ?? ""), name, format === "md" ? "text/markdown" : "text/html");
    };

    const share = async () => {
        setBusy("share");
        const response = await shareReportApiV1HelpersReportsReportUuidVisibilityPut({
            path: { report_uuid: uuid },
            body: { visibility: report.visibility === "workspace" ? "private" : "workspace" },
        });
        setBusy(null);
        if (response.error || !response.data) {
            setNote(detailFromResult(response, "Sharing did not change. Try again."));
            return;
        }
        setLoad({ state: "ready", data: response.data });
    };

    return (
        <article className="mx-auto w-full max-w-[760px] space-y-6 px-4 py-4 sm:px-6" data-testid="report-view">
            <Link href="/saved-reports" className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                <ArrowLeft aria-hidden className="h-4 w-4" /> Saved reports
            </Link>
            <header className="space-y-2">
                <h1 className="text-2xl font-semibold leading-8 break-words">{report.title}</h1>
                {report.question && <p className="text-muted-foreground break-words">Question: {report.question}</p>}
                {report.notice && (
                    <p className="rounded-md border border-border px-3 py-2 text-sm" role="note">
                        {report.notice}
                    </p>
                )}
                <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" className="min-h-11" disabled={!!busy} onClick={() => void exportAs("md")}>
                        {busy === "md" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Download aria-hidden className="h-4 w-4" />}
                        Export Markdown
                    </Button>
                    <Button type="button" variant="outline" className="min-h-11" disabled={!!busy} onClick={() => void exportAs("html")}>
                        <Download aria-hidden className="h-4 w-4" /> Export web page
                    </Button>
                    {report.mine && (
                        <Button type="button" variant="outline" className="min-h-11" disabled={!!busy} onClick={() => void share()}>
                            <Users aria-hidden className="h-4 w-4" />
                            {report.visibility === "workspace" ? "Make private" : "Share with workspace"}
                        </Button>
                    )}
                </div>
                <p className="text-xs text-muted-foreground">
                    {report.visibility === "workspace" ? "Shared with your workspace" : "Private to you"} · Version{" "}
                    <span className="font-mono">{report.content_hash.slice(0, 8)}</span> (the export carries the same)
                </p>
                {note && (
                    <p className="text-sm text-destructive" role="status">
                        {note}
                    </p>
                )}
            </header>
            {report.summary && (
                <section>
                    <h2 className="text-lg font-semibold">Summary</h2>
                    <p className="whitespace-pre-wrap break-words leading-[26px]">{report.summary}</p>
                </section>
            )}
            {report.findings.length > 0 && (
                <section>
                    <h2 className="text-lg font-semibold">Findings</h2>
                    <ul className="mt-2 space-y-2" data-testid="report-findings">
                        {report.findings.map((f, i) => (
                            <li key={i} className="break-words leading-[26px]">
                                <span className="mr-1 rounded border border-border px-1.5 py-0.5 text-xs">
                                    {f.basis === "source" ? "Source" : "Inference"}
                                </span>
                                {f.statement}
                                {f.as_of && <span className="text-muted-foreground"> (as of {f.as_of})</span>}
                                <Cites numbers={f.sources ?? []} />
                            </li>
                        ))}
                    </ul>
                </section>
            )}
            {report.conflicts.length > 0 && (
                <section>
                    <h2 className="text-lg font-semibold">Where sources disagree</h2>
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                        {report.conflicts.map((c, i) => (
                            <li key={i} className="break-words">
                                {c.statement}
                                <Cites numbers={c.sources ?? []} />
                            </li>
                        ))}
                    </ul>
                </section>
            )}
            {report.inaccessible.length > 0 && (
                <section>
                    <h2 className="text-lg font-semibold">Sources that could not be read</h2>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                        {report.inaccessible.map((c, i) => (
                            <li key={i} className="break-all">
                                {c.url} -- {c.reason}
                            </li>
                        ))}
                    </ul>
                </section>
            )}
            {report.sources.length > 0 && (
                <section>
                    <h2 className="text-lg font-semibold">Sources</h2>
                    <ol className="mt-2 space-y-1 text-sm">
                        {report.sources.map((s) => (
                            <li key={s.n} id={`source-${s.n}`} className="break-all">
                                [{s.n}]{" "}
                                <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline">
                                    {s.title || s.url}
                                </a>
                                {s.accessed && <span className="text-muted-foreground"> (read {s.accessed})</span>}
                            </li>
                        ))}
                    </ol>
                </section>
            )}
            {report.thread_id !== undefined && (
                <Link
                    href={report.thread_id ? `/overview?thread=${encodeURIComponent(report.thread_id)}` : "/overview"}
                    className="inline-flex min-h-11 items-center text-sm underline"
                >
                    Open the conversation
                </Link>
            )}
        </article>
    );
}

export function ReportList() {
    const { user, loading: authLoading } = useAuth();
    const [load, setLoad] = useState<Load<ReportSummary[]>>({ state: "loading" });
    const fetchList = useCallback(async () => {
        setLoad({ state: "loading" });
        const response = await listReportsApiV1HelpersReportsGet();
        if (response.error || !response.data) {
            setLoad({ state: "failed", message: detailFromResult(response, "Reports did not load.") });
            return;
        }
        setLoad({ state: "ready", data: response.data });
    }, []);
    useEffect(() => {
        if (authLoading || !user) return;
        void fetchList();
    }, [authLoading, user, fetchList]);
    if (load.state === "loading") return <p className="px-4 py-8 text-sm text-muted-foreground" role="status">Loading reports…</p>;
    if (load.state === "failed") return <ErrorState title="Reports did not load" description={load.message} onRetry={() => void fetchList()} />;
    if (!load.data.length) {
        return (
            <EmptyState
                title="No saved reports yet."
                description="Ask Research a question in Chat and keep the answer with Save as report."
                action={
                    <Link className="underline" href="/overview?helper=research">
                        Open Chat with Research
                    </Link>
                }
            />
        );
    }
    return (
        <ul className="mx-auto w-full max-w-[760px] divide-y divide-border px-4 sm:px-6" aria-label="Saved reports">
            {load.data.map((r) => (
                <li key={r.uuid}>
                    <Link href={`/saved-reports/${r.uuid}`} className="flex min-h-11 flex-col py-3 hover:underline">
                        <span className="font-medium break-words">{r.title}</span>
                        <span className="text-xs text-muted-foreground">
                            {r.kind === "trading_summary" ? "Trading summary · " : ""}
                            {r.visibility === "workspace" ? (r.mine ? "Shared by you" : "Shared with you") : "Private"}
                            {r.created_at ? ` · ${new Date(r.created_at).toLocaleDateString()}` : ""}
                        </span>
                    </Link>
                </li>
            ))}
        </ul>
    );
}
