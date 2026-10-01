"use client";

import { Loader2, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    type AccountMatch,
    clearGlobal,
    searchAccounts,
    setForOrganization,
    setGlobal,
} from "@/lib/superadmin/flags";
import { cn } from "@/lib/utils";

/** A date input's day, as the end of that day in the browser's zone. */
function endOfDayIso(day: string): string | null {
    if (!day) return null;
    const date = new Date(`${day}T23:59:59`);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/**
 * "Turn on for an account": find the account by its name or an owner's
 * email (the billing accounts search), then save. An optional note says why,
 * and an optional end date stops it applying without anyone remembering to.
 */
export function TurnOnForAccountDialog({
    flag,
    open,
    onOpenChange,
    onSaved,
}: {
    flag: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSaved: () => void;
}) {
    const [search, setSearch] = useState("");
    const [matches, setMatches] = useState<AccountMatch[] | null>(null);
    const [searching, setSearching] = useState(false);
    const [searchError, setSearchError] = useState<string | null>(null);
    const [picked, setPicked] = useState<AccountMatch | null>(null);
    const [note, setNote] = useState("");
    const [until, setUntil] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    useEffect(() => {
        if (!open) {
            setSearch("");
            setMatches(null);
            setPicked(null);
            setNote("");
            setUntil("");
            setSaveError(null);
            setSearchError(null);
        }
    }, [open]);

    useEffect(() => {
        if (!open || search.trim().length < 2) {
            setMatches(null);
            return;
        }
        let cancelled = false;
        const timer = setTimeout(async () => {
            setSearching(true);
            const outcome = await searchAccounts(search);
            if (cancelled) return;
            setSearching(false);
            if (outcome.ok) {
                setMatches(outcome.value.slice(0, 8));
                setSearchError(null);
            } else {
                setMatches(null);
                setSearchError(outcome.error);
            }
        }, 250);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [open, search]);

    const save = async () => {
        if (!flag || !picked) return;
        setSaving(true);
        setSaveError(null);
        const outcome = await setForOrganization(flag, picked.organization_id, {
            enabled: true,
            note: note.trim() || null,
            expires_at: endOfDayIso(until),
        });
        setSaving(false);
        if (!outcome.ok) {
            setSaveError(outcome.error);
            return;
        }
        onOpenChange(false);
        onSaved();
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Turn on for an account</DialogTitle>
                    <DialogDescription>
                        <span className="font-mono">{flag}</span> will be on for this account
                        only, whatever everyone else gets.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="flag-account-search">Account</Label>
                        <div className="relative">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                id="flag-account-search"
                                className="pl-8"
                                placeholder="Name or owner email"
                                value={search}
                                onChange={(e) => {
                                    setSearch(e.target.value);
                                    setPicked(null);
                                }}
                            />
                        </div>
                        {searching && (
                            <p className="flex items-center gap-1 text-xs text-muted-foreground">
                                <Loader2 className="h-3 w-3 animate-spin" /> Searching
                            </p>
                        )}
                        {searchError && <p className="text-xs text-destructive">{searchError}</p>}
                        {matches && matches.length === 0 && !searching && (
                            <p className="text-xs text-muted-foreground">No account matches.</p>
                        )}
                        {matches && matches.length > 0 && (
                            <ul className="max-h-56 divide-y overflow-auto rounded-md border" role="listbox">
                                {matches.map((m) => (
                                    <li key={m.organization_id}>
                                        <button
                                            type="button"
                                            role="option"
                                            aria-selected={picked?.organization_id === m.organization_id}
                                            onClick={() => setPicked(m)}
                                            className={cn(
                                                "flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-muted",
                                                picked?.organization_id === m.organization_id && "bg-muted",
                                            )}
                                        >
                                            <span className="font-medium">{m.name}</span>
                                            <span className="text-xs text-muted-foreground">
                                                #{m.organization_id}
                                                {m.owner_email ? ` · ${m.owner_email}` : ""}
                                            </span>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="flag-note">Why (optional)</Label>
                            <Input
                                id="flag-note"
                                maxLength={300}
                                placeholder="Pilot, asked on a call"
                                value={note}
                                onChange={(e) => setNote(e.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="flag-until">Until (optional)</Label>
                            <Input
                                id="flag-until"
                                type="date"
                                value={until}
                                onChange={(e) => setUntil(e.target.value)}
                            />
                        </div>
                    </div>
                    {saveError && <p className="text-sm text-destructive">{saveError}</p>}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={save} disabled={!picked || saving}>
                        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {picked ? `Turn on for ${picked.name}` : "Pick an account"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export type GlobalChange =
    | { flag: string; kind: "set"; enabled: boolean }
    | { flag: string; kind: "clear" };

/**
 * A change for every account. The flag's name has to be typed, because the
 * founder's rule is that a global flip follows a staging check, and a
 * click is too easy to make by accident.
 */
export function GlobalChangeDialog({
    change,
    onOpenChange,
    onSaved,
}: {
    change: GlobalChange | null;
    onOpenChange: (open: boolean) => void;
    onSaved: () => void;
}) {
    const [typed, setTyped] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setTyped("");
        setError(null);
    }, [change]);

    const flag = change?.flag ?? "";
    const title =
        change?.kind === "clear"
            ? "Hand back to the environment"
            : change?.enabled
              ? "Turn on for everyone"
              : "Turn off for everyone";

    const save = async () => {
        if (!change || typed !== flag) return;
        setSaving(true);
        setError(null);
        const outcome =
            change.kind === "clear"
                ? await clearGlobal(flag, typed)
                : await setGlobal(flag, change.enabled, typed);
        setSaving(false);
        if (!outcome.ok) {
            setError(outcome.error);
            return;
        }
        onOpenChange(false);
        onSaved();
    };

    return (
        <Dialog open={change !== null} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>
                        {change?.kind === "clear"
                            ? "The value in the server environment decides again, for every account without its own override."
                            : "Every account without its own override gets this within a few seconds. Check it on staging first."}
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-2">
                    <Label htmlFor="flag-confirm">
                        Type <span className="font-mono">{flag}</span> to confirm
                    </Label>
                    <Input
                        id="flag-confirm"
                        autoComplete="off"
                        value={typed}
                        onChange={(e) => setTyped(e.target.value)}
                    />
                    {error && <p className="text-sm text-destructive">{error}</p>}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button
                        variant={change?.kind === "set" && !change.enabled ? "destructive" : "default"}
                        onClick={save}
                        disabled={typed !== flag || saving}
                    >
                        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {title}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
