'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { getCurrentOrganizationContextApiV1OrganizationsContextGet, getUserConfigurationsApiV1UserConfigurationsUserGet } from '@/client/sdk.gen';
import type { OrganizationContextResponse, UserConfigurationRequestResponseSchema } from '@/client/types.gen';
import { setupAuthInterceptor } from '@/lib/apiClient';
import type { AuthUser } from '@/lib/auth';
import { useAuth } from '@/lib/auth';

interface TeamPermission {
    id: string;
}

interface OrganizationPricing {
    price_per_second_usd: number | null;
    currency: string;
    billing_enabled: boolean;
}

interface OrgConfigContextType {
    orgContext: OrganizationContextResponse | null;
    userConfig: UserConfigurationRequestResponseSchema | null;
    loading: boolean;
    error: Error | null;
    refreshConfig: () => Promise<void>;
    permissions: TeamPermission[];
    user: AuthUser | null;
    organizationPricing: OrganizationPricing | null;
    // The signed-in organisation's feature map from /api/v1/features
    // (global flags plus FEATURE_ORG_OVERRIDES); null until fetched. Read
    // through useFeature in @/lib/features, not directly.
    orgFeatures: Record<string, boolean> | null;
}

const OrgConfigContext = createContext<OrgConfigContextType | null>(null);

const pricingFromUserConfig = (
    userConfig: UserConfigurationRequestResponseSchema,
): OrganizationPricing | null => {
    if (!userConfig.organization_pricing) {
        return null;
    }

    return {
        price_per_second_usd: userConfig.organization_pricing.price_per_second_usd as number | null,
        currency: (userConfig.organization_pricing.currency as string) || 'USD',
        billing_enabled: (userConfig.organization_pricing.billing_enabled as boolean) || false,
    };
};

export function OrgConfigProvider({ children }: { children: ReactNode }) {
    const [orgContext, setOrgContext] = useState<OrganizationContextResponse | null>(null);
    const [userConfig, setUserConfig] = useState<UserConfigurationRequestResponseSchema | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const [organizationPricing, setOrganizationPricing] = useState<OrganizationPricing | null>(null);
    const [permissions, setPermissions] = useState<TeamPermission[]>([]);
    const [orgFeatures, setOrgFeatures] = useState<Record<string, boolean> | null>(null);

    const auth = useAuth();

    const authRef = useRef(auth);
    authRef.current = auth;

    const hasFetchedConfig = useRef(false);
    const hasFetchedPermissions = useRef(false);

    // Registered on the first render, not once auth has resolved: a
    // screen's first fetches go out before that, and an interceptor that
    // was not there yet sent them with no bearer at all. The getter reads
    // the live auth through the ref and itself waits for the token.
    setupAuthInterceptor(client, () => authRef.current.getAccessToken());

    useEffect(() => {
        if (auth.loading || hasFetchedPermissions.current) {
            return;
        }
        hasFetchedPermissions.current = true;

        const fetchPermissions = async () => {
            const currentAuth = authRef.current;
            if (currentAuth.provider === 'stack' && currentAuth.getSelectedTeam && currentAuth.listPermissions) {
                const selectedTeam = currentAuth.getSelectedTeam();
                if (selectedTeam) {
                    try {
                        const perms = await currentAuth.listPermissions(selectedTeam);
                        setPermissions(Array.isArray(perms) ? perms : []);
                    } catch {
                        setPermissions([]);
                    }
                } else {
                    setPermissions([]);
                }
            } else {
                setPermissions([{ id: 'admin' }]);
            }
        };

        fetchPermissions();
    }, [auth.loading, auth.provider]);

    const fetchConfig = useCallback(async () => {
        const currentAuth = authRef.current;
        if (!currentAuth.isAuthenticated) {
            return;
        }

        setLoading(true);
        try {
            const [orgContextResponse, userConfigResponse, featuresResponse] = await Promise.all([
                getCurrentOrganizationContextApiV1OrganizationsContextGet(),
                getUserConfigurationsApiV1UserConfigurationsUserGet(),
                // Not in the generated SDK on purpose: the map is a plain
                // record and this keeps the flags-only PR off sdk.gen.ts.
                client.get({ url: '/api/v1/features' }).catch(() => null),
            ]);

            const featureMap = featuresResponse && !featuresResponse.error
                ? (featuresResponse.data as Record<string, boolean> | undefined)
                : undefined;
            if (featureMap && typeof featureMap === 'object') {
                setOrgFeatures(featureMap);
            }

            if (orgContextResponse.data) {
                setOrgContext(orgContextResponse.data);
            }

            if (userConfigResponse.data) {
                setUserConfig(userConfigResponse.data);
                setOrganizationPricing(pricingFromUserConfig(userConfigResponse.data));
            }

            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err : new Error('Failed to fetch organization configuration'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (auth.loading || !auth.isAuthenticated || hasFetchedConfig.current) {
            return;
        }
        hasFetchedConfig.current = true;
        fetchConfig();
    }, [auth.loading, auth.isAuthenticated, fetchConfig]);

    const refreshConfig = useCallback(async () => {
        await fetchConfig();
    }, [fetchConfig]);

    return (
        <OrgConfigContext.Provider
            value={{
                orgContext,
                userConfig,
                loading,
                error,
                refreshConfig,
                permissions,
                user: auth.user,
                organizationPricing,
                orgFeatures,
            }}
        >
            {children}
        </OrgConfigContext.Provider>
    );
}

/**
 * The organisation feature map, or null outside the provider (the sign-in
 * screen, public pages). Never throws, so useFeature can run anywhere.
 */
export function useOrgFeatures(): Record<string, boolean> | null {
    const context = useContext(OrgConfigContext);
    return context?.orgFeatures ?? null;
}

/** Whether the organisation's config (and its feature map) has been asked
 *  for and answered. Outside the provider there is nothing to wait for. */
export function useOrgConfigSettled(): boolean {
    const context = useContext(OrgConfigContext);
    return !context || !context.loading;
}

export function useOrgConfig() {
    const context = useContext(OrgConfigContext);
    if (!context) {
        throw new Error('useOrgConfig must be used within an OrgConfigProvider');
    }
    return context;
}
