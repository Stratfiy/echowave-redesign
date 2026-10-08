"use client";

/**
 * Settings -> Connections (screen 22; launch stream identity).
 *
 * Two sections, because they are two different grants (handoff 25): apps
 * Decibyl may read and act in, and channels you message Decibyl on. Each
 * row shows the account, whose it is, its state and its last success; the
 * detail lists what it grants. Connecting explains the access first and
 * records your consent; disconnecting is an approval card, here on the page.
 * You see your own connections and the workspace's -- never a colleague's.
 */

import { ChevronDown, ChevronUp, Plug } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import {
    completeConnectionApiV1MeConnectionsConsentsConsentIdCompletePost,
    myConnectionsApiV1MeConnectionsGet,
    previewConnectionApiV1MeConnectionsPreviewGet,
    proposeDisconnectApiV1MeConnectionsDisconnectPost,
    startConnectionApiV1MeConnectionsStartPost,
} from "@/client/sdk.gen";
import type { AppItem, ChannelItem, ConnectionPreview, ConnectionsResponse } from "@/client/types.gen";
import { DecibylAppsSection } from "@/components/DecibylAppsSection";
import { IdentityCardPanel } from "@/components/identity/IdentityCardPanel";
import { useIdentityCards } from "@/components/identity/useIdentityCards";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { type CapabilityState, ConnectionRow, EmptyState, ErrorState, SettingsSection } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

/** The handoff's eight states, onto the shared capability vocabulary; the
 *  precise state is said in the reason. */
const APP_STATE: Record<string, { capability: CapabilityState; label: string }> = {
    ready: { capability: "available", label: "Ready" },
    authorizing: { capability: "needs_setup", label: "Signing in" },
    syncing: { capability: "needs_setup", label: "Syncing" },
    limited: { capability: "needs_setup", label: "Limited access" },
    expired: { capability: "needs_setup", label: "Expired" },
    error: { capability: "needs_setup", label: "Error" },
    revoked: { capability: "unavailable", label: "Disconnected" },
    disconnected: { capability: "unavailable", label: "Not connected" },
};

const SUGGESTED = ["gmail", "googlecalendar", "outlook", "slack", "notion"];

