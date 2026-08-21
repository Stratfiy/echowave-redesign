"use client";

import {
    AlertTriangle,
    CalendarClock,
    CheckCircle2,
    KeyRound,
    Loader2,
    ShieldQuestion,
    Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    deleteProviderKeyApiV1ProviderKeysDelete,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    listProviderKeysApiV1ProviderKeysGet,
    setProviderKeyActiveApiV1ProviderKeysActivePost,
    setProviderKeyApiV1ProviderKeysPut,
} from "@/client/sdk.gen";
import { IntegrationsTabs } from "@/components/integrations/IntegrationsTabs";
import {
    COMPONENTS,
    type ComponentValue,
    describeComponents,
    FilterChip,
    providerLabel,
} from "@/components/providerCards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { UnlockedModels } from "@/components/UnlockedModels";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

interface GoogleCalendarStatus {
    connected: boolean;
    connected_email: string | null;
    calendar_id: string | null;
    updated_at: string | null;
    configured: boolean;
}

interface ProviderCredential {
    id: number;
    component: string;
    provider: string;
    masked_key: string;
    label: string | null;
    is_active: boolean;
    updated_at: string | null;
}

// COMPONENTS, describeComponents and providerLabel live in
// @/components/providerCards now — shared with the superadmin screen, which
// draws the same card grid over the same registry. Two copies of a vendor's
// display name are two places to forget to update when one is added.

