"use client";

import { AlertTriangle, CalendarClock, CheckCircle2, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    deleteProviderKeyApiV1ProviderKeysDelete,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    listProviderKeysApiV1ProviderKeysGet,
    setProviderKeyActiveApiV1ProviderKeysActivePost,
    setProviderKeyApiV1ProviderKeysPut,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
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

// The three slots a key can serve. Telephony is deliberately absent: carrier
// credentials live on a telephony configuration, which also models the KYC that
// comes with a phone number.
const COMPONENTS = [
    {
        value: "stt",
        title: "Transcriber",
        blurb: "Turns what the caller says into text.",
    },
    {
        value: "llm",
        title: "Language model",
        blurb: "Decides what the agent says next. Also used for speech-to-speech models and embeddings.",
    },
    {
        value: "tts",
        title: "Voice",
        blurb: "Speaks the agent's replies.",
    },
] as const;

/** The slot's own name, as the cards above already title it. */
function componentLabel(component: string): string {
    return (
        COMPONENTS.find((c) => c.value === component)?.title.toLowerCase() ?? component
    );
}

// Vendors capitalise their own names, and title-casing produces "Openai" and
// "Elevenlabs" — which look like we do not know who we are integrating with.
// Only the ones whose casing a naive transform gets wrong are listed; anything
// missing falls back to title case, so a new provider still reads sensibly.
const PROVIDER_NAMES: Record<string, string> = {
    openai: "OpenAI",
    openai_realtime: "OpenAI Realtime",
    elevenlabs: "ElevenLabs",
    openrouter: "OpenRouter",
    aws_bedrock: "AWS Bedrock",
    google_vertex: "Google Vertex",
    huggingface: "Hugging Face",
    minimax: "MiniMax",
    xai: "xAI",
};

function providerLabel(provider: string): string {
    return (
        PROVIDER_NAMES[provider] ??
        provider
            .split("_")
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(" ")
    );
}

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

export default function ProviderKeysPage() {
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
    return <ProviderKeysScreen />;
}

function ProviderKeysScreen() {
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

    const [gcalStatus, setGcalStatus] = useState<GoogleCalendarStatus | null>(null);
    const [gcalLoading, setGcalLoading] = useState(true);
    const [gcalBusy, setGcalBusy] = useState(false);
    const [gcalMessage, setGcalMessage] = useState<
        { kind: "success" | "error"; text: string } | null
    >(null);

    const [dialogComponent, setDialogComponent] = useState<string | null>(null);
    const [formProvider, setFormProvider] = useState("");
    const [formKey, setFormKey] = useState("");
    const [formLabel, setFormLabel] = useState("");
    // On by default: a vendor account is normally one account, and the same key
    // works across everything it serves. The customer who genuinely holds two
    // keys with one vendor turns it off.
    const [formApplyAll, setFormApplyAll] = useState(true);
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);

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
        router.replace("/provider-keys");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const connectGoogleCalendar = async () => {
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
    };

    const disconnectGoogleCalendar = async () => {
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
    };

    const openDialog = (component: string) => {
        setDialogComponent(component);
        setFormProvider("");
        setFormKey("");
        setFormLabel("");
        setFormError(null);
    };

    // The other slots this vendor also serves, read off the same provider lists
    // the pickers use — so the offer can never name a component the vendor does
    // not actually support.
    const alsoServes = dialogComponent
        ? ["stt", "llm", "tts"].filter(
              (component) =>
                  component !== dialogComponent &&
                  (providersByComponent[component] ?? []).includes(formProvider),
          )
        : [];

    const saveKey = async () => {
        if (!dialogComponent) return;
        setSaving(true);
        setFormError(null);

        const result = await setProviderKeyApiV1ProviderKeysPut({
            body: {
                component: dialogComponent,
                provider: formProvider,
                api_key: formKey.trim(),
                label: formLabel.trim() || null,
                apply_to_all_components: formApplyAll && alsoServes.length > 0,
            },
        });

        if (result.error) {
            setFormError(detailFromError(result.error, "Failed to save the key"));
            setSaving(false);
            return;
        }

        setSaving(false);
        setDialogComponent(null);
        await load();
    };

    const toggleActive = async (credential: ProviderCredential, isActive: boolean) => {
        const result = await setProviderKeyActiveApiV1ProviderKeysActivePost({
            body: {
                component: credential.component,
                provider: credential.provider,
                is_active: isActive,
            },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to update the key"));
            return;
        }
        await load();
    };

    const removeKey = async (credential: ProviderCredential) => {
        const result = await deleteProviderKeyApiV1ProviderKeysDelete({
            query: { component: credential.component, provider: credential.provider },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to remove the key"));
            return;
        }
        await load();
    };

    if (loading) {
        return (
            <div className="container mx-auto max-w-4xl px-4 py-8 space-y-6">
                <Skeleton className="h-10 w-72" />
                <Skeleton className="h-48 w-full" />
                <Skeleton className="h-48 w-full" />
            </div>
        );
    }

    return (
        <div className="container mx-auto max-w-4xl px-4 py-8 space-y-6">
            <div>
                <h1 className="text-3xl font-bold">Provider keys</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                    Your own API keys for the model vendors you have accounts with. Store a
                    key here once, then choose it on any agent.{" "}
                    <span className="text-foreground">
                        Anything you do not store a key for runs on Decibyl&apos;s keys and is
                        billed at the published rate
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

            {COMPONENTS.map((component) => {
                const stored = credentials.filter((c) => c.component === component.value);
                return (
                    <Card key={component.value}>
                        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                            <div>
                                <CardTitle className="text-lg">{component.title}</CardTitle>
                                <CardDescription>{component.blurb}</CardDescription>
                            </div>
                            {isOrganizationAdmin && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => openDialog(component.value)}
                                    disabled={!encryptionConfigured}
                                >
                                    <Plus className="mr-1.5 h-4 w-4" />
                                    Add key
                                </Button>
                            )}
                        </CardHeader>
                        <CardContent>
                            {stored.length === 0 ? (
                                <p className="flex items-start gap-2 text-sm text-muted-foreground">
                                    <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                    <span>
                                        No key stored. Agents using {component.title.toLowerCase()}{" "}
                                        run on Decibyl&apos;s key.
                                    </span>
                                </p>
                            ) : (
                                <ul className="divide-y">
                                    {stored.map((credential) => (
                                        <li
                                            key={credential.id}
                                            className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                                        >
                                            <div className="min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="font-medium">
                                                        {providerLabel(credential.provider)}
                                                    </span>
                                                    {!credential.is_active && (
                                                        <Badge variant="secondary">Paused</Badge>
                                                    )}
                                                </div>
                                                <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                                                    {credential.masked_key}
                                                    {credential.label ? ` · ${credential.label}` : ""}
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-3">
                                                {/* Pausing rather than deleting is what you want
                                                    while rotating at the vendor: agents fail over
                                                    to managed instead of authenticating with a key
                                                    that is being revoked. */}
                                                {isOrganizationAdmin ? (
                                                    <div className="flex items-center gap-2">
                                                        <Switch
                                                            checked={credential.is_active}
                                                            onCheckedChange={(checked) =>
                                                                toggleActive(credential, checked)
                                                            }
                                                            aria-label={`Use this ${credential.provider} key`}
                                                        />
                                                        <span className="text-xs text-muted-foreground">
                                                            {credential.is_active ? "In use" : "Paused"}
                                                        </span>
                                                    </div>
                                                ) : (
                                                    // A member still needs to know whether the key
                                                    // is carrying traffic — that is the difference
                                                    // between "my slot is on our own key" and "it
                                                    // quietly fell back to Decibyl's".
                                                    <span className="text-xs text-muted-foreground">
                                                        {credential.is_active ? "In use" : "Paused"}
                                                    </span>
                                                )}
                                                {isOrganizationAdmin && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => removeKey(credential)}
                                                        aria-label={`Remove the ${credential.provider} key`}
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                )}
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </CardContent>
                    </Card>
                );
            })}

            <p className="text-xs text-muted-foreground">
                {!rolesLoaded || isOrganizationAdmin ? (
                    <>
                        Keys are encrypted before they are stored and are never shown again —
                        only the last four characters. To change one, add it again.
                    </>
                ) : (
                    // Says what a member can do here rather than leaving them to
                    // discover the missing buttons: they came to find out which
                    // keys exist so they can pick one in a model slot, and that
                    // still works. Adding and removing needs an admin.
                    <>
                        Keys are stored encrypted and only ever shown as their last four
                        characters. You can see which keys this account holds and choose them
                        in a model slot; adding, pausing or removing one needs an Owner or
                        Admin.
                    </>
                )}
            </p>

            <Dialog
                open={dialogComponent !== null}
                onOpenChange={(open) => !open && setDialogComponent(null)}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>
                            Add a{" "}
                            {COMPONENTS.find((c) => c.value === dialogComponent)?.title.toLowerCase()}{" "}
                            key
                        </DialogTitle>
                        <DialogDescription>
                            Paste the whole key. It is encrypted immediately and cannot be read
                            back.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Provider</Label>
                            <Select value={formProvider} onValueChange={setFormProvider}>
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Choose the vendor this key is for" />
                                </SelectTrigger>
                                <SelectContent>
                                    {(providersByComponent[dialogComponent ?? ""] ?? []).map(
                                        (provider) => (
                                            <SelectItem key={provider} value={provider}>
                                                {providerLabel(provider)}
                                            </SelectItem>
                                        ),
                                    )}
                                </SelectContent>
                            </Select>
                        </div>

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

                        {/* Only shown when it is true. A checkbox offering to
                            apply a key to nothing else reads as a broken
                            control. */}
                        {alsoServes.length > 0 && (
                            <label className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-sm">
                                <input
                                    type="checkbox"
                                    className="mt-0.5 h-4 w-4 shrink-0 accent-[color:var(--primary)]"
                                    checked={formApplyAll}
                                    onChange={(event) =>
                                        setFormApplyAll(event.currentTarget.checked)
                                    }
                                />
                                <span>
                                    <span className="font-medium">
                                        Also use for {alsoServes.map(componentLabel).join(" and ")}
                                    </span>
                                    <span className="block text-xs text-muted-foreground">
                                        {providerLabel(formProvider)} serves them too, and one
                                        account normally means one key. Turn this off if you hold
                                        separate keys.
                                    </span>
                                </span>
                            </label>
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
                        <Button variant="outline" onClick={() => setDialogComponent(null)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={saveKey}
                            disabled={saving || !formProvider || formKey.trim().length < 8}
                        >
                            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Save key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
