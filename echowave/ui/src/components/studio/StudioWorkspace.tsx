"use client";

/**
 * The Studio screen: the chat on one side, the site it is building on the
 * other. On a phone they stack, chat first.
 *
 * The chat says which site each turn touched (`site_id`), and the panel
 * follows it, so somebody who asked for a change sees that site rebuilt
 * without picking it from a list.
 */

import { Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

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

    return (
        <div className="space-y-3">
            {!config.builds_configured ? (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-900 dark:text-amber-200">
                    Site builds are not set up on this deployment, so Studio can write a
                    site but not build or preview it yet.
                </div>
            ) : null}
            <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_minmax(0,1fr)]">
                <StudioChat usage={usage} onTurn={onTurn} />
                <div className="min-w-0 space-y-2">
                    {sites.length > 0 ? (
                        <div className="flex items-center gap-2">
                            <label htmlFor="studio-site" className="text-sm text-muted-foreground">
                                Site
                            </label>
                            <select
                                id="studio-site"
                                value={selectedId ?? ""}
                                onChange={(event) => setSelectedId(Number(event.target.value))}
                                className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm sm:max-w-xs"
                            >
                                {sites.map((s) => (
                                    <option key={s.id} value={s.id}>
                                        {s.name}
                                    </option>
                                ))}
                            </select>
                            {site ? (
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={removeSite}
                                    aria-label={`Delete ${site.name}`}
                                >
                                    <Trash2 className="h-4 w-4" />
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
                        <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground lg:h-[calc(100vh-11rem)]">
                            Your site appears here as Studio builds it.
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
