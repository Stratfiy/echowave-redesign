"use client";

/**
 * One app in the catalogue, with the truth about how hard it is to turn on.
 *
 * Extracted so the main screen and the long-tail page render an identical
 * card. Two copies would drift, and the thing that would drift first is the
 * setup badge -- the part that stops a catalogue where a one-click app and one
 * we have not registered yet look the same. That is the expensive kind of lie,
 * because the business finds out after choosing rather than before.
 */

import { ArrowRight, Check, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { startConnectingApiV1ConnectorsSlugConnectPost } from "@/client/sdk.gen";
import type { ConnectorResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** What the badge says, in the words of the work the operator has to do. */
const SETUP_LABELS: Record<string, string> = {
    one_click: "One click",
    no_auth: "No setup",
    api_key: "Needs your key",
    needs_approval: "Ask us",
    ours: "We run this",
};

const SETUP_HINTS: Record<string, string> = {
    one_click: "Sign in and it is connected.",
    no_auth: "Nothing to connect.",
    api_key: "Paste a key from that app. We will add a screen for this next.",
    needs_approval:
        "We have to register with this provider before anyone can connect it. Tell us you want it and it moves up the list.",
    ours: "Decibyl connects to this itself. Set it up on its own screen — a key pasted here is not read by any call.",
};

export function ConnectorCard({
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
            // A new tab rather than a redirect: the operator is mid-way through
            // setting an agent up, and sending them to Google and back would
            // lose whatever else they had open on this screen.
            window.open(url, "_blank", "noopener");
            onConnected();
        } catch {
            setError("Could not start connecting just now.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Card className="flex flex-col">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-sm font-medium">{connector.name}</CardTitle>
                    {connector.connected ? (
                        <Badge variant="secondary" className="shrink-0 gap-1">
                            <Check className="h-3 w-3" />
                            Connected
                        </Badge>
                    ) : (
                        <Badge variant="outline" className="shrink-0">
                            {SETUP_LABELS[connector.setup] ?? connector.setup}
                        </Badge>
                    )}
                </div>
                {connector.description ? (
                    <CardDescription className="line-clamp-2 text-xs">
                        {connector.description}
                    </CardDescription>
                ) : null}
            </CardHeader>
            <CardContent className="mt-auto space-y-2 pt-0">
                {connector.connected ? (
                    <p className="text-xs text-muted-foreground">
                        Ready to use in an agent&rsquo;s tools and after-call steps.
                    </p>
                ) : (
                    <p className="text-xs text-muted-foreground">
                        {SETUP_HINTS[connector.setup]}
                    </p>
                )}

                {/* The native screen, and it shows even when this vendor is
                    already connected here as a tool. A connected Plivo saying
                    only "ready to use in an agent's tools" is how somebody
                    concludes their phone line is live: it is a true sentence
                    about the wrong thing. */}
                {connector.setup_url ? (
                    <Button asChild size="sm" variant="secondary" className="w-full">
                        <Link href={connector.setup_url}>
                            Set up {connector.name}
                            <ArrowRight className="ml-1 h-3 w-3" />
                        </Link>
                    </Button>
                ) : null}

                {/* Kept below the native route rather than removed. Composio's
                    toolkit for one of these vendors is a real capability --
                    an agent sending an SMS from the customer's own Plivo --
                    and it is simply not what somebody searching for it here
                    is trying to do. */}
                {!connector.connected &&
                (connector.setup === "one_click" || connector.also_connectable) ? (
                    <Button
                        size="sm"
                        variant={connector.setup_url ? "outline" : "default"}
                        onClick={connect}
                        disabled={busy}
                        className="w-full"
                    >
                        {busy
                            ? "Opening…"
                            : connector.setup_url
                              ? "Or connect as a tool"
                              : "Connect"}
                        <ExternalLink className="ml-1 h-3 w-3" />
                    </Button>
                ) : null}
                {error ? <p className="text-xs text-destructive">{error}</p> : null}
            </CardContent>
        </Card>
    );
}
