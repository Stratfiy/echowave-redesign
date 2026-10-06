"use client";

/**
 * The Studio screen. Before anything exists it is one question and a box,
 * full width. Once a conversation starts it becomes the chat beside a
 * canvas -- the site it is building -- the way Grok and ChatGPT open a
 * canvas next to the thread. On a phone the two share the screen through a
 * Chat / Preview switch rather than stacking a long chat over a preview
 * nobody scrolls down to.
 *
 * The chat says which site each turn touched (`site_id`), and the panel
 * follows it, so somebody who asked for a change sees that site rebuilt
 * without picking it from a list.
 */

import { Loader2, MessageSquare, MonitorSmartphone, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import {
    type Site,
    studioApi,
    type StudioChatResponse,
    type StudioConfig,
    type StudioUsage,
} from "./api";
import { SitePanel } from "./SitePanel";
import { StudioChat } from "./StudioChat";

export function StudioWorkspace() {
    const { user, loading: authLoading } = useAuth();
    const [config, setConfig] = useState<StudioConfig | null>(null);
    const [configError, setConfigError] = useState<string | null>(null);
    const [usage, setUsage] = useState<StudioUsage | null>(null);
    const [sites, setSites] = useState<Site[]>([]);
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [site, setSite] = useState<Site | null>(null);
    const [siteError, setSiteError] = useState<string | null>(null);
    const [started, setStarted] = useState(false);
    const [phoneView, setPhoneView] = useState<"chat" | "preview">("chat");
    const hasFetched = useRef(false);

    const loadSites = useCallback(async (prefer?: number | null) => {
        const result = await studioApi.listSites();
        if (result.error !== undefined) {
            setSiteError(result.error);
            return;
        }
        setSites(result.data.sites);
        setSelectedId((current) => {
            if (prefer) return prefer;
            if (current && result.data.sites.some((s) => s.id === current)) return current;
            return result.data.sites[0]?.id ?? null;
        });
    }, []);

    const loadSite = useCallback(async (siteId: number) => {
        const result = await studioApi.getSite(siteId);
        if (result.error !== undefined) {
            setSiteError(result.error);
            return;
        }
        setSiteError(null);
        setSite(result.data);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            const result = await studioApi.config();
            if (result.error !== undefined) {
                setConfigError(result.error);
                return;
            }
            setConfig(result.data);
            setUsage(result.data.usage);
            await loadSites();
        })();
    }, [authLoading, user, loadSites]);

    useEffect(() => {
        if (selectedId === null) {
            setSite(null);
            return;
        }
        void loadSite(selectedId);
    }, [selectedId, loadSite]);

    const onTurn = useCallback(
        (response: StudioChatResponse) => {
            setUsage((previous) => ({ ...(previous ?? response.usage), ...response.usage }));
            const touched = response.site_id;
            // A turn that built something: show it on a phone too.
            if (touched) setPhoneView("preview");
            void loadSites(touched).then(() => {
                // Same id as before: the effect will not fire, so reload here.
                if (touched && touched === selectedId) void loadSite(touched);
            });
        },
        [loadSites, loadSite, selectedId],
    );

    const removeSite = async () => {
        if (!site) return;
        if (!window.confirm(`Delete "${site.name}"? Its files and preview are removed.`)) return;
        const result = await studioApi.deleteSite(site.id);
        if (result.error !== undefined) {
            setSiteError(result.error);
            return;
        }
        setSelectedId(null);
        await loadSites();
    };

    if (configError) {
        return (
            <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                {configError}
            </div>
        );
    }
    if (!config) {
        return (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading Studio…
            </div>
        );
    }
    if (!config.available) {
        return (
            <div className="rounded-md border border-border bg-card px-4 py-6 text-sm text-muted-foreground">
                {config.unavailable_reason ??
                    "Studio needs a model key, and this deployment has none set."}
            </div>
        );
    }

    const banner = !config.builds_configured ? (
        <div className="rounded-2xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-900 dark:text-amber-200">
            Site builds are not set up on this deployment, so Studio can write a
            site but not build or preview it yet.
        </div>
    ) : null;

    const chat = (
        <StudioChat
            usage={usage}
            onTurn={onTurn}
            onStarted={setStarted}
            hasSite={sites.length > 0}
        />
    );

    // Nothing yet: the question, centred, with nothing beside it. The chat
    // stays at the same place in the tree in both layouts, so the first
    // message does not remount it halfway through being sent.
    const hero = !started && sites.length === 0;

    const canvas = (
        <div className="flex min-h-0 min-w-0 flex-col gap-2 lg:h-full">
            {/* One site is named on its own panel; a picker only when there is a choice. */}
            {sites.length > 0 ? (
                <div className="flex items-center gap-2">
                    {sites.length > 1 ? (
                        <>
                            <label htmlFor="studio-site" className="sr-only">
                                Site
                            </label>
                            <select
                                id="studio-site"
                                value={selectedId ?? ""}
                                onChange={(event) => setSelectedId(Number(event.target.value))}
                                className="h-9 min-w-0 flex-1 rounded-full border border-input bg-background px-3 text-sm sm:max-w-xs"
                            >
                                {sites.map((s) => (
                                    <option key={s.id} value={s.id}>
                                        {s.name}
                                    </option>
                                ))}
                            </select>
                        </>
                    ) : null}
                    {site ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="ml-auto rounded-full text-muted-foreground"
                            onClick={removeSite}
                            aria-label={`Delete ${site.name}`}
                        >
                            <Trash2 className="mr-1.5 h-4 w-4" />
                            Delete site
                        </Button>
                    ) : null}
                </div>
            ) : null}
            {siteError ? (
                <p role="alert" className="text-sm text-destructive">
                    {siteError}
                </p>
            ) : null}
            {site ? (
                <SitePanel site={site} onChanged={() => void loadSite(site.id)} />
            ) : (
                <div className="flex min-h-[420px] flex-1 flex-col items-center justify-center gap-2 rounded-3xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                    <MonitorSmartphone className="h-8 w-8 text-muted-foreground/60" />
                    Your site appears here as Studio builds it.
                </div>
            )}
        </div>
    );

    return (
        <div className="space-y-3">
            {banner}
            <div
                role="tablist"
                aria-label="Show"
                className={cn("flex rounded-full bg-muted p-1 lg:hidden", hero && "hidden")}
            >
                {(
                    [
                        ["chat", "Chat", MessageSquare],
                        ["preview", "Preview", MonitorSmartphone],
                    ] as const
                ).map(([id, label, Icon]) => (
                    <button
                        key={id}
                        type="button"
                        role="tab"
                        aria-selected={phoneView === id}
                        onClick={() => setPhoneView(id)}
                        className={cn(
                            "flex flex-1 items-center justify-center gap-1.5 rounded-full py-1.5 text-sm text-muted-foreground",
                            phoneView === id && "bg-background text-foreground shadow-sm",
                        )}
                    >
                        <Icon className="h-4 w-4" />
                        {label}
                    </button>
                ))}
            </div>
            <div
                className={cn(
                    "grid gap-5",
                    !hero &&
                        "lg:h-[calc(100vh-14rem)] lg:min-h-[560px] lg:grid-cols-[minmax(340px,440px)_minmax(0,1fr)]",
                )}
            >
                <div
                    className={cn(
                        "min-h-0 lg:block",
                        !hero && "lg:h-full",
                        hero || phoneView === "chat" ? "block" : "hidden",
                    )}
                >
                    {chat}
                </div>
                {hero ? null : (
                    <div
                        className={cn(
                            "min-h-0 lg:block lg:h-full",
                            phoneView === "preview" ? "block" : "hidden",
                        )}
                    >
                        {canvas}
                    </div>
                )}
            </div>
        </div>
    );
}
