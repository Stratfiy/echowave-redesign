"use client";

import { CreditCard, ExternalLink, PhoneOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
    getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    getPreferencesApiV1OrganizationsPreferencesGet,
    saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put,
    savePreferencesApiV1OrganizationsPreferencesPut,
} from "@/client/sdk.gen";
import type {
    OrganizationAiModelConfigurationResponse,
    OrganizationAiModelConfigurationV2,
} from "@/client/types.gen";
import { AIModelConfigurationV2Editor, type ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useUserConfig } from "@/context/UserConfigContext";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export default function ModelConfigurationV2({
    docsUrl,
    guardUnsavedChanges = false,
}: {
    docsUrl?: string;
    /** Forwarded to the model editor — see ServiceConfigurationForm's prop docs. */
    guardUnsavedChanges?: boolean;
}) {
    const auth = useAuth();
    const { refreshConfig } = useUserConfig();
    const hasFetched = useRef(false);

    const [defaults, setDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const [response, setResponse] = useState<OrganizationAiModelConfigurationResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            setLoading(true);
            setError(null);
            const [defaultsResult, configResult] = await Promise.all([
                getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
                getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get(),
            ]);

            if (defaultsResult.error) {
                setError(detailFromResult(defaultsResult, "Failed to load model configuration defaults"));
                setLoading(false);
                return;
            }
            if (configResult.error) {
                setError(detailFromResult(configResult, "Failed to load model configuration"));
                setLoading(false);
                return;
            }

            const nextDefaults = defaultsResult.data as ModelConfigurationDefaultsV2;
            if (!nextDefaults || !configResult.data) {
                setError("Failed to load model configuration");
                setLoading(false);
                return;
            }
            setDefaults(nextDefaults);
            setResponse(configResult.data);
            setLoading(false);
        };

        load();

    }, [auth.loading, auth.user]);

    const saveConfiguration = async (configuration: OrganizationAiModelConfigurationV2) => {
        if (!defaults) return;
        setError(null);
        setNotice(null);

        const result = await saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put({
            body: configuration,
        });

        if (result.error) {
            throw new Error(detailFromResult(result, "Failed to save model configuration"));
        }
        if (!result.data) {
            throw new Error("Failed to save model configuration");
        }

        setResponse(result.data);
        await refreshConfig();
        setNotice("Model configuration saved");
    };

    if (loading) {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <Skeleton className="h-10 w-80" />
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-96 w-full" />
            </div>
        );
    }

    return (
        <div className="w-full max-w-4xl mx-auto space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-3xl font-bold">Models</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        The default stack for every agent in this organization. An agent can
                        override it.{" "}
                        {docsUrl && (
                            <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                    </p>
                </div>
            </div>

            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}
            {notice && (
                <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                    {notice}
                </div>
            )}

            {defaults && response && (
                <AIModelConfigurationV2Editor
                    defaults={defaults}
                    configuration={response.configuration}
                    effectiveConfiguration={response.effective_configuration}
                    onSave={saveConfiguration}
                    guardUnsavedChanges={guardUnsavedChanges}
                />
            )}

            <KeyFallbackPreference />
        </div>
    );
}

/**
 * What to do when a slot runs on the account's own key and no usable key is
 * stored — never added, or paused while rotating at the vendor.
 *
 * Its own card below the editor rather than a field inside it, because it is
 * not part of the model stack: it is a standing answer to a failure, and it
 * applies whichever stack is saved above. It also saves to a different
 * endpoint, and folding it into the editor's save would make one button write
 * two resources.
 *
 * The copy is the point. This is a consent control — turning it on authorises a
 * charge — so it says what it costs before it is flipped, not afterwards in a
 * toast.
 */
function KeyFallbackPreference() {
    const auth = useAuth();
    const hasFetched = useRef(false);

    const [enabled, setEnabled] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            const result = await getPreferencesApiV1OrganizationsPreferencesGet();
            if (result.error) {
                setError(detailFromResult(result, "Failed to load preferences"));
                setLoading(false);
                return;
            }
            setEnabled(Boolean(result.data?.byok_fallback_to_managed));
            setLoading(false);
        };

        load();
    }, [auth.loading, auth.user]);

    const toggle = async (next: boolean) => {
        setSaving(true);
        setError(null);

        // Read-modify-write: the endpoint takes the whole preferences object,
        // so sending only this field would blank the timezone and the test
        // number alongside it.
        const current = await getPreferencesApiV1OrganizationsPreferencesGet();
        if (current.error) {
            setError(detailFromResult(current, "Failed to save preference"));
            setSaving(false);
            return;
        }

        const result = await savePreferencesApiV1OrganizationsPreferencesPut({
            body: { ...(current.data ?? {}), byok_fallback_to_managed: next },
        });
        if (result.error) {
            setError(detailFromResult(result, "Failed to save preference"));
            setSaving(false);
            return;
        }

        setEnabled(next);
        setSaving(false);
    };

    if (loading) return <Skeleton className="h-32 w-full" />;

    return (
        <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="text-lg">If your own key is unavailable</CardTitle>
                    <CardDescription>
                        Applies when a slot above is set to your own key and no usable key is
                        stored for it — you have not added one yet, or you paused it while
                        rotating at the vendor.
                    </CardDescription>
                </div>
                <Switch
                    checked={enabled}
                    disabled={saving}
                    onCheckedChange={toggle}
                    aria-label="Fall back to Decibyl's keys when your own key is unavailable"
                />
            </CardHeader>
            <CardContent className="space-y-3">
                {enabled ? (
                    <p className="flex items-start gap-2 text-sm">
                        <CreditCard className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        <span>
                            <span className="font-medium">
                                Calls keep running on Decibyl&apos;s keys.
                            </span>{" "}
                            The affected component uses the same provider and model you chose,
                            on our key, and{" "}
                            <span className="text-foreground">
                                is billed to your account at the published rate
                            </span>{" "}
                            for as long as your key is missing. You will not be told
                            mid-call; check the usage breakdown to see which calls fell back.
                        </span>
                    </p>
                ) : (
                    <p className="flex items-start gap-2 text-sm">
                        <PhoneOff className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        <span>
                            <span className="font-medium">Calls are refused.</span> Nothing is
                            billed and no call is placed, and the error names the slot that
                            needs a key. This is the default: it cannot charge you for
                            something you did not choose.
                        </span>
                    </p>
                )}

                <p className="text-xs text-muted-foreground">
                    Either way the call never connects to a silent agent, which is what used
                    to happen.
                </p>

                {error && <p className="text-sm text-destructive">{error}</p>}
            </CardContent>
        </Card>
    );
}
