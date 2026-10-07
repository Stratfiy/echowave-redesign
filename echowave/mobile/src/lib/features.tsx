/**
 * Which switched-off features are on for this workspace: the global map from
 * GET /health merged with the workspace's own from GET /features, exactly as
 * the web does (ui/src/lib/features.ts). A screen whose feature is off shows
 * what it would show on the web -- nothing, or "not switched on" -- never a
 * broken request.
 */
import { useLoad } from '@/lib/useLoad';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { healthApiV1HealthGet, organizationFeaturesApiV1FeaturesGet } from '@/client/sdk.gen';
import { call } from '@/lib/api';

type Features = { flags: Record<string, boolean>; loaded: boolean; refresh(): Promise<void> };

const FeaturesContext = createContext<Features>({ flags: {}, loaded: false, refresh: async () => undefined });

export function FeaturesProvider({ children, signedIn }: { children: ReactNode; signedIn: boolean }) {
    const [flags, setFlags] = useState<Record<string, boolean>>({});
    const [loaded, setLoaded] = useState(false);

    const refresh = useCallback(async () => {
        const merged: Record<string, boolean> = {};
        try {
            const health = await call(healthApiV1HealthGet());
            Object.assign(merged, health.features ?? {});
        } catch {
            // Offline: keep what we had.
        }
        if (signedIn) {
            try {
                const mine = await call(organizationFeaturesApiV1FeaturesGet());
                for (const [name, on] of Object.entries(mine ?? {})) merged[name] = Boolean(merged[name]) || Boolean(on);
            } catch {
                // Same.
            }
        }
        if (Object.keys(merged).length) setFlags(merged);
        setLoaded(true);
    }, [signedIn]);

    useLoad(refresh, [refresh]);

    const value = useMemo(() => ({ flags, loaded, refresh }), [flags, loaded, refresh]);
    return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export function useFeatures(): Features {
    return useContext(FeaturesContext);
}

export function useFeature(name: string): boolean {
    return Boolean(useContext(FeaturesContext).flags[name]);
}
