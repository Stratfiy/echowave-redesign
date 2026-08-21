"use client";

/**
 * Decibyl's own provider API keys.
 *
 * These are what make an account "managed": a customer who does not bring their
 * own OpenAI or Deepgram key runs on ours, and that usage is metered and passed
 * through at cost on their receipt.
 *
 * The screen is deliberately write-only. A stored key is shown as its last four
 * characters and nothing else — enough to confirm *which* key is installed,
 * never enough to take it. A reveal button here would turn one compromised
 * staff session into every provider key on the platform.
 *
 * One card per vendor, the same grid the customer's own Integrations screen
 * draws — both read the same registry (`known_providers()`), so a vendor
 * added there shows up here with no second screen to remember to build. What
 * this screen adds over the customer one: connecting reveals, immediately and
 * per component, exactly which models that vendor's key unlocks — read from
 * the vendor live where we can, so there is no second place that goes stale
 * the moment a vendor ships a new model.
 */

import {
    AlertTriangle,
    CheckCircle2,
    KeyRound,
    Loader2,
    ShieldQuestion,
    Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { client } from "@/client/client.gen";
import {
    deleteProviderKeyApiV1AdminProviderKeysDelete,
    listKnownProvidersApiV1AdminProviderKeysProvidersGet,
    listProviderKeysApiV1AdminProviderKeysGet,
    setProviderKeyActiveApiV1AdminProviderKeysActivePost,
    setProviderKeyApiV1AdminProviderKeysPut,
} from "@/client/sdk.gen";
import {
    type CatalogueRow,
    CatalogueSummary,
    ModelCatalogue,
} from "@/components/billing/ModelCatalogue";
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
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Credential = {
    id: number;
    component: string;
    provider: string;
    masked_key: string;
    label: string | null;
    is_active: boolean;
    updated_at: string | null;
};

type Payload = {
    credentials: Credential[];
    encryption_configured: boolean;
};

interface ProviderRow {
    provider: string;
    /** Every slot this vendor can serve, from the registry. */
    serves: ComponentValue[];
    /** What is stored today, one row per component. */
    stored: Credential[];
}

export default function ProviderKeysPage() {
    return <ProviderKeysScreen />;
}

function ProviderKeysScreen() {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && Boolean(user);

    const [payload, setPayload] = useState<Payload | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filter, setFilter] = useState<"all" | ComponentValue>("all");

    // Every vendor this codebase can key, and what each one serves —
    // ``{ sarvam: ["llm", "stt", "tts"], groq: ["llm"], ... }`` — read off the
    // same registry the customer screen uses, so the two cannot disagree
    // about what a vendor covers.
    const [knownProviders, setKnownProviders] = useState<Record<string, ComponentValue[]>>({});

    // What Decibyl currently sells, across every connected vendor — the
    // aggregate view. A key installed with nothing ticked buys nothing; a
    // model ticked and unpriced bills the platform fee alone and reports
    // margin we did not earn. Both are invisible without this.
    const [catalogue, setCatalogue] = useState<CatalogueRow[]>([]);

    const loadCatalogue = useCallback(async () => {
        const result = await client.get({ url: "/api/v1/admin/provider-keys/catalogue" });
        if (result.error) return;
        setCatalogue(((result.data as { models: CatalogueRow[] })?.models) ?? []);
    }, []);

    const load = useCallback(async () => {
        const result = await listProviderKeysApiV1AdminProviderKeysGet({});
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load provider keys"));
        } else {
            setPayload(result.data as unknown as Payload);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
        void loadCatalogue();
        void (async () => {
            const result = await listKnownProvidersApiV1AdminProviderKeysProvidersGet({});
            if (result.error || !result.data) return;
            const rows = (result.data as unknown as {
                providers: { provider: string; components: ComponentValue[] }[];
            }).providers;
            setKnownProviders(Object.fromEntries(rows.map((r) => [r.provider, r.components])));
        })();
    }, [authReady, load, loadCatalogue]);

    // The vendor being connected. Provider-first, same as the customer
    // screen: the dialog opens already knowing who it is for, and asks only
    // for the key.
    const [dialogProvider, setDialogProvider] = useState<ProviderRow | null>(null);
    const [formKey, setFormKey] = useState("");
    const [formLabel, setFormLabel] = useState("");
    // The escape hatch, off by default — see the customer screen for why.
    const [advanced, setAdvanced] = useState(false);
    const [chosenComponents, setChosenComponents] = useState<ComponentValue[]>([]);
    const [saving, setSaving] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [notice, setNotice] = useState<
        { kind: "verified" | "unverified"; text: string } | null
    >(null);

    const [actionBusy, setActionBusy] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    /** One row per vendor, not per slot — the registry already knows which
     * components each vendor serves, so the grid asks "which vendor" and
     * derives the rest, instead of three near-identical rows for one Sarvam
     * account. */
    const providers: ProviderRow[] = useMemo(() => {
        const credentials = payload?.credentials ?? [];
        return Object.entries(knownProviders)
            .map(([provider, components]) => ({
                provider,
                serves: components,
                stored: credentials.filter((c) => c.provider === provider),
            }))
            .sort((a, b) => {
                const connected = Number(b.stored.length > 0) - Number(a.stored.length > 0);
                return connected || providerLabel(a.provider).localeCompare(providerLabel(b.provider));
            });
    }, [knownProviders, payload]);

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

        const components = advanced ? chosenComponents : dialogProvider.serves;
        if (components.length === 0) {
            setFormError("Choose at least one thing this key is for.");
            setSaving(false);
            return;
        }

        const coversEverything = components.length === dialogProvider.serves.length;

        const result = await setProviderKeyApiV1AdminProviderKeysPut({
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
                const extra = await setProviderKeyApiV1AdminProviderKeysPut({
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

        // Whether the vendor actually confirmed the key, or we only stored
        // it. Showing the same tick for both would make a Connect button a
        // lie, and a platform key is worse to get wrong than a customer's —
        // it is what every managed account runs on.
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
        setActionBusy(`delete-${row.provider}`);
        setActionError(null);
        for (const credential of row.stored) {
            const result = await deleteProviderKeyApiV1AdminProviderKeysDelete({
                query: { component: credential.component, provider: credential.provider },
            });
            if (result.error) {
                setActionError(detailFromError(result.error, "Failed to remove the key"));
                await load();
                setActionBusy(null);
                return;
            }
        }
        await load();
        setActionBusy(null);
    };

    const setProviderActive = async (row: ProviderRow, isActive: boolean) => {
        setActionBusy(`active-${row.provider}`);
        setActionError(null);
        for (const credential of row.stored) {
            const result = await setProviderKeyActiveApiV1AdminProviderKeysActivePost({
                body: {
                    component: credential.component,
                    provider: credential.provider,
                    is_active: isActive,
                },
            });
            if (result.error) {
                setActionError(detailFromError(result.error, "Failed to update the key"));
                await load();
                setActionBusy(null);
                return;
            }
        }
        await load();
        setActionBusy(null);
    };

    if (loading) {
        return (
            <div className="glass-canvas min-h-full">
                <div className="mx-auto w-full max-w-[1100px] px-6 pt-8">
                    <Skeleton className="h-10 w-72" />
                    <Skeleton className="mt-4 h-48 w-full rounded-2xl" />
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="glass-canvas min-h-full">
                <div className="mx-auto w-full max-w-[1100px] px-6 pt-8">
                    <section className="glass-panel flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4 shrink-0" />
                        {error}
                    </section>
                </div>
            </div>
        );
    }

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[1100px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Provider keys
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Every vendor this codebase integrates, in one grid. Connect one and
                        every account without its own key runs on it — metered and passed
                        through at cost on their receipt.
                    </p>
                </header>

                {!payload?.encryption_configured && (
                    <section className="glass-panel mb-5 px-5 py-4 ring-1 ring-destructive/35">
                        <p className="flex items-center gap-2 text-sm font-medium text-destructive">
                            <AlertTriangle className="h-4 w-4" />
                            Keys cannot be stored yet
                        </p>
                        <p className="mt-1.5 text-sm text-muted-foreground">
                            <code className="rounded bg-foreground/[0.06] px-1.5 py-0.5 text-xs">
                                PLATFORM_CREDENTIAL_SECRET
                            </code>{" "}
                            is not set, so there is nothing to encrypt them with. Generate one
                            with{" "}
                            <code className="rounded bg-foreground/[0.06] px-1.5 py-0.5 text-xs">
                                python -c &quot;from cryptography.fernet import Fernet;
                                print(Fernet.generate_key().decode())&quot;
                            </code>{" "}
                            and set it on the API. There is deliberately no default — one
                            would mean every key on the platform encrypted under a value
                            published in the source.
                        </p>
                    </section>
                )}

                {actionError && (
                    <div className="mb-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        {actionError}
                    </div>
                )}

                {notice && (
                    <div
                        className={
                            notice.kind === "verified"
                                ? "mb-4 flex items-start gap-2 rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-400"
                                : "mb-4 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-400"
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

                {catalogue.length > 0 && (
                    <section className="glass-panel mb-5 px-5 py-4">
                        <h2 className="mb-3 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                            What we&apos;re selling
                        </h2>
                        <CatalogueSummary rows={catalogue} />
                    </section>
                )}

                {/* Categories as a filter over one list of vendors, not three lists
                    to fill in one at a time — the same inversion the customer
                    screen makes, over the same registry. */}
                <div className="mb-4 flex flex-wrap items-center gap-2">
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
                            ? "Nothing connected yet"
                            : `${connectedCount} connected`}
                    </span>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                    {visibleProviders.map((row) => {
                        const connected = row.stored.length > 0;
                        const paused = connected && row.stored.every((c) => !c.is_active);
                        const busy =
                            actionBusy === `delete-${row.provider}` ||
                            actionBusy === `active-${row.provider}`;
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
                                            {row.stored.length < row.serves.length && (
                                                <p className="text-xs text-amber-700 dark:text-amber-400">
                                                    Used for{" "}
                                                    {describeComponents(row.stored.map((c) => c.component))}{" "}
                                                    only.
                                                </p>
                                            )}
                                            <div className="flex flex-wrap items-center gap-3">
                                                <div className="flex items-center gap-2">
                                                    <Switch
                                                        checked={!paused}
                                                        onCheckedChange={(checked) =>
                                                            setProviderActive(row, checked)
                                                        }
                                                        disabled={busy}
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
                                                    disabled={!payload?.encryption_configured}
                                                >
                                                    Replace key
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => removeProvider(row)}
                                                    disabled={busy}
                                                    aria-label={`Disconnect ${row.provider}`}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                            {/* What this key actually unlocks, read live from
                                                the vendor where we can — one catalogue per slot
                                                it covers, ready to tick immediately rather than
                                                behind a chip to click first. */}
                                            {!paused && (
                                                <div className="space-y-4 border-t pt-3">
                                                    {row.stored.map((credential) => (
                                                        <ModelCatalogue
                                                            key={credential.component}
                                                            component={credential.component}
                                                            provider={row.provider}
                                                            onSaved={() => void loadCatalogue()}
                                                        />
                                                    ))}
                                                </div>
                                            )}
                                        </>
                                    ) : (
                                        <>
                                            <p className="flex items-start gap-2 text-xs text-muted-foreground">
                                                <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                <span>No platform key — nothing here runs on this vendor yet.</span>
                                            </p>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => openDialog(row)}
                                                disabled={!payload?.encryption_configured}
                                            >
                                                Connect
                                            </Button>
                                        </>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>

                <p className="mt-6 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                    <KeyRound className="mt-0.5 h-3 w-3 shrink-0" />
                    Keys are checked against the vendor when you connect, then encrypted
                    and never shown again — only the last four characters. To change one,
                    connect it again. Pausing stops it being used without discarding it;
                    accounts on that provider without their own key fail until it is
                    resumed, so check Unit economics before pausing something in service.
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
                                Paste Decibyl&apos;s{" "}
                                {dialogProvider ? providerLabel(dialogProvider.provider) : ""} key. We
                                check it with the vendor before storing it. It is encrypted
                                immediately and cannot be read back.
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
                                                    One vendor account normally means one key.
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
                                                Choose what this key is for. Do this when Decibyl
                                                holds separate {providerLabel(dialogProvider.provider)}{" "}
                                                keys on different billing.
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
        </div>
    );
}
