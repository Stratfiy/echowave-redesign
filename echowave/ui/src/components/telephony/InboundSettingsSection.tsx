"use client";

import { PlusIcon, XIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { listContactListsApiV1ContactListsGet } from "@/client/sdk.gen";
import type { ContactListResponse } from "@/client/types.gen";
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
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";

export const NO_CONTACT_LIST = "__none__";

export interface InboundSettingsValue {
    contactListId: string;
    requireKnownCaller: boolean;
    /** Empty string means unlimited, which is also what the backend stores. */
    maxCallsPerCaller: string;
    windowHours: string;
    allowList: string[];
}

export const EMPTY_INBOUND_SETTINGS: InboundSettingsValue = {
    contactListId: NO_CONTACT_LIST,
    requireKnownCaller: false,
    maxCallsPerCaller: "",
    windowHours: "24",
    allowList: [],
};

/**
 * Who this number answers, and what the agent already knows when it does.
 *
 * On the number rather than the agent, because the number is what a stranger
 * dials: one agent may answer both a published support line and a number given
 * only to existing customers, and those want different rules.
 *
 * Every control here defaults to off. A published number whose behaviour
 * changes because a feature shipped is the failure worth designing out — the
 * person who notices is the caller who could not get through, and they cannot
 * tell us.
 */
export function InboundSettingsSection({
    value,
    onChange,
}: {
    value: InboundSettingsValue;
    onChange: (next: InboundSettingsValue) => void;
}) {
    const { user, getAccessToken } = useAuth();
    const [lists, setLists] = useState<ContactListResponse[]>([]);
    const [newAllowed, setNewAllowed] = useState("");

    useEffect(() => {
        if (!user) return;
        let cancelled = false;
        (async () => {
            const token = await getAccessToken();
            const res = await listContactListsApiV1ContactListsGet({
                headers: { Authorization: `Bearer ${token}` },
            });
            if (cancelled || res.error) return;
            setLists(res.data ?? []);
        })();
        return () => {
            cancelled = true;
        };
    }, [user, getAccessToken]);

    const set = (patch: Partial<InboundSettingsValue>) =>
        onChange({ ...value, ...patch });

    const addAllowed = () => {
        const entry = newAllowed.trim();
        if (!entry || value.allowList.includes(entry)) {
            setNewAllowed("");
            return;
        }
        set({ allowList: [...value.allowList, entry] });
        setNewAllowed("");
    };

    const hasList = value.contactListId !== NO_CONTACT_LIST;
    const limited = value.maxCallsPerCaller.trim() !== "";

    return (
        <div className="space-y-5 rounded-md border p-4">
            <div>
                <h4 className="text-sm font-medium">Inbound settings</h4>
                <p className="text-xs text-muted-foreground">
                    Who this number answers, and what the agent knows before it
                    speaks.
                </p>
            </div>

            <div className="grid gap-2">
                <Label htmlFor="pn-contact-list">Caller database</Label>
                <Select
                    value={value.contactListId}
                    onValueChange={(v) => set({ contactListId: v })}
                >
                    <SelectTrigger id="pn-contact-list">
                        <SelectValue placeholder="No database" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={NO_CONTACT_LIST}>No database</SelectItem>
                        {lists.map((list) => (
                            <SelectItem key={list.id} value={String(list.id)}>
                                {list.name} ({list.contact_count})
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                    Match an incoming caller against a contact list and preload
                    what you know about them — their name and every column from
                    your upload — before the agent opens its mouth.
                </p>
            </div>

            <div className="flex items-start justify-between gap-4">
                <div className="space-y-0.5">
                    <Label htmlFor="pn-known-only">Only answer known callers</Label>
                    <p className="text-xs text-muted-foreground">
                        {hasList
                            ? "Anyone not in the database is refused. For a line given only to existing customers."
                            : "Choose a database first — with none, there is nothing for a caller to be unknown to, so this has no effect."}
                    </p>
                </div>
                <Switch
                    id="pn-known-only"
                    checked={value.requireKnownCaller}
                    disabled={!hasList}
                    onCheckedChange={(checked) => set({ requireKnownCaller: checked })}
                />
            </div>

            <div className="space-y-3 border-t pt-4">
                <div className="grid gap-2 sm:grid-cols-2">
                    <div className="grid gap-2">
                        <Label htmlFor="pn-max-calls">Max calls per caller</Label>
                        <Input
                            id="pn-max-calls"
                            type="number"
                            min={1}
                            inputMode="numeric"
                            placeholder="Unlimited"
                            value={value.maxCallsPerCaller}
                            onChange={(e) =>
                                set({ maxCallsPerCaller: e.target.value })
                            }
                        />
                    </div>
                    <div className="grid gap-2">
                        <Label htmlFor="pn-window">Counted over (hours)</Label>
                        <Input
                            id="pn-window"
                            type="number"
                            min={1}
                            max={24 * 28}
                            inputMode="numeric"
                            disabled={!limited}
                            value={value.windowHours}
                            onChange={(e) => set({ windowHours: e.target.value })}
                        />
                    </div>
                </div>
                <p className="text-xs text-muted-foreground">
                    Leave the limit blank for unlimited. The window matters: a
                    cap that never resets locks out a genuine repeat caller for
                    good, and the only person who would notice is the caller.
                </p>
            </div>

            <div className="space-y-2 border-t pt-4">
                <Label htmlFor="pn-allow">Always allow these numbers</Label>
                <p className="text-xs text-muted-foreground">
                    Exempt from the limit — an office line, or a monitoring
                    service that legitimately calls all day. Written however you
                    like; they are matched in the carrier&apos;s own format.
                </p>
                <div className="flex gap-2">
                    <Input
                        id="pn-allow"
                        placeholder="+91 98765 43210"
                        value={newAllowed}
                        onChange={(e) => setNewAllowed(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") {
                                e.preventDefault();
                                addAllowed();
                            }
                        }}
                    />
                    <Button
                        type="button"
                        variant="outline"
                        size="icon"
                        onClick={addAllowed}
                        aria-label="Add number to the always-allow list"
                    >
                        <PlusIcon className="h-4 w-4" />
                    </Button>
                </div>
                {value.allowList.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                        {value.allowList.map((entry) => (
                            <span
                                key={entry}
                                className="inline-flex items-center gap-1 rounded-full border bg-muted/40 py-0.5 pl-2.5 pr-1 text-xs"
                            >
                                {entry}
                                <button
                                    type="button"
                                    className="rounded-full p-0.5 hover:bg-muted"
                                    aria-label={`Remove ${entry}`}
                                    onClick={() =>
                                        set({
                                            allowList: value.allowList.filter(
                                                (n) => n !== entry,
                                            ),
                                        })
                                    }
                                >
                                    <XIcon className="h-3 w-3" />
                                </button>
                            </span>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
