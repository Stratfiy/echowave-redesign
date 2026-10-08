"use client";

/**
 * Where contacts come from, each with its honest state: Google and Outlook
 * (connect in place, sync, syncing, synced, error), a vCard or CSV file,
 * and -- on Android Chrome only -- the phone's own contact picker.
 *
 * A missing connection is a connect chip right here: it opens the provider's
 * own sign-in in a new tab and comes back to "I've signed in", never a trip
 * to another screen.
 */

import { Check, FileUp, Loader2, Plug, RefreshCw, Smartphone } from "lucide-react";
import { useRef, useState } from "react";

import {
    connectProviderApiV1PeopleConnectProviderPost,
    importPeopleApiV1PeopleImportPost,
    importPickedApiV1PeopleImportPickerPost,
    startSyncApiV1PeopleSyncProviderPost,
} from "@/client/sdk.gen";
import type { ImportResult, ProviderStatus } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { ago, syncedLine } from "@/lib/people/format";
import { contactPicker, pickContacts } from "@/lib/people/picker";

type Provider = "google" | "microsoft";

function importLine(result: ImportResult): string {
    const parts = [`${result.added} added`];
    if (result.updated) parts.push(`${result.updated} updated`);
    if (result.unchanged) parts.push(`${result.unchanged} already here`);
    if (result.skipped) parts.push(`${result.skipped} without a name, number or address skipped`);
    return parts.join(", ") + ".";
}

function ProviderRow({ row, onChanged }: { row: ProviderStatus; onChanged: () => void }) {
    const [busy, setBusy] = useState(false);
    const [opened, setOpened] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const provider = row.provider as Provider;

    const connect = async () => {
        setBusy(true);
        setError(null);
        const result = await connectProviderApiV1PeopleConnectProviderPost({
            path: { provider },
        });
        setBusy(false);
        if (result.error || !result.data?.connect_url) {
            setError(detailFromResult(result, `Could not start signing in to ${row.name}`));
            return;
        }
        setOpened(true);
        window.open(result.data.connect_url, "_blank", "noopener,noreferrer");
    };

    const sync = async () => {
        setBusy(true);
        setError(null);
        const result = await startSyncApiV1PeopleSyncProviderPost({
            path: { provider },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromResult(result, `Could not sync ${row.name}`));
            return;
        }
        setOpened(false);
        onChanged();
    };

    let line: string;
    let action: React.ReactNode = null;
    switch (row.state) {
        case "not_connected":
            line = opened ? "Finish signing in, then come back here." : "Not connected.";
            action = opened ? (
                <Button size="sm" variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void sync()}>
                    {busy ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />}
                    I&apos;ve signed in
                </Button>
            ) : (
                <Button size="sm" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void connect()} data-testid={`people-connect-${provider}`}>
                    {busy ? <Loader2 aria-hidden className="animate-spin" /> : <Plug aria-hidden />}
                    Connect
                </Button>
            );
            break;
        case "needs_setup":
            line = row.detail || "Not set up on this deployment yet.";
            break;
        case "syncing":
            line = "Syncing your contacts…";
            action = <Loader2 aria-label="Syncing" className="h-4 w-4 animate-spin text-muted-foreground" />;
            break;
        case "ok":
            line = `Synced ${ago(row.last_synced_at)}: ${syncedLine(row.counts)}. Kept up to date every few hours.`;
            action = (
                <Button size="sm" variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void sync()}>
                    <RefreshCw aria-hidden />
                    Sync now
                </Button>
            );
            break;
        case "error":
            line = row.detail || "The last sync did not finish.";
            action = (
                <Button size="sm" variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void sync()}>
                    <RefreshCw aria-hidden />
                    Try again
                </Button>
            );
            break;
        default:
            line = "Connected. Not synced yet.";
            action = (
                <Button size="sm" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void sync()}>
                    {busy ? <Loader2 aria-hidden className="animate-spin" /> : <RefreshCw aria-hidden />}
                    Sync contacts
                </Button>
            );
    }

    return (
        <li className="flex flex-wrap items-center justify-between gap-2 px-4 py-3" data-testid={`people-source-${provider}`} data-state={row.state}>
            <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{row.name}</p>
                <p className={row.state === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>{line}</p>
                {error && (
                    <p role="alert" className="mt-1 text-sm text-destructive">
                        {error}
                    </p>
                )}
            </div>
            {action}
        </li>
    );
}

export function PeopleSources({ providers, onChanged }: { providers: ProviderStatus[]; onChanged: () => void }) {
    const fileInput = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [fileName, setFileName] = useState<string | null>(null);
    const picker = typeof window === "undefined" ? null : contactPicker();

    const upload = async (file: File) => {
        setBusy(true);
        setError(null);
        setNote(null);
        setFileName(file.name);
        const result = await importPeopleApiV1PeopleImportPost({ body: { file } });
        setBusy(false);
        if (result.error || !result.data) {
            setError(detailFromResult(result, "Could not import that file"));
            return;
        }
        setNote(importLine(result.data));
        onChanged();
    };

    const pick = async () => {
        if (!picker) return;
        setError(null);
        setNote(null);
        let picked;
        try {
            picked = await pickContacts(picker);
        } catch {
            return; // closed without choosing
        }
        if (!picked.length) return;
        setBusy(true);
        const result = await importPickedApiV1PeopleImportPickerPost({
            body: {
                contacts: picked.map((c) => ({
                    name: c.name ?? [],
                    tel: c.tel ?? [],
                    email: c.email ?? [],
                })),
            },
        });
        setBusy(false);
        if (result.error || !result.data) {
            setError(detailFromResult(result, "Could not add those contacts"));
            return;
        }
        setNote(importLine(result.data));
        onChanged();
    };

    return (
        <section aria-label="Where your contacts come from" className="rounded-[8px] border border-border">
            <ul className="divide-y divide-border">
                {providers.map((row) => (
                    <ProviderRow key={row.provider} row={row} onChanged={onChanged} />
                ))}
                <li className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                    <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">From a file or your phone</p>
                        <p className="text-sm text-muted-foreground">
                            A vCard (.vcf) or CSV export
                            {picker ? ", or pick from this phone's contacts" : ""}.{fileName && <span className="block break-all">{fileName}</span>}
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <input
                            ref={fileInput}
                            type="file"
                            accept=".vcf,.vcard,.csv,text/vcard,text/csv"
                            className="hidden"
                            data-testid="people-file"
                            onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void upload(file);
                                event.target.value = "";
                            }}
                        />
                        <Button size="sm" variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => fileInput.current?.click()}>
                            {busy ? <Loader2 aria-hidden className="animate-spin" /> : <FileUp aria-hidden />}
                            Import file
                        </Button>
                        {picker && (
                            <Button
                                size="sm"
                                variant="outline"
                                className="min-h-11 md:min-h-9"
                                disabled={busy}
                                onClick={() => void pick()}
                                data-testid="people-picker"
                            >
                                <Smartphone aria-hidden />
                                Pick from phone
                            </Button>
                        )}
                    </div>
                    {note && (
                        <p role="status" className="w-full text-sm text-muted-foreground">
                            {note}
                        </p>
                    )}
                    {error && (
                        <p role="alert" className="w-full text-sm text-destructive">
                            {error}
                        </p>
                    )}
                </li>
            </ul>
        </section>
    );
}

export default PeopleSources;
