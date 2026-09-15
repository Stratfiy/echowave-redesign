"use client";

/**
 * One app as a row on the shelf: logo, name, one line, and Add. The way a
 * plugin marketplace lists them -- dense enough that a category is a glance,
 * not a scroll. The card (ConnectorCard) stays for the long-tail page; this
 * is the shelf's own shape, and the connect flow is the same call.
 */

import { Check, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { startConnectingApiV1ConnectorsSlugConnectPost } from "@/client/sdk.gen";
import type { ConnectorResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
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
        <div className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-muted/40" data-testid="connector-row">
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
    );
}