function when(value?: string | null): string | null {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function AppRow({ item, onDisconnect }: { item: AppItem; onDisconnect: (item: AppItem) => void }) {
    const [open, setOpen] = useState(false);
    const meta = APP_STATE[item.state] ?? APP_STATE.error;
    const detail = [item.account, item.owner === "you" ? "Yours" : "Workspace", when(item.last_success_at) && `last worked ${when(item.last_success_at)}`]
        .filter(Boolean)
        .join(" · ");
    return (
        <div className="border-b border-border last:border-b-0">
            <ConnectionRow
                className="border-b-0"
                name={item.app_name}
                state={meta.capability}
                reason={meta.capability === "available" ? undefined : `${meta.label}. ${item.reason ?? ""}`.trim()}
                detail={detail}
                action={
                    <Button
                        type="button"
                        variant="ghost"
                        className="min-h-11 md:min-h-9"
                        aria-expanded={open}
                        onClick={() => setOpen((v) => !v)}
                    >
                        Details {open ? <ChevronUp aria-hidden className="h-4 w-4" /> : <ChevronDown aria-hidden className="h-4 w-4" />}
                    </Button>
                }
            />
            {open && (
                <div className="motion-m2 mb-3 rounded-[var(--radius)] bg-muted/40 p-3 text-sm" data-testid="app-detail">
                    {item.purpose && <p className="mb-2">Connected for: {item.purpose}</p>}
                    <p className="mb-1 font-medium">What it allows</p>
                    <ul className="mb-3 list-disc space-y-1 pl-5">
                        {(item.access ?? []).map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                    {item.connected_at && <p className="text-muted-foreground">Connected {when(item.connected_at)}</p>}
                    {item.can_disconnect ? (
                        <Button type="button" variant="outline" className="mt-3 min-h-11 md:min-h-9" onClick={() => onDisconnect(item)}>
                            Disconnect…
                        </Button>
                    ) : item.owner === "workspace" && item.state !== "revoked" ? (
                        <p className="mt-3 text-muted-foreground">Managed by your workspace.</p>
                    ) : null}
                </div>
            )}
        </div>
    );
}

function ChannelRow({ item }: { item: ChannelItem }) {
    const linked = item.linked[0];
    const detail = [
        linked ? `Linked${linked.display_name ? ` as ${linked.display_name}` : ""} · ending ${linked.handle}` : "Not linked",
        item.proactive,
    ].join(" · ");
    return (
        <ConnectionRow
            name={item.name}
            state={item.capability}
            reason={item.capability === "available" ? (item.delivery_failing ? "Recent messages could not be delivered." : undefined) : item.reason ?? undefined}
            detail={detail}
        />
    );
}

function ConnectAnApp({ onStarted }: { onStarted: () => void }) {
    const pathname = usePathname() ?? "/settings/connections";
    const [toolkit, setToolkit] = useState("");
    const [purpose, setPurpose] = useState("");
    const [preview, setPreview] = useState<ConnectionPreview | null>(null);
    const [scope, setScope] = useState<"mine" | "workspace">("mine");
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const explain = async (slug: string) => {
        setError(null);
        setToolkit(slug);
        const res = await previewConnectionApiV1MeConnectionsPreviewGet({ query: { toolkit: slug } });
        if (res.error || !res.data) {
            setPreview(null);
            setError(detailFromError(res.error, "Could not look that app up. Try again."));
            return;
        }
        setPreview(res.data);
        setScope(res.data.per_person ? "mine" : "workspace");
    };

    const connect = async () => {
        if (!preview) return;
        setBusy(true);
        setError(null);
        // Opened inside the click, so a pop-up blocker lets it through.
        const tab = window.open("", "_blank");
        const res = await startConnectionApiV1MeConnectionsStartPost({
            body: { toolkit: preview.toolkit, scope, purpose: purpose || null, return_to: pathname },
        });
        setBusy(false);
        if (res.error || !res.data) {
            tab?.close();
            setError(detailFromError(res.error, "Could not start connecting. Try again."));
            return;
        }
        try {
            window.sessionStorage.setItem("identity:pending-consent", String(res.data.consent_id));
        } catch {
            /* the return check below still runs on focus */
        }
        if (tab) tab.location.href = res.data.url;
        else window.location.href = res.data.url;
        setPreview(null);
        setToolkit("");
        setPurpose("");
        onStarted();
    };

    return (
        <div className="mt-4 flex flex-col gap-3" data-testid="connect-an-app">
            <p className="text-sm font-medium">Connect an app</p>
            <div className="flex flex-wrap gap-2">
                {SUGGESTED.map((slug) => (
                    <Button key={slug} type="button" variant={toolkit === slug ? "default" : "outline"} className="min-h-11 md:min-h-9" onClick={() => void explain(slug)}>
                        {slug}
                    </Button>
                ))}
            </div>
            <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    if (toolkit.trim()) void explain(toolkit.trim().toLowerCase());
                }}
            >
                <div className="min-w-0 flex-1">
                    <Label htmlFor="connect-app">Another app</Label>
                    <Input id="connect-app" className="min-h-11 text-base md:min-h-9 md:text-sm" value={toolkit} onChange={(e) => setToolkit(e.target.value)} placeholder="e.g. hubspot" />
                </div>
                <Button type="submit" variant="outline" className="min-h-11 md:min-h-9">
                    Look up
                </Button>
            </form>
            {preview && (
                <div className="rounded-[var(--radius)] border border-border p-3 text-sm" data-testid="connect-preview">
                    <p className="mb-1 font-medium">Before you connect {preview.app_name}</p>
                    <ul className="mb-3 list-disc space-y-1 pl-5">
                        {preview.access.map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                    <Label htmlFor="connect-purpose">What is it for? (optional)</Label>
                    <Input id="connect-purpose" className="mb-3 min-h-11 text-base md:min-h-9 md:text-sm" maxLength={200} value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="Reply to customer mail" />
                    {preview.per_person && preview.can_connect_for_workspace && (
                        <fieldset className="mb-3 flex flex-wrap gap-4">
                            <legend className="mb-1 text-muted-foreground">Whose connection</legend>
                            {(["mine", "workspace"] as const).map((value) => (
                                <label key={value} className="flex min-h-11 items-center gap-2">
                                    <input type="radio" name="scope" checked={scope === value} onChange={() => setScope(value)} />
                                    {value === "mine" ? "Just mine" : "The whole workspace"}
                                </label>
                            ))}
                        </fieldset>
                    )}
                    {!preview.per_person && !preview.can_connect_for_workspace ? (
                        <p className="text-muted-foreground">Only a workspace admin can connect apps here.</p>
                    ) : (
                        <Button type="button" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void connect()}>
                            <Plug aria-hidden className="h-4 w-4" /> Connect {preview.app_name}
                        </Button>
                    )}
                </div>
            )}
            {error && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {error}
                </p>
            )}
        </div>
    );
}

