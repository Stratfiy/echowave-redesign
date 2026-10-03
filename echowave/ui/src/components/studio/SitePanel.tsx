"use client";

/**
 * One Studio site: its preview, its files, its agents, and the buttons to
 * rebuild and download it.
 *
 * The preview is an iframe with `sandbox` set and no `allow-same-origin`.
 * The api already serves the page under a CSP sandbox (routes/studio.py);
 * the attribute is the same rule enforced by this page too, so a generated
 * site can never script the app it is shown inside.
 */

import {
    Bot,
    Download,
    ExternalLink,
    FileCode2,
    Hammer,
    Loader2,
    MonitorSmartphone,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { type Site, studioApi } from "./api";

const STATUS_WORDS: Record<Site["build_status"], string> = {
    none: "Not built yet",
    building: "Building…",
    succeeded: "Built",
    failed: "Last build failed",
};

export function SitePanel({
    site,
    onChanged,
}: {
    site: Site;
    /** Called after a rebuild, so the screen reloads the site. */
    onChanged: () => void;
}) {
    const [building, setBuilding] = useState(false);
    const [buildError, setBuildError] = useState<string | null>(null);
    const [openPath, setOpenPath] = useState<string | null>(null);
    const [content, setContent] = useState<string>("");
    const [fileError, setFileError] = useState<string | null>(null);
    const [downloadError, setDownloadError] = useState<string | null>(null);

    useEffect(() => {
        setOpenPath(null);
        setContent("");
        setBuildError(null);
    }, [site.id]);

    const openFile = useCallback(
        async (path: string) => {
            setOpenPath(path);
            setFileError(null);
            setContent("");
            const result = await studioApi.readFile(site.id, path);
            if (result.error !== undefined) {
                setFileError(result.error);
                return;
            }
            setContent(result.data.content);
        },
        [site.id],
    );

    const rebuild = async () => {
        setBuilding(true);
        setBuildError(null);
        const result = await studioApi.build(site.id);
        setBuilding(false);
        if (result.error !== undefined) {
            setBuildError(result.error);
        } else if (result.data.status === "failed") {
            setBuildError(result.data.errors || "The build failed.");
        }
        onChanged();
    };

    const download = async () => {
        setDownloadError(null);
        const result = await studioApi.download(site.id);
        if (result.error !== undefined) {
            setDownloadError(result.error);
            return;
        }
        const url = URL.createObjectURL(result.data);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${site.name.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "site"}.zip`;
        link.click();
        URL.revokeObjectURL(url);
    };

    const failedLog =
        buildError ?? (site.build_status === "failed" ? site.build_log ?? null : null);

    return (
        <div className="flex min-h-[420px] min-w-0 flex-col rounded-xl border border-border bg-card lg:h-[calc(100vh-11rem)]">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
                <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{site.name}</p>
                    <p
                        className={cn(
                            "text-xs",
                            site.build_status === "failed"
                                ? "text-destructive"
                                : "text-muted-foreground",
                        )}
                    >
                        {building ? "Building…" : STATUS_WORDS[site.build_status]}
                        {site.build_seconds && site.build_status === "succeeded"
                            ? ` in ${Math.round(site.build_seconds)}s`
                            : ""}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={rebuild} disabled={building}>
                        {building ? (
                            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                        ) : (
                            <Hammer className="mr-1.5 h-4 w-4" />
                        )}
                        Rebuild
                    </Button>
                    <Button size="sm" variant="outline" onClick={download}>
                        <Download className="mr-1.5 h-4 w-4" />
                        Download
                    </Button>
                    {site.preview_url ? (
                        <Button size="sm" variant="outline" asChild>
                            <a href={site.preview_url} target="_blank" rel="noopener noreferrer">
                                <ExternalLink className="mr-1.5 h-4 w-4" />
                                Open
                            </a>
                        </Button>
                    ) : null}
                </div>
            </div>

            {downloadError ? (
                <p role="alert" className="px-4 pt-2 text-sm text-destructive">
                    {downloadError}
                </p>
            ) : null}

            <Tabs defaultValue="preview" className="flex min-h-0 flex-1 flex-col px-4 py-3">
                <TabsList className="self-start">
                    <TabsTrigger value="preview">
                        <MonitorSmartphone className="mr-1.5 h-4 w-4" />
                        Preview
                    </TabsTrigger>
                    <TabsTrigger value="files">
                        <FileCode2 className="mr-1.5 h-4 w-4" />
                        Files
                    </TabsTrigger>
                    <TabsTrigger value="agents">
                        <Bot className="mr-1.5 h-4 w-4" />
                        Agents
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="preview" className="mt-3 flex min-h-0 flex-1 flex-col">
                    {failedLog ? (
                        <pre className="mb-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
                            {failedLog}
                        </pre>
                    ) : null}
                    {site.preview_url ? (
                        <iframe
                            // A new build is a new page; the key reloads it.
                            key={site.built_at ?? "none"}
                            title={`Preview of ${site.name}`}
                            src={site.preview_url}
                            sandbox="allow-scripts allow-forms allow-popups allow-modals"
                            className="min-h-[360px] w-full flex-1 rounded-md border border-border bg-white"
                        />
                    ) : (
                        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                            {site.build_status === "failed"
                                ? "The site has not built yet. Ask Studio to fix the errors above."
                                : "Nothing to show yet. Ask Studio to build the site, or press Rebuild."}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="files" className="mt-3 min-h-0 flex-1">
                    <div className="grid h-full min-h-0 gap-3 md:grid-cols-[220px_1fr]">
                        <ul className="max-h-60 overflow-auto rounded-md border border-border text-sm md:max-h-none">
                            {(site.files ?? []).map((file) => (
                                <li key={file.path}>
                                    <button
                                        type="button"
                                        onClick={() => void openFile(file.path)}
                                        className={cn(
                                            "w-full truncate px-3 py-1.5 text-left font-mono text-xs hover:bg-muted",
                                            openPath === file.path && "bg-muted",
                                        )}
                                        title={`${file.path} · ${file.bytes} bytes`}
                                    >
                                        {file.path}
                                    </button>
                                </li>
                            ))}
                        </ul>
                        <div className="min-h-[240px] min-w-0 overflow-auto rounded-md border border-border bg-muted/30">
                            {fileError ? (
                                <p role="alert" className="p-3 text-sm text-destructive">
                                    {fileError}
                                </p>
                            ) : openPath ? (
                                <pre className="p-3 font-mono text-xs leading-relaxed">
                                    {content}
                                </pre>
                            ) : (
                                <p className="p-3 text-sm text-muted-foreground">
                                    Pick a file to read it. To change one, ask in the chat.
                                </p>
                            )}
                        </div>
                    </div>
                </TabsContent>

                <TabsContent value="agents" className="mt-3">
                    {site.agent_workflow_ids.length > 0 ? (
                        <ul className="space-y-2">
                            {site.agent_workflow_ids.map((id) => (
                                <li key={id}>
                                    <Link
                                        href={`/workflow/${id}`}
                                        className="inline-flex items-center gap-2 text-sm underline-offset-2 hover:underline"
                                    >
                                        <Bot className="h-4 w-4" />
                                        Agent {id}
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="text-sm text-muted-foreground">
                            No agents on this site yet. Ask Studio to put one on it, and
                            say which domain the site will live on.
                        </p>
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
}
