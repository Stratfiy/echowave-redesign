"use client";

/**
 * Per-model markup overrides.
 *
 * `ManagedMarkupCard` sets one multiple for every managed line on every
 * account. This narrows that for a single `(component, provider, model)` —
 * a model that is unusually cheap or expensive to us relative to what the
 * blanket multiple would charge for it — without moving anyone else's bill.
 *
 * No confirmation code, unlike the blanket markup: a single-line override
 * cannot move every account's bill at once, so it saves immediately, the
 * same way a provider rate does.
 */

import { AlertTriangle, Check, Loader2, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    clearManagedMarkupOverrideApiV1AdminBillingRateCardMarkupOverridesDelete,
    listManagedMarkupOverridesApiV1AdminBillingRateCardMarkupOverridesGet,
    setManagedMarkupOverrideApiV1AdminBillingRateCardMarkupOverridesPut,
} from "@/client/sdk.gen";
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
import { detailFromResult } from "@/lib/apiError";
import { formatDateIST } from "@/lib/billing/format";

const COMPONENTS = ["stt", "llm", "tts", "telephony", "embedding"] as const;

type Override = {
    id: number;
    provider: string;
    component: string;
    model: string;
    markup_bps: number;
    effective_from: string;
    note: string | null;
};

type State = {
    overrides: Override[];
    min_bps: number;
    max_bps: number;
};

/** Basis points to the multiple people actually say out loud. */
function asMultiple(bps: number): string {
    return `${(bps / 10000).toFixed(2).replace(/\.?0+$/, "")}×`;
}

export function MarkupOverridesCard() {
    const [state, setState] = useState<State | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);

    const [provider, setProvider] = useState("");
    const [component, setComponent] = useState<string>("llm");
    const [model, setModel] = useState("");
    const [markup, setMarkup] = useState("");
    const [note, setNote] = useState("");

    const load = useCallback(async () => {
        const result =
            await listManagedMarkupOverridesApiV1AdminBillingRateCardMarkupOverridesGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not load markup overrides"));
        } else {
            setState(result.data as unknown as State);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const draftBps = Math.round(Number(markup) * 10000);
    const draftValid =
        provider.trim().length > 0 &&
        Number.isFinite(draftBps) &&
        state !== null &&
        draftBps >= state.min_bps &&
        draftBps <= state.max_bps;

    const submit = async () => {
        setBusy("add");
        setError(null);
        setNotice(null);
        const result = await setManagedMarkupOverrideApiV1AdminBillingRateCardMarkupOverridesPut(
            {
                body: {
                    provider: provider.trim().toLowerCase(),
                    component,
                    model: model.trim().toLowerCase(),
                    markup_bps: draftBps,
                    note: note.trim() || null,
                },
            },
        );
        if (result.error) {
            setError(detailFromResult(result, "Could not save the override"));
        } else {
            setNotice(`Saved — ${provider.trim()}/${component} now at ${asMultiple(draftBps)}.`);
            setProvider("");
            setModel("");
            setMarkup("");
            setNote("");
            setAdding(false);
            await load();
        }
        setBusy(null);
    };

    const clear = async (row: Override) => {
        setBusy(`clear-${row.id}`);
        setError(null);
        setNotice(null);
        const result =
            await clearManagedMarkupOverrideApiV1AdminBillingRateCardMarkupOverridesDelete({
                query: { provider: row.provider, component: row.component, model: row.model },
            });
        if (result.error) {
            setError(detailFromResult(result, "Could not clear the override"));
        } else {
            setNotice(`Cleared — ${row.provider}/${row.component} is back on the blanket markup.`);
            await load();
        }
        setBusy(null);
    };

    if (loading) {
        return <Skeleton className="h-64 w-full rounded-2xl" />;
    }
    if (!state) {
        return (
            <section className="glass-panel px-5 py-4">
                <p className="text-sm text-destructive">{error}</p>
            </section>
        );
    }

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Per-model markup overrides
                    </h2>
                    <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-muted-foreground">
                        Replaces the blanket managed markup for one model, or every model
                        from one provider when left blank. Everything else keeps the
                        multiple above.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setAdding((v) => !v)}>
                    {adding ? (
                        <X className="mr-2 h-3.5 w-3.5" />
                    ) : (
                        <Plus className="mr-2 h-3.5 w-3.5" />
                    )}
                    {adding ? "Cancel" : "Add override"}
                </Button>
            </div>

            {adding && (
                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                    <div className="space-y-1.5">
                        <Label htmlFor="ov-provider">Provider</Label>
                        <Input
                            id="ov-provider"
                            value={provider}
                            onChange={(e) => setProvider(e.target.value)}
                            placeholder="openai"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="ov-component">Component</Label>
                        <Select value={component} onValueChange={setComponent}>
                            <SelectTrigger id="ov-component">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {COMPONENTS.map((c) => (
                                    <SelectItem key={c} value={c}>
                                        {c}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="ov-model">Model (blank = every model)</Label>
                        <Input
                            id="ov-model"
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                            placeholder="gpt-4o"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="ov-markup">Markup (×)</Label>
                        <Input
                            id="ov-markup"
                            value={markup}
                            inputMode="decimal"
                            onChange={(e) => setMarkup(e.target.value)}
                            placeholder="2.00"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="ov-note">Why (optional)</Label>
                        <Input
                            id="ov-note"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Recorded against the change"
                        />
                    </div>
                    <div className="flex items-end lg:col-span-5">
                        <Button disabled={busy !== null || !draftValid} onClick={submit}>
                            {busy === "add" && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Save override
                        </Button>
                    </div>
                    {!draftValid && markup !== "" && (
                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground lg:col-span-5">
                            <AlertTriangle className="h-3.5 w-3.5" />
                            Needs a provider, and a markup between{" "}
                            {asMultiple(state.min_bps)} and {asMultiple(state.max_bps)}.
                        </p>
                    )}
                </div>
            )}

            {state.overrides.length === 0 ? (
                <p className="mt-4 text-sm text-muted-foreground">
                    No overrides — every managed line prices at the blanket markup above.
                </p>
            ) : (
                <div className="mt-4 overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Provider</TableHead>
                                <TableHead>Component</TableHead>
                                <TableHead>Model</TableHead>
                                <TableHead className="text-right">Markup</TableHead>
                                <TableHead className="text-right">Since</TableHead>
                                <TableHead>Note</TableHead>
                                <TableHead className="text-right" />
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {state.overrides.map((row) => (
                                <TableRow key={row.id}>
                                    <TableCell className="font-medium">
                                        {row.provider}
                                    </TableCell>
                                    <TableCell>{row.component}</TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {row.model || "All models"}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {asMultiple(row.markup_bps)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatDateIST(row.effective_from)}
                                    </TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {row.note ?? ""}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            disabled={busy !== null}
                                            onClick={() => void clear(row)}
                                        >
                                            {busy === `clear-${row.id}` && (
                                                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                            )}
                                            Clear
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {notice && (
                <p className="mt-3 flex items-start gap-1.5 text-xs text-foreground">
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {notice}
                </p>
            )}
            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
        </section>
    );
}
