"use client";

/**
 * One Studio site, as a canvas beside the chat: the preview in a browser
 * frame you can size to a phone, a tablet or a desktop, the code, and the
 * team of agents that work on it.
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
    Monitor,
    MonitorSmartphone,
    RefreshCw,
    Smartphone,
    Tablet,
    Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { type Site, type StudioAgent, studioApi } from "./api";

const STATUS: Record<Site["build_status"], { words: string; tone: string }> = {
    none: { words: "Not built yet", tone: "bg-muted text-muted-foreground" },
    building: { words: "Building…", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
    succeeded: { words: "Live preview", tone: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
    failed: { words: "Last build failed", tone: "bg-destructive/15 text-destructive" },
};

type Device = "desktop" | "tablet" | "phone";

const DEVICES: { id: Device; label: string; icon: typeof Monitor; width: string }[] = [
    { id: "desktop", label: "Desktop", icon: Monitor, width: "100%" },
    { id: "tablet", label: "Tablet", icon: Tablet, width: "768px" },
    { id: "phone", label: "Phone", icon: Smartphone, width: "390px" },
];

function IconButton({
    label,
    onClick,
    disabled,
    children,
}: {
    label: string;
    onClick?: () => void;
    disabled?: boolean;
    children: React.ReactNode;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            title={label}
            aria-label={label}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
        >
            {children}
        </button>
    );
}

function TeamCard({ agent }: { agent: StudioAgent }) {
    const initials = agent.name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0]?.toUpperCase())
        .join("");
    const state = agent.archived
        ? { words: "Archived", dot: "bg-muted-foreground/40" }
        : agent.live
          ? { words: "Live", dot: "bg-emerald-500" }
          : { words: "Paused", dot: "bg-amber-500" };
    return (
        <li className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                {initials || <Bot className="h-4 w-4" />}
            </span>
            <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{agent.name}</p>
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span className={cn("h-1.5 w-1.5 rounded-full", state.dot)} />
                    {state.words} · works on this site
                </p>
            </div>
            <Link
                href={`/workflow/${agent.id}`}
                className="rounded-full border border-border px-3 py-1 text-xs hover:bg-muted"
            >
                Open
            </Link>
        </li>
    );
}

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
    const [device, setDevice] = useState<Device>("desktop");
    const [reloads, setReloads] = useState(0);

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
    const status = building ? STATUS.building : STATUS[site.build_status];
    const team: StudioAgent[] =
        site.agents ??
        site.agent_workflow_ids.map((id) => ({
            id,
            name: `Agent ${id}`,
            live: true,
            archived: false,
        }));
    const width = DEVICES.find((d) => d.id === device)?.width ?? "100%";

    return (
        <div className="flex min-h-[480px] min-w-0 flex-col overflow-hidden rounded-3xl border border-border bg-card lg:h-full">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-2.5">
                    <p className="truncate text-sm font-semibold">{site.name}</p>
                    <span
                        className={cn(
                            "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
                            status.tone,
                        )}
                    >
                        {status.words}
                        {!building && site.build_seconds && site.build_status === "succeeded"
                            ? ` · built in ${Math.round(site.build_seconds)}s`
                            : ""}
                    </span>
                </div>
                <div className="flex flex-wrap items-center gap-0.5">
                    <IconButton label="Rebuild" onClick={rebuild} disabled={building}>
                        {building ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <Hammer className="h-3.5 w-3.5" />
                        )}
                        Rebuild
                    </IconButton>
                    <IconButton label="Download the code" onClick={download}>
                        <Download className="h-3.5 w-3.5" />
                        Download
                    </IconButton>
                    {site.preview_url ? (
                        <a
                            href={site.preview_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                            <ExternalLink className="h-3.5 w-3.5" />
                            Open
                        </a>
                    ) : null}
                </div>
            </div>

            {downloadError ? (
                <p role="alert" className="px-4 pt-2 text-sm text-destructive">
                    {downloadError}
                </p>
            ) : null}

            <Tabs defaultValue="preview" className="flex min-h-0 flex-1 flex-col px-3 py-3 sm:px-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <TabsList className="rounded-full">
                        <TabsTrigger value="preview" className="rounded-full">
                            <MonitorSmartphone className="mr-1.5 h-4 w-4" />
                            Preview
                        </TabsTrigger>
                        <TabsTrigger value="files" className="rounded-full">
                            <FileCode2 className="mr-1.5 h-4 w-4" />
                            Code
                        </TabsTrigger>
                        <TabsTrigger value="agents" className="rounded-full">
                            <Users className="mr-1.5 h-4 w-4" />
                            Team
                            {team.length > 0 ? (
                                <span className="ml-1.5 rounded-full bg-primary/10 px-1.5 text-[10px] text-primary">
                                    {team.length}
                                </span>
                            ) : null}
                        </TabsTrigger>
                    </TabsList>
                </div>

                <TabsContent value="preview" className="mt-3 flex min-h-0 flex-1 flex-col">
                    {failedLog ? (
                        <pre className="mb-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-2xl border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
                            {failedLog}
                        </pre>
                    ) : null}
                    {site.preview_url ? (
                        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-muted/40">
                            <div className="flex items-center gap-2 border-b border-border bg-background/70 px-3 py-2">
                                <span className="hidden gap-1.5 sm:flex" aria-hidden>
                                    <span className="h-2.5 w-2.5 rounded-full bg-red-400/70" />
                                    <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
                                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
                                </span>
                                <span className="min-w-0 flex-1 truncate rounded-full bg-muted px-3 py-1 text-center font-mono text-[11px] text-muted-foreground">
                                    {site.preview_url.replace(/^https?:\/\//, "")}
                                </span>
                                <div
                                    role="group"
                                    aria-label="Preview size"
                                    className="flex items-center rounded-full bg-muted p-0.5"
                                >
                                    {DEVICES.map((d) => (
                                        <button
                                            key={d.id}
                                            type="button"
                                            aria-label={d.label}
                                            aria-pressed={device === d.id}
                                            title={d.label}
                                            onClick={() => setDevice(d.id)}
                                            className={cn(
                                                "inline-flex h-6 w-7 items-center justify-center rounded-full text-muted-foreground",
                                                device === d.id && "bg-background text-foreground shadow-sm",
                                            )}
                                        >
                                            <d.icon className="h-3.5 w-3.5" />
                                        </button>
                                    ))}
                                </div>
                                <button
                                    type="button"
                                    aria-label="Reload preview"
                                    title="Reload preview"
                                    onClick={() => setReloads((n) => n + 1)}
                                    className="inline-flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                                >
                                    <RefreshCw className="h-3.5 w-3.5" />
                                </button>
                            </div>
                            <div className="flex min-h-[360px] flex-1 justify-center overflow-auto p-0 sm:p-3">
                                <iframe
                                    // A new build is a new page; the key reloads it.
                                    key={`${site.built_at ?? "none"}-${reloads}`}
                                    title={`Preview of ${site.name}`}
                                    src={site.preview_url}
                                    sandbox="allow-scripts allow-forms allow-popups allow-modals"
                                    style={{ width, maxWidth: "100%" }}
                                    className={cn(
                                        "min-h-[360px] flex-1 bg-white transition-[width] duration-300",
                                        device !== "desktop" && "flex-none rounded-xl border border-border shadow-sm",
                                    )}
                                />
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border p-6 text-center">
                            <MonitorSmartphone className="h-8 w-8 text-muted-foreground/60" />
                            <p className="max-w-sm text-sm text-muted-foreground">
                                {site.build_status === "failed"
                                    ? "The site has not built yet. Ask Studio to fix the errors above."
                                    : "Nothing to show yet. Ask Studio to build the site, or press Rebuild."}
                            </p>
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="files" className="mt-3 min-h-0 flex-1">
                    <div className="grid h-full min-h-0 gap-3 md:grid-cols-[220px_1fr]">
                        <ul className="max-h-60 overflow-auto rounded-2xl border border-border py-1 text-sm md:max-h-none">
                            {(site.files ?? []).map((file) => (
                                <li key={file.path}>
                                    <button
                                        type="button"
                                        onClick={() => void openFile(file.path)}
                                        className={cn(
                                            "w-full truncate px-3 py-1.5 text-left font-mono text-xs hover:bg-muted",
                                            openPath === file.path && "bg-muted text-foreground",
                                        )}
                                        title={`${file.path} · ${file.bytes} bytes`}
                                    >
                                        {file.path}
                                    </button>
                                </li>
                            ))}
                        </ul>
                        <div className="min-h-[240px] min-w-0 overflow-auto rounded-2xl border border-border bg-muted/30">
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
                    {team.length > 0 ? (
                        <ul className="grid gap-2 sm:grid-cols-2">
                            {team.map((agent) => (
                                <TeamCard key={agent.id} agent={agent} />
                            ))}
                        </ul>
                    ) : (
                        <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border p-6 text-center">
                            <Users className="h-8 w-8 text-muted-foreground/60" />
                            <p className="max-w-sm text-sm text-muted-foreground">
                                No agents on this site yet. Ask Studio to put one on it, and
                                say which domain the site will live on.
                            </p>
                        </div>
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
}