// Not yet in the generated SDK (client/sdk.gen.ts) -- these routes are new.
// Plain fetch, same auth header the generated client attaches, until
// `npm run generate-client` is re-run against a live backend.
async function googleCalendarFetch(
    path: string,
    accessToken: string,
    init?: RequestInit,
): Promise<{ data?: unknown; error?: string }> {
    try {
        const response = await fetch(`/api/v1/integrations/google-calendar${path}`, {
            ...init,
            headers: {
                Authorization: `Bearer ${accessToken}`,
                ...(init?.headers ?? {}),
            },
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
            return { error: (body as { detail?: string })?.detail || "Request failed" };
        }
        return { data: body };
    } catch {
        return { error: "Network error" };
    }
}

export default function IntegrationsPage() {
    // Open to every member, on purpose.
    //
    // This screen was briefly wrapped in an admin wall on the theory that
    // "every route under /api/v1/provider-keys requires ADMIN". That was
    // wrong: the list endpoint takes plain `get_user`, and only PUT,
    // POST /active and DELETE require ADMIN. The wall broke the thing the
    // screen exists for — BYOK is a member's job. A member picks "your own
    // key" in a model slot, and needs to know which keys the account actually
    // holds to tell an empty slot from a filled one, or to name the key they
    // are asking an admin for. Three places in the model editor link straight
    // here; each of them was a dead end.
    //
    // Same shape as Billing and the Do-not-call list: the door stays open and
    // the writes are gated inside. Keys are masked to their last four
    // characters by the API, so reading shows what exists, never the secret.
    return <IntegrationsScreen />;
}

interface ProviderRow {
    provider: string;
    /** Every slot this vendor can serve, from the registry. */
    serves: ComponentValue[];
    /** What is stored today, one row per component. */
    stored: ProviderCredential[];
}

function IntegrationsScreen() {
    const auth = useAuth();
    const router = useRouter();
    const hasFetched = useRef(false);
    // Presentation only — the server refuses these three regardless. This
    // stops a member being handed a button whose only possible answer is 403.
    //
    // `rolesLoaded` matters for the footer specifically: the hook reports
    // unprivileged until the server answers, so keying copy off the role alone
    // would flash "ask an Owner or Admin" at an actual admin on every load.
    const { isOrganizationAdmin, loaded: rolesLoaded } = useAccessRoles();

    const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
    const [providersByComponent, setProvidersByComponent] = useState<Record<string, string[]>>({});
    const [encryptionConfigured, setEncryptionConfigured] = useState(true);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filter, setFilter] = useState<"all" | ComponentValue>("all");

    const [gcalStatus, setGcalStatus] = useState<GoogleCalendarStatus | null>(null);
    const [gcalLoading, setGcalLoading] = useState(true);
    const [gcalBusy, setGcalBusy] = useState(false);
    const [gcalMessage, setGcalMessage] = useState<
        { kind: "success" | "error"; text: string } | null
    >(null);

    // The vendor being connected. Provider-first: the dialog opens knowing who
    // it is for, and asks only for the key.
    const [dialogProvider, setDialogProvider] = useState<ProviderRow | null>(null);
    const [formKey, setFormKey] = useState("");
    const [formLabel, setFormLabel] = useState("");
    // The escape hatch, off by default. Holding two keys with one vendor on
    // separate billing is a real case — it is why apply_to_all_components
    // exists — but it is not the common one, and making it the default is what
    // produced the three-times-pasted key.
    const [advanced, setAdvanced] = useState(false);
    const [chosenComponents, setChosenComponents] = useState<ComponentValue[]>([]);
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<
        { kind: "verified" | "unverified"; text: string } | null
    >(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);

        const [keysResult, defaultsResult] = await Promise.all([
            listProviderKeysApiV1ProviderKeysGet(),
            getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
        ]);

        if (keysResult.error) {
            setError(detailFromError(keysResult.error, "Failed to load provider keys"));
            setLoading(false);
            return;
        }

        const payload = keysResult.data as {
            credentials: ProviderCredential[];
            encryption_configured: boolean;
        };
        setCredentials(payload.credentials ?? []);
        setEncryptionConfigured(payload.encryption_configured !== false);

        // Which vendors exist per slot, read off the same schema the model
        // picker uses, so the two cannot offer different provider lists.
        // "decibyl" is filtered out: it is the managed option and needs no key.
        if (!defaultsResult.error && defaultsResult.data) {
            const pipeline = (defaultsResult.data as Record<string, never>)?.byok?.["pipeline"] ?? {};
            const next: Record<string, string[]> = {};
            for (const component of ["stt", "llm", "tts"]) {
                const schemas = (pipeline as Record<string, Record<string, unknown>>)[component] ?? {};
                next[component] = Object.keys(schemas)
                    .filter((provider) => provider !== "decibyl")
                    .sort();
            }
            setProvidersByComponent(next);
        }

        setLoading(false);
    }, []);

    const loadGoogleCalendarStatus = useCallback(async () => {
        setGcalLoading(true);
        const accessToken = await auth.getAccessToken();
        const result = await googleCalendarFetch("/status", accessToken);
        if (!result.error && result.data) {
            setGcalStatus(result.data as GoogleCalendarStatus);
        }
        setGcalLoading(false);
    }, [auth]);

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;
        load();
        loadGoogleCalendarStatus();
    }, [auth.loading, auth.user, load, loadGoogleCalendarStatus]);

    // Landing back here after the Google consent screen: ?google_calendar=connected|error
    // Read straight from window.location rather than useSearchParams() -- this
    // only needs to run once on mount, and avoids Next's Suspense-boundary
    // requirement for that hook.
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const outcome = params.get("google_calendar");
        if (!outcome) return;

        if (outcome === "connected") {
            setGcalMessage({ kind: "success", text: "Google Calendar connected." });
            loadGoogleCalendarStatus();
        } else if (outcome === "error") {
            const reason = params.get("reason") || "Something went wrong.";
            setGcalMessage({ kind: "error", text: `Could not connect Google Calendar: ${reason}` });
        }

        // Strip the query params so a refresh doesn't re-show the banner.
        router.replace("/integrations");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /**
     * One row per vendor, not per slot.
     *
     * This is the whole inversion. The registry already knows which components
     * each vendor serves, so the page asks the question Vapi asks — *which
     * vendor do you have an account with* — and derives the rest.
     */
    const providers: ProviderRow[] = useMemo(() => {
        const serves = new Map<string, ComponentValue[]>();
        for (const { value } of COMPONENTS) {
            for (const provider of providersByComponent[value] ?? []) {
                serves.set(provider, [...(serves.get(provider) ?? []), value]);
            }
        }
        return [...serves.entries()]
            .map(([provider, components]) => ({
                provider,
                serves: components,
                stored: credentials.filter((c) => c.provider === provider),
            }))
            .sort((a, b) => {
                // Connected vendors first — what you came to check is what you
                // already have — then alphabetically.
                const connected = Number(b.stored.length > 0) - Number(a.stored.length > 0);
                return connected || providerLabel(a.provider).localeCompare(providerLabel(b.provider));
            });
    }, [providersByComponent, credentials]);

    const visibleProviders = useMemo(
        () =>
            filter === "all"
                ? providers
                : providers.filter((row) => row.serves.includes(filter)),
        [providers, filter],
    );

    const connectedCount = providers.filter((row) => row.stored.length > 0).length;

    const openDialog = (row: ProviderRow) => {
        setDialogProvider(row);
        setFormKey("");
        setFormLabel(row.stored[0]?.label ?? "");
        setAdvanced(false);
        setChosenComponents(row.serves);
        setFormError(null);
    };

    const saveKey = async () => {
        if (!dialogProvider) return;
        setSaving(true);
        setFormError(null);
        setNotice(null);

        // Which slots this key covers. The default is every slot the vendor
        // serves — one account, one key — with the per-component choice behind
        // "advanced" for the customer who genuinely holds several.
        const components = advanced ? chosenComponents : dialogProvider.serves;
        if (components.length === 0) {
            setFormError("Choose at least one thing this key is for.");
            setSaving(false);
            return;
        }

        // Covering everything the vendor serves is one request the backend
        // writes in a single transaction. A subset — only reachable through
        // "advanced" — has to be stored a slot at a time, so the slots the
        // customer did *not* choose keep whatever key they already had.
        const coversEverything = components.length === dialogProvider.serves.length;

        const result = await setProviderKeyApiV1ProviderKeysPut({
            body: {
                component: components[0],
                provider: dialogProvider.provider,
                api_key: formKey.trim(),
                label: formLabel.trim() || null,
                apply_to_all_components: coversEverything,
            },
        });

        if (result.error) {
            setFormError(detailFromError(result.error, "Failed to save the key"));
            setSaving(false);
            return;
        }

        if (!coversEverything) {
            for (const component of components.slice(1)) {
                const extra = await setProviderKeyApiV1ProviderKeysPut({
                    body: {
                        component,
                        provider: dialogProvider.provider,
                        api_key: formKey.trim(),
                        label: formLabel.trim() || null,
                        apply_to_all_components: false,
                    },
                });
                if (extra.error) {
                    setFormError(detailFromError(extra.error, "Failed to save the key"));
                    setSaving(false);
                    await load();
                    return;
                }
            }
        }

        // Whether the vendor actually confirmed the key, or we only stored it.
        // Showing the same tick for both is what made a Connect button a lie.
        const verification = (result.data as { verification?: string; verification_message?: string }) ?? {};
        if (verification.verification === "unverified") {
            setNotice({
                kind: "unverified",
                text:
                    verification.verification_message ??
                    "The key was stored but could not be checked.",
            });
        } else {
            setNotice({
                kind: "verified",
                text: `${providerLabel(dialogProvider.provider)} accepted the key.`,
            });
        }

        setSaving(false);
        setDialogProvider(null);
        await load();
    };

    /** Disconnecting a vendor removes every slot it was covering. */
    const removeProvider = async (row: ProviderRow) => {
        for (const credential of row.stored) {
            const result = await deleteProviderKeyApiV1ProviderKeysDelete({
                query: { component: credential.component, provider: credential.provider },
            });
            if (result.error) {
                setError(detailFromError(result.error, "Failed to remove the key"));
                await load();
                return;
            }
        }
        await load();
    };

    const setProviderActive = async (row: ProviderRow, isActive: boolean) => {
        for (const credential of row.stored) {
            const result = await setProviderKeyActiveApiV1ProviderKeysActivePost({
                body: {
                    component: credential.component,
                    provider: credential.provider,
                    is_active: isActive,
                },
            });
            if (result.error) {
                setError(detailFromError(result.error, "Failed to update the key"));
                await load();
                return;
            }
        }
        await load();
    };

    if (loading) {
        return (
            <>
                <IntegrationsTabs />
                <div className="container mx-auto max-w-4xl px-4 py-8 space-y-6">
                    <Skeleton className="h-10 w-72" />
                    <Skeleton className="h-48 w-full" />
                    <Skeleton className="h-48 w-full" />
                </div>
            </>
        );
    }

    return (
        <>
        <IntegrationsTabs />
        <div className="container mx-auto max-w-4xl px-4 py-8 space-y-6">
            <div>
                <h1 className="text-3xl font-bold">Provider keys</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                    Connect the vendors you already have an account with. One key per
                    vendor, entered once — we work out which parts of the pipeline it
                    covers.{" "}
                    <span className="text-foreground">
                        Anything you do not connect runs on Decibyl&apos;s keys and is billed
                        at the published rate
                    </span>{" "}
                    — you never have to bring a key to get an agent working.
                </p>
            </div>

            {!encryptionConfigured && (
                <div className="flex gap-3 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                        <p className="font-medium">Keys cannot be stored on this deployment</p>
                        <p className="mt-1">
                            The encryption secret is not configured, so there is nowhere safe to
                            put a key. Your administrator needs to set
                            PLATFORM_CREDENTIAL_SECRET. Agents will keep running on managed
                            models in the meantime.
                        </p>
                    </div>
                </div>
            )}

            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {notice && (
                <div
                    className={
                        notice.kind === "verified"
                            ? "flex items-start gap-2 rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-400"
                            : "flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-400"
                    }
                >
                    {notice.kind === "verified" ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                    ) : (
                        <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" />
                    )}
                    {notice.text}
                </div>
            )}

            {gcalMessage && (
                <div
                    className={
                        gcalMessage.kind === "success"
                            ? "flex items-center gap-2 rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-400"
                            : "rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
                    }
                >
                    {gcalMessage.kind === "success" && <CheckCircle2 className="h-4 w-4 shrink-0" />}
                    {gcalMessage.text}
                </div>
            )}

            {/* Categories as a filter over one list of vendors, which is what
                makes them a way of reading rather than three things to fill in. */}
            <div className="flex flex-wrap items-center gap-2">
                <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>
                    All providers
                    <span className="ml-1.5 text-xs opacity-70">{providers.length}</span>
                </FilterChip>
                {COMPONENTS.map((component) => (
                    <FilterChip
                        key={component.value}
                        active={filter === component.value}
                        onClick={() => setFilter(component.value)}
                    >
                        {component.title}
                        <span className="ml-1.5 text-xs opacity-70">
                            {providers.filter((row) => row.serves.includes(component.value)).length}
                        </span>
                    </FilterChip>
                ))}
                <span className="ml-auto text-xs text-muted-foreground">
                    {connectedCount === 0
                        ? "Nothing connected — everything runs on Decibyl's keys"
                        : `${connectedCount} connected`}
                </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
                {visibleProviders.map((row) => {
                    const connected = row.stored.length > 0;
                    const paused = connected && row.stored.every((c) => !c.is_active);
                    return (
                        <Card key={row.provider} className="flex flex-col">
                            <CardHeader className="space-y-1 pb-3">
                                <div className="flex items-center justify-between gap-2">
                                    <CardTitle className="text-base">
                                        {providerLabel(row.provider)}
                                    </CardTitle>
                                    {connected ? (
                                        paused ? (
                                            <Badge variant="secondary">Paused</Badge>
                                        ) : (
                                            <Badge className="bg-green-600 hover:bg-green-600">
                                                Connected
                                            </Badge>
                                        )
                                    ) : (
                                        <Badge variant="outline" className="text-muted-foreground">
                                            Not connected
                                        </Badge>
                                    )}
                                </div>
                                <CardDescription>
                                    Covers {describeComponents(row.serves)}
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="mt-auto space-y-3">
                                {connected ? (
                                    <>
                                        <p className="font-mono text-xs text-muted-foreground">
                                            {row.stored[0].masked_key}
                                            {row.stored[0].label ? ` · ${row.stored[0].label}` : ""}
                                        </p>
                                        {/* When a vendor serves several slots but only
                                            some are on this key, say so rather than
                                            implying full coverage. */}
                                        {row.stored.length < row.serves.length && (
                                            <p className="text-xs text-amber-700 dark:text-amber-400">
                                                Used for{" "}
                                                {describeComponents(row.stored.map((c) => c.component))}{" "}
                                                only.
                                            </p>
                                        )}
                                        {/* What the key is actually for. A key
                                            that reaches only models Decibyl
                                            already provides adds nothing, and
                                            saying so is more useful than letting
                                            someone keep a key they do not need. */}
                                        {!paused && (
                                            <UnlockedModels
                                                provider={row.provider}
                                                component={row.stored[0].component}
                                            />
                                        )}
                                        {isOrganizationAdmin && (
                                            <div className="flex flex-wrap items-center gap-3">
                                                {/* Pausing rather than deleting is what you
                                                    want while rotating at the vendor. */}
                                                <div className="flex items-center gap-2">
                                                    <Switch
                                                        checked={!paused}
                                                        onCheckedChange={(checked) =>
                                                            setProviderActive(row, checked)
                                                        }
                                                        aria-label={`Use the ${row.provider} key`}
                                                    />
                                                    <span className="text-xs text-muted-foreground">
                                                        {paused ? "Paused" : "In use"}
                                                    </span>
                                                </div>
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => openDialog(row)}
                                                    disabled={!encryptionConfigured}
                                                >
                                                    Replace key
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => removeProvider(row)}
                                                    aria-label={`Disconnect ${row.provider}`}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        )}
                                        {!isOrganizationAdmin && (
                                            <span className="text-xs text-muted-foreground">
                                                {paused ? "Paused" : "In use"}
                                            </span>
                                        )}
                                    </>
                                ) : (
                                    <>
                                        <p className="flex items-start gap-2 text-xs text-muted-foreground">
                                            <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                            <span>Runs on Decibyl&apos;s key.</span>
                                        </p>
                                        {isOrganizationAdmin && (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => openDialog(row)}
                                                disabled={!encryptionConfigured}
                                            >
                                                Connect
                                            </Button>
                                        )}
                                    </>
                                )}
                            </CardContent>
                        </Card>
                    );
                })}
            </div>

            <Card>
                <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                    <div>
                        <CardTitle className="text-lg">Google Calendar</CardTitle>
                        <CardDescription>
                            Let an agent book real events on a connected Google Calendar. Sign in
                            with Google — no API key.
                        </CardDescription>
                    </div>
                    {gcalLoading ? (
                        <Skeleton className="h-9 w-32" />
                    ) : gcalStatus?.connected ? (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={disconnectGoogleCalendar}
                            disabled={gcalBusy}
                        >
                            {gcalBusy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                            Disconnect
                        </Button>
                    ) : (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={connectGoogleCalendar}
                            disabled={gcalBusy || !gcalStatus?.configured}
                        >
                            {gcalBusy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                            Connect Google Calendar
                        </Button>
                    )}
                </CardHeader>
                <CardContent>
                    {gcalLoading ? (
                        <Skeleton className="h-5 w-64" />
                    ) : gcalStatus?.connected ? (
                        <p className="flex items-start gap-2 text-sm text-muted-foreground">
                            <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <span>
                                Connected as{" "}
                                <span className="text-foreground">{gcalStatus.connected_email}</span>.
                                New events go on this account&apos;s{" "}
                                {gcalStatus.calendar_id === "primary" ? "primary" : gcalStatus.calendar_id}{" "}
                                calendar.
                            </span>
                        </p>
                    ) : gcalStatus?.configured ? (
                        <p className="flex items-start gap-2 text-sm text-muted-foreground">
                            <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <span>
                                Not connected. Connect a Google account to let agents use the
                                Google Calendar tool.
                            </span>
                        </p>
                    ) : (
                        <p className="flex items-start gap-2 text-sm text-muted-foreground">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <span>
                                Not available on this deployment yet — an administrator needs to
                                configure GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET.
                            </span>
                        </p>
                    )}
                </CardContent>
            </Card>

            <p className="text-xs text-muted-foreground">
                {!rolesLoaded || isOrganizationAdmin ? (
                    <>
                        Keys are checked against the vendor when you connect, then encrypted
                        and never shown again — only the last four characters. To change one,
                        connect it again.
                    </>
                ) : (
                    // Says what a member can do here rather than leaving them to
                    // discover the missing buttons: they came to find out which
                    // keys exist so they can pick one in a model slot, and that
                    // still works. Connecting and removing needs an admin.
                    <>
                        Keys are stored encrypted and only ever shown as their last four
                        characters. You can see which vendors this account is connected to and
                        choose them in a model slot; connecting, pausing or removing one needs
                        an Owner or Admin.
                    </>
                )}
            </p>

            <Dialog
                open={dialogProvider !== null}
                onOpenChange={(open) => !open && setDialogProvider(null)}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>
                            Connect {dialogProvider ? providerLabel(dialogProvider.provider) : ""}
                        </DialogTitle>
                        <DialogDescription>
                            Paste the key from your{" "}
                            {dialogProvider ? providerLabel(dialogProvider.provider) : ""} account.
                            We check it with them before storing it. It is encrypted immediately
                            and cannot be read back.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="provider-api-key">API key</Label>
                            <Input
                                id="provider-api-key"
                                type="password"
                                autoComplete="off"
                                value={formKey}
                                onChange={(event) => setFormKey(event.currentTarget.value)}
                                placeholder="Paste the key"
                            />
                        </div>

                        {dialogProvider && dialogProvider.serves.length > 1 && (
                            <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-sm">
                                {!advanced ? (
                                    <div className="flex items-start justify-between gap-3">
                                        <span>
                                            <span className="font-medium">
                                                Used for {describeComponents(dialogProvider.serves)}
                                            </span>
                                            <span className="block text-xs text-muted-foreground">
                                                One account normally means one key.
                                            </span>
                                        </span>
                                        <button
                                            type="button"
                                            className="shrink-0 text-xs underline text-muted-foreground"
                                            onClick={() => setAdvanced(true)}
                                        >
                                            Use a different key per part
                                        </button>
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        <p className="text-xs text-muted-foreground">
                                            Choose what this key is for. Do this when you hold
                                            separate {providerLabel(dialogProvider.provider)} keys on
                                            different billing.
                                        </p>
                                        {dialogProvider.serves.map((component) => (
                                            <label
                                                key={component}
                                                className="flex items-center gap-2.5"
                                            >
                                                <input
                                                    type="checkbox"
                                                    className="h-4 w-4 shrink-0 accent-[color:var(--primary)]"
                                                    checked={chosenComponents.includes(component)}
                                                    onChange={(event) =>
                                                        setChosenComponents((current) =>
                                                            event.currentTarget.checked
                                                                ? [...current, component]
                                                                : current.filter((c) => c !== component),
                                                        )
                                                    }
                                                />
                                                <span>
                                                    {COMPONENTS.find((c) => c.value === component)?.title}
                                                </span>
                                            </label>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        <div className="space-y-2">
                            <Label htmlFor="provider-key-label">Label (optional)</Label>
                            <Input
                                id="provider-key-label"
                                value={formLabel}
                                onChange={(event) => setFormLabel(event.currentTarget.value)}
                                placeholder="e.g. Production account"
                            />
                        </div>

                        {formError && (
                            <p className="text-sm text-destructive">{formError}</p>
                        )}
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogProvider(null)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={saveKey}
                            disabled={saving || formKey.trim().length < 8}
                        >
                            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            {saving ? "Checking the key…" : "Connect"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
        </>
    );

    async function connectGoogleCalendar() {
        setGcalBusy(true);
        setGcalMessage(null);
        const accessToken = await auth.getAccessToken();
        const result = await googleCalendarFetch("/authorize-url", accessToken);
        if (result.error || !result.data) {
            setGcalMessage({
                kind: "error",
                text: (result.error as string) || "Could not start the connect flow",
            });
            setGcalBusy(false);
            return;
        }
        window.location.href = (result.data as { url: string }).url;
    }

    async function disconnectGoogleCalendar() {
        setGcalBusy(true);
        setGcalMessage(null);
        const accessToken = await auth.getAccessToken();
        const result = await googleCalendarFetch("/disconnect", accessToken, { method: "POST" });
        if (result.error) {
            setGcalMessage({ kind: "error", text: result.error });
        } else {
            setGcalMessage({ kind: "success", text: "Google Calendar disconnected." });
            await loadGoogleCalendarStatus();
        }
        setGcalBusy(false);
    }
}
