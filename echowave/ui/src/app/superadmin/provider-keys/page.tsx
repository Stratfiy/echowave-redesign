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
 */

import {
    AlertTriangle,
    Check,
    Eye,
    KeyRound,
    Loader2,
    Plus,
    Trash2,
    X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    deleteProviderKeyApiV1AdminProviderKeysDelete,
    listProviderKeysApiV1AdminProviderKeysGet,
    setProviderKeyActiveApiV1AdminProviderKeysActivePost,
    setProviderKeyApiV1AdminProviderKeysPut,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTimeIST } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

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
    components: string[];
};

const COMPONENT_LABELS: Record<string, string> = {
    stt: "Speech to text",
    llm: "Language model",
    tts: "Text to speech",
};

/** Providers we already price, so the form suggests names the rate card knows. */
const SUGGESTED: Record<string, string[]> = {
    llm: ["openai", "anthropic", "azure", "google"],
    stt: ["deepgram", "sarvam", "elevenlabs", "azure"],
    tts: ["elevenlabs", "cartesia", "sarvam", "smallest"],
};

export default function ProviderKeysPage() {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && Boolean(user);

    const [payload, setPayload] = useState<Payload | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const [adding, setAdding] = useState(false);
    const [component, setComponent] = useState("llm");
    const [provider, setProvider] = useState("");
    const [apiKey, setApiKey] = useState("");
    const [label, setLabel] = useState("");

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
    }, [authReady, load]);

    const run = async (
        key: string,
        call: () => Promise<{ error?: unknown }>,
        fallback: string,
    ) => {
        setBusy(key);
        setActionError(null);
        setSaved(false);
        const result = await call();
        if (result.error) {
            setActionError(detailFromError(result.error, fallback));
        } else {
            await load();
            setSaved(true);
            setTimeout(() => setSaved(false), 2500);
        }
        setBusy(null);
    };

    const submit = () => {
        if (!provider.trim() || apiKey.trim().length < 8) return;
        void run(
            "save",
            () =>
                setProviderKeyApiV1AdminProviderKeysPut({
                    body: {
                        component,
                        provider: provider.trim(),
                        api_key: apiKey.trim(),
                        label: label.trim() || null,
                    },
                }),
            "Could not save that key",
        );
        setAdding(false);
        setProvider("");
        setApiKey("");
        setLabel("");
    };

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
                        Our own API keys for the model providers. A customer
                        without their own key runs on these, and the usage is
                        metered and passed through at cost on their invoice.
                    </p>
                </header>

                {loading ? (
                    <Skeleton className="h-[400px] w-full rounded-2xl" />
                ) : (
                    <>
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
                                    is not set, so there is nothing to encrypt
                                    them with. Generate one with{" "}
                                    <code className="rounded bg-foreground/[0.06] px-1.5 py-0.5 text-xs">
                                        python -c &quot;from cryptography.fernet import
                                        Fernet; print(Fernet.generate_key().decode())&quot;
                                    </code>{" "}
                                    and set it on the API. There is deliberately
                                    no default — one would mean every key on the
                                    platform encrypted under a value published in
                                    the source.
                                </p>
                            </section>
                        )}

                        <section className="glass-panel px-6 pb-6 pt-5">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <h2 className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                                        <KeyRound className="h-4 w-4 text-[color:var(--brand-amber)]" />
                                        Stored keys
                                    </h2>
                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                        Write-only. Once saved a key cannot be read
                                        back — rotate it instead.
                                    </p>
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={!payload?.encryption_configured}
                                    onClick={() => setAdding((v) => !v)}
                                >
                                    {adding ? (
                                        <X className="mr-2 h-3.5 w-3.5" />
                                    ) : (
                                        <Plus className="mr-2 h-3.5 w-3.5" />
                                    )}
                                    {adding ? "Cancel" : "Add or rotate"}
                                </Button>
                            </div>

                            {adding && (
                                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                                    <div className="space-y-1.5">
                                        <Label htmlFor="pk-component">Component</Label>
                                        <Select
                                            value={component}
                                            onValueChange={setComponent}
                                        >
                                            <SelectTrigger id="pk-component">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {(payload?.components ?? []).map((c) => (
                                                    <SelectItem key={c} value={c}>
                                                        {COMPONENT_LABELS[c] ?? c}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor="pk-provider">Provider</Label>
                                        <Input
                                            id="pk-provider"
                                            list="pk-provider-options"
                                            value={provider}
                                            onChange={(e) => setProvider(e.target.value)}
                                            placeholder="openai"
                                        />
                                        <datalist id="pk-provider-options">
                                            {(SUGGESTED[component] ?? []).map((p) => (
                                                <option key={p} value={p} />
                                            ))}
                                        </datalist>
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor="pk-key">API key</Label>
                                        <Input
                                            id="pk-key"
                                            type="password"
                                            autoComplete="off"
                                            value={apiKey}
                                            onChange={(e) => setApiKey(e.target.value)}
                                            placeholder="sk-…"
                                        />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor="pk-label">Label</Label>
                                        <div className="flex gap-2">
                                            <Input
                                                id="pk-label"
                                                value={label}
                                                onChange={(e) => setLabel(e.target.value)}
                                                placeholder="optional"
                                            />
                                            <Button
                                                aria-label="Save provider key"
                                                disabled={busy !== null}
                                                onClick={submit}
                                            >
                                                {busy === "save" ? (
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                ) : (
                                                    <Check className="h-4 w-4" />
                                                )}
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {(payload?.credentials.length ?? 0) === 0 ? (
                                <p className="mt-4 text-sm text-muted-foreground">
                                    No keys stored. Every account is bring-your-own
                                    until you add one.
                                </p>
                            ) : (
                                <div className="mt-4 overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Component</TableHead>
                                                <TableHead>Provider</TableHead>
                                                <TableHead>Key</TableHead>
                                                <TableHead>Label</TableHead>
                                                <TableHead>Updated</TableHead>
                                                <TableHead className="text-right" />
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {payload!.credentials.map((c) => (
                                                <TableRow
                                                    key={c.id}
                                                    className={cn(
                                                        !c.is_active && "opacity-55",
                                                    )}
                                                >
                                                    <TableCell className="text-xs uppercase tracking-[0.05em] text-muted-foreground">
                                                        {c.component}
                                                    </TableCell>
                                                    <TableCell className="font-medium">
                                                        {c.provider}
                                                        {!c.is_active && (
                                                            <Badge
                                                                variant="secondary"
                                                                className="ml-2"
                                                            >
                                                                paused
                                                            </Badge>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="font-mono text-sm">
                                                        {c.masked_key}
                                                    </TableCell>
                                                    <TableCell className="text-muted-foreground">
                                                        {c.label ?? "—"}
                                                    </TableCell>
                                                    <TableCell className="text-muted-foreground">
                                                        {c.updated_at
                                                            ? formatDateTimeIST(c.updated_at)
                                                            : "—"}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            disabled={busy !== null}
                                                            onClick={() =>
                                                                run(
                                                                    `active-${c.id}`,
                                                                    () =>
                                                                        setProviderKeyActiveApiV1AdminProviderKeysActivePost(
                                                                            {
                                                                                body: {
                                                                                    component:
                                                                                        c.component,
                                                                                    provider:
                                                                                        c.provider,
                                                                                    is_active:
                                                                                        !c.is_active,
                                                                                },
                                                                            },
                                                                        ),
                                                                    "Could not change that key",
                                                                )
                                                            }
                                                        >
                                                            {c.is_active ? "Pause" : "Resume"}
                                                        </Button>
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            aria-label={`Delete ${c.provider} ${c.component} key`}
                                                            disabled={busy !== null}
                                                            onClick={() =>
                                                                run(
                                                                    `delete-${c.id}`,
                                                                    () =>
                                                                        deleteProviderKeyApiV1AdminProviderKeysDelete(
                                                                            {
                                                                                query: {
                                                                                    component:
                                                                                        c.component,
                                                                                    provider:
                                                                                        c.provider,
                                                                                },
                                                                            },
                                                                        ),
                                                                    "Could not delete that key",
                                                                )
                                                            }
                                                        >
                                                            <Trash2 className="h-3.5 w-3.5" />
                                                        </Button>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}

                            {actionError && (
                                <p className="mt-3 flex items-start gap-2 text-sm text-destructive">
                                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                    {actionError}
                                </p>
                            )}
                            {saved && (
                                <p className="mt-3 flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
                                    <Check className="h-3.5 w-3.5" />
                                    Saved.
                                </p>
                            )}

                            <p className="mt-4 flex items-start gap-1.5 border-t border-foreground/[0.07] pt-3 text-xs leading-relaxed text-muted-foreground">
                                <Eye className="mt-0.5 h-3 w-3 shrink-0" />
                                Pausing a key stops it being used without discarding
                                it. Accounts on that provider fall back to their own
                                key if they have one, and fail if they do not — so
                                check the rate card and Unit economics before pausing
                                something in service.
                            </p>
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}
