"use client";

/**
 * One app as a row on the shelf: logo, name, one line, and Add. The way a
 * plugin marketplace lists them -- dense enough that a category is a glance,
 * not a scroll. The card (ConnectorCard) stays for the long-tail page; this
 * is the shelf's own shape, and the connect flow is the same call.
 */

import { Check, ChevronDown, ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
    listAppToolsApiV1ConnectorsSlugToolsGet,
    startConnectingApiV1ConnectorsSlugConnectPost,
    syncAppToolsApiV1ConnectorsSlugToolsSyncPost,
} from "@/client/sdk.gen";
import type { AppTool, ConnectorResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { toneFor } from "@/lib/marketplace";
import { cn } from "@/lib/utils";

/** What the muted word says for an app that is not one click. */
const SETUP_WORD: Record<string, string> = {
    api_key: "Needs your key",
    needs_approval: "Ask us",
    no_auth: "No setup",
};

export function ConnectorLogo({ connector, className }: { connector: Pick<ConnectorResponse, "name" | "logo">; className?: string }) {
    const [broken, setBroken] = useState(false);
    if (connector.logo && !broken) {
        // A vendor's logo from Composio's CDN: plain <img>, because next/image
        // needs every host allowlisted and there are hundreds of them.
        return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
                src={connector.logo}
                alt=""
                aria-hidden="true"
                onError={() => setBroken(true)}
                className={cn("h-10 w-10 shrink-0 rounded-lg object-contain", className)}
            />
        );
    }
    // No logo, or one that would not load: the app's initial on a tile in
    // its own steady colour, so a shelf of them still reads as a shelf
    // rather than a column of grey squares.
    return (
        <span
            aria-hidden="true"
            className={cn(
                "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-sm font-semibold",
                toneFor(connector.name),
                className,
            )}
        >
            {connector.name.slice(0, 1).toUpperCase()}
        </span>
    );
}

/**
 * What an app can be asked to do, one line each.
 *
 * A number in the corner ("9 tools") tells somebody nothing about whether
 * connecting Gmail will let a bot send mail or only read it. The list is
 * the vendor's own, fetched when the row is opened rather than with the
 * screen: a catalogue of nine hundred apps that fetched every app's
 * actions would be nine hundred requests for a list nobody opened.
 *
 * For an app that is already connected, opening the list is also when the
 * tool rows get made (see services/integrations/composio/tool_sync.py) --
 * connecting an app and being able to point a bot at it should not be two
 * jobs a person has to know about.
 */
function AppTools({ connector }: { connector: ConnectorResponse }) {
    const [open, setOpen] = useState(false);
    const [tools, setTools] = useState<AppTool[] | null>(null);
    const [loading, setLoading] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        if (open) {
            setOpen(false);
            return;
        }
        setOpen(true);
        if (tools !== null) return;
        setLoading(true);
        setError(null);
        if (connector.connected) {
            // Safe to call twice: it creates only what is missing. An
            // account without permission to write them still gets the list.
            const synced = await syncAppToolsApiV1ConnectorsSlugToolsSyncPost({
                path: { slug: connector.slug },
            });
            const created = synced.data?.created ?? 0;
            if (created > 0) {
                setNote(`${created} ${created === 1 ? "tool" : "tools"} added to Your tools`);
            }
        }
        const result = await listAppToolsApiV1ConnectorsSlugToolsGet({
            path: { slug: connector.slug },
        });
        setLoading(false);
        if (result.error) {
            setError(detailFromResult(result, `Could not read what ${connector.name} can do`));
            return;
        }
        if (result.data?.error) {
            setError(result.data.error);
            return;
        }
        setTools(result.data?.tools ?? []);
    };

    return (
        <div className="pl-[52px]">
            <button
                type="button"
                onClick={() => void load()}
                aria-expanded={open}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                data-testid="connector-tools-toggle"
            >
                <ChevronDown
                    aria-hidden="true"
                    className={cn("h-3 w-3 transition-transform", open && "rotate-180")}
                />
                {connector.tools_count > 0
                    ? `${connector.tools_count} ${connector.tools_count === 1 ? "tool" : "tools"}`
                    : "What it can do"}
            </button>
            {open && (
                <div className="mt-1 pb-1">
                    {loading && (
                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
                            Reading…
                        </p>
                    )}
                    {error && <p className="text-xs text-destructive">{error}</p>}
                    {note && <p className="text-xs text-emerald-700">{note}</p>}
                    {tools !== null && tools.length === 0 && !error && (
                        <p className="text-xs text-muted-foreground">
                            This app brings no tools a bot can be given.
                        </p>
                    )}
                    <ul className="space-y-0.5" data-testid="connector-tool-list">
                        {(tools ?? []).map((tool) => (
                            <li key={tool.slug} className="text-xs">
                                <span className="font-medium">{tool.name}</span>
                                {tool.does && (
                                    <span className="text-muted-foreground"> — {tool.does}</span>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

export function ConnectorRow({
    connector,
    onConnected,
}: {
    connector: ConnectorResponse;
    onConnected: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const connect = async () => {
        setBusy(true);
        setError(null);
        try {
            const response = await startConnectingApiV1ConnectorsSlugConnectPost({
                path: { slug: connector.slug },
            });
            // The generated client resolves on a 4xx rather than throwing, so
            // the catch below never saw a refusal: an admin-only connect read
            // as "could not start connecting just now" for every member.
            if (response.error) {
                setError(detailFromResult(response, "Could not start connecting just now."));
                return;
            }
            const url = response.data?.connect_url;
            if (!url) {
                setError("Could not start connecting just now.");
                return;
            }
            window.open(url, "_blank", "noopener");
            onConnected();
        } catch {
            setError("Could not start connecting just now.");
        } finally {
            setBusy(false);
        }
    };

    const canAdd = !connector.connected && (connector.setup === "one_click" || connector.also_connectable);

    return (
        <div className="rounded-xl px-2 py-2 hover:bg-muted/40" data-testid="connector-row">
        <div className="flex items-center gap-3">
            <ConnectorLogo connector={connector} />
            <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{connector.name}</p>
                <p className="truncate text-xs text-muted-foreground" title={connector.description}>
                    {connector.description || SETUP_WORD[connector.setup] || ""}
                </p>
                {error ? <p className="text-xs text-destructive">{error}</p> : null}
            </div>
            {connector.connected ? (
                <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-emerald-700">
                    <Check className="h-3.5 w-3.5" aria-hidden="true" />
                    Added
                </span>
            ) : connector.setup_url ? (
                <Button asChild size="sm" variant="outline" className="shrink-0 rounded-full">
                    <Link href={connector.setup_url}>Set up</Link>
                </Button>
            ) : canAdd ? (
                <Button
                    size="sm"
                    variant="outline"
                    className="shrink-0 rounded-full"
                    onClick={connect}
                    disabled={busy}
                    aria-label={`Add ${connector.name}`}
                >
                    {busy ? "Opening…" : "Add"}
                    {busy ? null : <ExternalLink className="ml-1 h-3 w-3" aria-hidden="true" />}
                </Button>
            ) : (
                <span className="shrink-0 text-xs text-muted-foreground">
                    {SETUP_WORD[connector.setup] ?? connector.setup}
                </span>
            )}
        </div>
        {/* A vendor we set up ourselves has no Composio actions to list. */}
        {connector.setup_url ? null : <AppTools connector={connector} />}
        </div>
    );
}