function ConnectionsScreen() {
    const { user, loading: authLoading } = useAuth();
    const router = useRouter();
    const params = useSearchParams();
    const [data, setData] = useState<ConnectionsResponse | null>(null);
    const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
    const [notice, setNotice] = useState<string | null>(null);
    const [disconnectError, setDisconnectError] = useState<string | null>(null);
    const { cards, refresh: refreshCards } = useIdentityCards(["disconnect_app"]);
    const started = useRef(false);

    const load = useCallback(async () => {
        const res = await myConnectionsApiV1MeConnectionsGet();
        if (res.error || !res.data) {
            // Keep what was shown; say it is stale rather than empty it.
            setState((s) => (s === "ready" ? "ready" : "failed"));
            if (data) setNotice("Could not refresh. Showing what was last loaded.");
            return;
        }
        setData(res.data);
        setState("ready");
    }, [data]);

    const checkReturn = useCallback(async () => {
        let pending: string | null = null;
        try {
            pending = params?.get("consent") ?? window.sessionStorage.getItem("identity:pending-consent");
        } catch {
            pending = params?.get("consent") ?? null;
        }
        if (!pending) return;
        const res = await completeConnectionApiV1MeConnectionsConsentsConsentIdCompletePost({ path: { consent_id: Number(pending) } });
        if (res.error || !res.data) {
            setNotice(detailFromError(res.error, "Could not check the connection. Try again."));
            return;
        }
        if (res.data.state !== "authorizing") {
            try {
                window.sessionStorage.removeItem("identity:pending-consent");
            } catch {
                /* nothing to clear */
            }
        }
        if (res.data.state === "ready") {
            setNotice(`Connected. ${res.data.purpose ? `Back to: ${res.data.purpose}.` : ""}`.trim());
            if (res.data.return_to && res.data.return_to !== window.location.pathname) router.push(res.data.return_to);
        } else if (res.data.state === "error") {
            setNotice(res.data.reason ?? "Signing in was not finished.");
        }
        void load();
    }, [params, router, load]);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
        void checkReturn();
    }, [authLoading, user, load, checkReturn]);

    useEffect(() => {
        const onFocus = () => void checkReturn();
        window.addEventListener("focus", onFocus);
        return () => window.removeEventListener("focus", onFocus);
    }, [checkReturn]);

    const disconnect = async (item: AppItem) => {
        setDisconnectError(null);
        const res = await proposeDisconnectApiV1MeConnectionsDisconnectPost({
            body: { scope: item.scope === "workspace" ? "workspace" : "mine", connected_account_id: item.id },
        });
        if (res.error) {
            setDisconnectError(detailFromError(res.error, "Could not prepare that. Try again."));
            return;
        }
        await refreshCards();
    };

    if (state === "loading") {
        return (
            <div className="flex flex-col gap-3" aria-busy="true">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
            </div>
        );
    }
    if (state === "failed" || !data) {
        return <ErrorState title="Could not load your connections" description="Nothing was changed." onRetry={() => void load()} />;
    }

    const waiting = cards.filter((card) => !["declined", "cancelled"].includes(card.state)).slice(0, 3);
    return (
        <div className="flex flex-col gap-6">
            {notice && (
                <p role="status" className="rounded-[var(--radius)] border border-border px-3 py-2 text-sm">
                    {notice}
                </p>
            )}
            <SettingsSection
                id="apps"
                title="Apps Decibyl may read and act in"
                description="Decibyl reads them for tasks you ask for. Anything that sends, changes or deletes asks you first."
                scope="You and your workspace"
            >
                {waiting.length > 0 && (
                    <div className="mb-4 flex flex-col gap-3">
                        {waiting.map((card) => (
                            <IdentityCardPanel key={card.event_id} card={card} onChanged={() => void refreshCards().then(load)} />
                        ))}
                    </div>
                )}
                {disconnectError && (
                    <p role="alert" className="mb-3 text-sm text-[#772322] dark:text-red-300">
                        {disconnectError}
                    </p>
                )}
                {data.apps.state === "needs_setup" ? (
                    <ConnectionRow name="Connected apps" state="needs_setup" reason={data.apps.reason ?? undefined} />
                ) : (
                    <>
                        {data.apps.state === "error" && (
                            <ErrorState title="Some connections could not be checked" description={data.apps.reason} onRetry={() => void load()} className="py-4" />
                        )}
                        {data.apps.items.length === 0 && data.apps.state === "ok" ? (
                            <EmptyState title="No apps connected" description="Connect one when a task needs it; Decibyl asks in the chat too." />
                        ) : (
                            <div>
                                {data.apps.items.map((item) => (
                                    <AppRow key={item.id} item={item} onDisconnect={(i) => void disconnect(i)} />
                                ))}
                            </div>
                        )}
                        <ConnectAnApp onStarted={() => setNotice("Finish signing in on the app's page, then come back here.")} />
                    </>
                )}
            </SettingsSection>
            <SettingsSection
                id="channels"
                title="Channels you message Decibyl on"
                description="Messaging Decibyl in an app is not access to your other messages there: Decibyl sees only what you send it."
                scope="Just you"
            >
                <div>
                    {data.channels.map((item) => (
                        <ChannelRow key={item.channel} item={item} />
                    ))}
                </div>
                <div className="mt-4">
                    <DecibylAppsSection />
                </div>
            </SettingsSection>
        </div>
    );
}

export default function ConnectionsPage() {
    const on = useFeature("identity_connections");
    return (
        <>
            <PageHeader title="Connections" description="Apps Decibyl works in for you, and the apps you message it from." />
            <PageBody className="max-w-[640px] px-4 md:px-6">
                {on ? (
                    <Suspense fallback={<Skeleton className="h-24 w-full" />}>
                        <ConnectionsScreen />
                    </Suspense>
                ) : (
                    <EmptyState title="Not switched on yet" description="Connections will appear here when this is turned on for your workspace." />
                )}
            </PageBody>
        </>
    );
}
