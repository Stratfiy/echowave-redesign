"use client";

/**
 * Pick a ready-made tool instead of filling in an empty HTTP form.
 *
 * The catalogue is `api/services/integrations/tool_library.py`. Picking an
 * entry creates an ordinary `http_api` tool from its seeded definition and
 * lands the operator on its detail page — where they change the Zoho
 * datacentre in the URL and attach their credential. Nothing here is
 * referenced by id afterwards; the entry is copied, and editing the tool edits
 * the copy.
 *
 * `setup_note` is rendered on the selected entry rather than hidden behind a
 * tooltip, because both of the notes it carries describe failures that happen
 * on a *live call* and point at the wrong problem: a `.in` Zoho URL on a
 * `.com` account returns `invalid_client`, which reads as a bad credential.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
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
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { cn } from "@/lib/utils";

export interface LibraryTool {
    key: string;
    vendor: string;
    display_name: string;
    summary: string;
    tool_name: string;
    tool_description: string;
    method: string;
    url: string;
    setup_note: string;
}

interface LibraryResponse {
    catalog: string;
    vendors: string[];
    tools: LibraryTool[];
}

export interface ToolLibraryDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Given the picked entry, create the tool. The page owns creation so this
     *  dialog stays a picker and does not need to know about routing. */
    onPick: (entry: LibraryTool) => Promise<void> | void;
    /** From `useAuth()`. Passed in rather than imported because that is how the
     *  rest of the app gets a token — see ui/AGENTS.md on authenticated calls. */
    getAccessToken: () => Promise<string | null | undefined>;
    creating?: boolean;
}

// The catalogue is identical for everyone and never changes within a session,
// so one fetch serves every open of the dialog.
let _cache: LibraryResponse | null = null;

export function ToolLibraryDialog({
    open,
    onOpenChange,
    onPick,
    getAccessToken,
    creating = false,
}: ToolLibraryDialogProps) {
    const [data, setData] = useState<LibraryResponse | null>(_cache);
    const [loading, setLoading] = useState(!_cache);
    const [error, setError] = useState<string | null>(null);
    const [vendor, setVendor] = useState<string | null>(null);
    const [selected, setSelected] = useState<string | null>(null);
    const [query, setQuery] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const token = await getAccessToken();
            // Fetched directly rather than through the generated client, which
            // predates this endpoint. Delete after `npm run generate-client`.
            const response = await fetch(
                `${resolveBrowserBackendUrl()}/api/v1/tool-library`,
                { headers: { Authorization: `Bearer ${token}` } },
            );
            if (!response.ok) {
                setError("Could not load the tool library.");
                return;
            }
            const body = (await response.json()) as LibraryResponse;
            _cache = body;
            setData(body);
        } catch {
            setError("Could not reach the server.");
        } finally {
            setLoading(false);
        }
    }, [getAccessToken]);

    useEffect(() => {
        if (open && !data) void load();
    }, [open, data, load]);

    const visible = useMemo(() => {
        const all = data?.tools ?? [];
        const needle = query.trim().toLowerCase();
        return all.filter((t) => {
            if (vendor && t.vendor !== vendor) return false;
            if (!needle) return true;
            return (
                t.display_name.toLowerCase().includes(needle) ||
                t.summary.toLowerCase().includes(needle) ||
                t.vendor.toLowerCase().includes(needle)
            );
        });
    }, [data, vendor, query]);

    const picked = visible.find((t) => t.key === selected) ?? null;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Start from a ready-made tool</DialogTitle>
                    <DialogDescription>
                        The handful of things an agent does against each app during
                        a call. You can change anything after it is created.
                    </DialogDescription>
                </DialogHeader>

                {loading && (
                    <p className="py-8 text-center text-sm text-muted-foreground">
                        Loading…
                    </p>
                )}

                {error && !loading && (
                    <div className="space-y-3 py-6 text-center">
                        <p className="text-sm text-destructive">{error}</p>
                        <Button variant="outline" size="sm" onClick={() => void load()}>
                            Try again
                        </Button>
                    </div>
                )}

                {!loading && !error && data && (
                    <div className="space-y-3">
                        <Input
                            placeholder="Search…"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                        />

                        <div className="flex flex-wrap gap-2">
                            <Button
                                size="sm"
                                variant={vendor === null ? "secondary" : "ghost"}
                                onClick={() => setVendor(null)}
                            >
                                All
                            </Button>
                            {data.vendors.map((v) => (
                                <Button
                                    key={v}
                                    size="sm"
                                    variant={vendor === v ? "secondary" : "ghost"}
                                    onClick={() => setVendor(v)}
                                >
                                    {v}
                                </Button>
                            ))}
                        </div>

                        <ul className="max-h-72 space-y-2 overflow-y-auto">
                            {visible.map((tool) => (
                                <li key={tool.key}>
                                    <button
                                        type="button"
                                        onClick={() => setSelected(tool.key)}
                                        className={cn(
                                            "w-full rounded-lg border px-3 py-2 text-left transition-colors",
                                            selected === tool.key
                                                ? "border-primary bg-primary/5"
                                                : "hover:bg-muted/50",
                                        )}
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="text-sm font-medium">
                                                {tool.display_name}
                                            </span>
                                            <Badge
                                                variant="outline"
                                                className="shrink-0 text-xs font-normal"
                                            >
                                                {tool.vendor}
                                            </Badge>
                                        </div>
                                        <p className="mt-0.5 text-xs text-muted-foreground">
                                            {tool.summary}
                                        </p>
                                    </button>
                                </li>
                            ))}
                            {visible.length === 0 && (
                                <li className="py-6 text-center text-sm text-muted-foreground">
                                    Nothing matches that.
                                </li>
                            )}
                        </ul>

                        {picked?.setup_note && (
                            // Shown before creating, not after. Both notes in the
                            // catalogue describe a failure that happens mid-call
                            // and looks like a different problem.
                            <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2">
                                <p className="text-xs text-muted-foreground">
                                    <span className="font-medium text-foreground">
                                        Before it will work:{" "}
                                    </span>
                                    {picked.setup_note}
                                </p>
                            </div>
                        )}
                    </div>
                )}

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={creating}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={() => picked && void onPick(picked)}
                        disabled={!picked || creating}
                    >
                        {creating ? "Creating…" : "Create tool"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
