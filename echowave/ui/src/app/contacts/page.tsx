"use client";

import { PlusIcon, SearchIcon, TrashIcon, UploadIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    createContactListApiV1ContactListsPost,
    deleteContactListApiV1ContactListsContactListIdDelete,
    importContactsApiV1ContactListsContactListIdImportPost,
    listContactListsApiV1ContactListsGet,
    listContactsApiV1ContactListsContactListIdContactsGet,
} from "@/client/sdk.gen";
import type { ContactListResponse, ContactResponse } from "@/client/types.gen";
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
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

/**
 * Contact lists: who an inbound number recognises.
 *
 * A list is matched against the caller ID of an incoming call. When it hits,
 * the contact's name and every column from the upload are preloaded into the
 * run, so the agent opens knowing who it is talking to.
 */
export default function ContactsPage() {
    const { user, loading: authLoading, getAccessToken } = useAuth();

    const [lists, setLists] = useState<ContactListResponse[]>([]);
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [contacts, setContacts] = useState<ContactResponse[]>([]);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState("");
    const [loadingLists, setLoadingLists] = useState(true);
    const [loadingContacts, setLoadingContacts] = useState(false);
    const [creating, setCreating] = useState(false);
    const [newName, setNewName] = useState("");
    const [importing, setImporting] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const loadLists = useCallback(
        async (selectAfter?: number) => {
            const token = await getAccessToken();
            const res = await listContactListsApiV1ContactListsGet({
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.error) {
                toast.error(detailFromError(res.error, "Could not load contact lists"));
                return;
            }
            const rows = res.data ?? [];
            setLists(rows);
            setSelectedId((current) => selectAfter ?? current ?? rows[0]?.id ?? null);
            setLoadingLists(false);
        },
        [getAccessToken],
    );

    useEffect(() => {
        if (authLoading || !user) return;
        void loadLists();
    }, [authLoading, user, loadLists]);

    const loadContacts = useCallback(async () => {
        if (!selectedId) {
            setContacts([]);
            setTotal(0);
            return;
        }
        setLoadingContacts(true);
        const token = await getAccessToken();
        const res = await listContactsApiV1ContactListsContactListIdContactsGet({
            headers: { Authorization: `Bearer ${token}` },
            path: { contact_list_id: selectedId },
            query: { limit: PAGE_SIZE, search: search || undefined },
        });
        setLoadingContacts(false);
        if (res.error) {
            toast.error(detailFromError(res.error, "Could not load contacts"));
            return;
        }
        setContacts(res.data?.contacts ?? []);
        setTotal(res.data?.total_count ?? 0);
    }, [selectedId, search, getAccessToken]);

    useEffect(() => {
        if (authLoading || !user) return;
        void loadContacts();
    }, [authLoading, user, loadContacts]);

    const handleCreate = async () => {
        const name = newName.trim();
        if (!name) return;
        const token = await getAccessToken();
        const res = await createContactListApiV1ContactListsPost({
            headers: { Authorization: `Bearer ${token}` },
            body: { name },
        });
        if (res.error) {
            toast.error(detailFromError(res.error, "Could not create the list"));
            return;
        }
        setCreating(false);
        setNewName("");
        toast.success(`Created “${name}”`);
        await loadLists(res.data?.id);
    };

    const handleDeleteList = async (list: ContactListResponse) => {
        const token = await getAccessToken();
        const res = await deleteContactListApiV1ContactListsContactListIdDelete({
            headers: { Authorization: `Bearer ${token}` },
            path: { contact_list_id: list.id },
        });
        if (res.error) {
            toast.error(detailFromError(res.error, "Could not delete the list"));
            return;
        }
        toast.success(`Deleted “${list.name}”`);
        setSelectedId(null);
        await loadLists();
    };

    const handleImport = async (file: File) => {
        if (!selectedId) return;
        setImporting(true);
        const token = await getAccessToken();
        const res = await importContactsApiV1ContactListsContactListIdImportPost({
            headers: { Authorization: `Bearer ${token}` },
            path: { contact_list_id: selectedId },
            body: { file },
        });
        setImporting(false);
        if (res.error) {
            toast.error(detailFromError(res.error, "Import failed"));
            return;
        }
        const result = res.data;
        if (!result) return;

        // Say what did not import, not just what did. "I uploaded 4,000 and
        // 3,712 arrived" is only answerable if the import said which ones at
        // the time.
        if (result.skipped) {
            toast.warning(
                `Imported ${result.imported}, skipped ${result.skipped}. ` +
                    (result.problems?.[0] ?? ""),
                { duration: 10000 },
            );
        } else {
            toast.success(`Imported ${result.imported} contacts`);
        }
        await Promise.all([loadLists(selectedId), loadContacts()]);
    };

    const selected = lists.find((l) => l.id === selectedId) ?? null;

    return (
        <div className="mx-auto max-w-6xl space-y-6 p-6">
            <div>
                <h1 className="text-2xl font-semibold">Contacts</h1>
                <p className="text-sm text-muted-foreground">
                    Lists an inbound number matches its callers against. When a
                    caller is recognised, everything you know about them is
                    loaded before the agent speaks — attach a list on the
                    number, under Telephony.
                </p>
            </div>

            <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
                <div className="space-y-2">
                    <div className="flex items-center justify-between">
                        <h2 className="text-sm font-medium">Lists</h2>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setCreating(true)}
                        >
                            <PlusIcon className="mr-1 h-4 w-4" /> New
                        </Button>
                    </div>

                    {loadingLists ? (
                        <div className="space-y-2">
                            {Array.from({ length: 3 }).map((_, i) => (
                                <Skeleton key={i} className="h-9 w-full" />
                            ))}
                        </div>
                    ) : lists.length === 0 ? (
                        <p className="rounded-md border border-dashed p-4 text-xs text-muted-foreground">
                            No lists yet. Create one, then upload a CSV with a
                            phone column.
                        </p>
                    ) : (
                        lists.map((list) => (
                            <button
                                key={list.id}
                                type="button"
                                onClick={() => setSelectedId(list.id)}
                                className={cn(
                                    "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                                    selectedId === list.id
                                        ? "bg-accent text-accent-foreground"
                                        : "hover:bg-accent/50",
                                )}
                            >
                                <span className="min-w-0 truncate">{list.name}</span>
                                <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                                    {list.contact_count}
                                </span>
                            </button>
                        ))
                    )}
                </div>

                <div className="min-w-0 space-y-3">
                    {selected ? (
                        <>
                            <div className="flex flex-wrap items-center gap-2">
                                <div className="relative min-w-0 flex-1">
                                    <SearchIcon
                                        className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                                        aria-hidden="true"
                                    />
                                    <Input
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        placeholder="Search this list"
                                        aria-label="Search contacts"
                                        className="pl-8"
                                    />
                                </div>
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept=".csv,text/csv"
                                    className="hidden"
                                    onChange={(e) => {
                                        const file = e.target.files?.[0];
                                        e.target.value = "";
                                        if (file) void handleImport(file);
                                    }}
                                />
                                <Button
                                    variant="outline"
                                    disabled={importing}
                                    onClick={() => fileInputRef.current?.click()}
                                >
                                    <UploadIcon className="mr-1 h-4 w-4" />
                                    {importing ? "Importing…" : "Upload CSV"}
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Delete ${selected.name}`}
                                    onClick={() => void handleDeleteList(selected)}
                                >
                                    <TrashIcon className="h-4 w-4" />
                                </Button>
                            </div>

                            {loadingContacts ? (
                                <Skeleton className="h-40 w-full" />
                            ) : contacts.length === 0 ? (
                                <div className="rounded-md border border-dashed p-8 text-center">
                                    <p className="text-sm text-muted-foreground">
                                        {search
                                            ? "Nothing matches that."
                                            : "No contacts yet."}
                                    </p>
                                    {!search && (
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            Upload a CSV with a{" "}
                                            <code>phone</code> column. Every
                                            other column is kept and handed to
                                            the agent.
                                        </p>
                                    )}
                                </div>
                            ) : (
                                <div className="overflow-x-auto rounded-md border">
                                    <table className="w-full text-sm">
                                        <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                                            <tr>
                                                <th className="p-2 font-medium">Name</th>
                                                <th className="p-2 font-medium">Phone</th>
                                                <th className="p-2 font-medium">
                                                    Attributes
                                                </th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {contacts.map((contact) => (
                                                <tr
                                                    key={contact.id}
                                                    className="border-b last:border-0"
                                                >
                                                    <td className="p-2">
                                                        {contact.name ?? (
                                                            <span className="text-muted-foreground">
                                                                —
                                                            </span>
                                                        )}
                                                    </td>
                                                    <td className="p-2 font-mono text-xs">
                                                        {contact.phone_normalized}
                                                    </td>
                                                    <td className="p-2 text-xs text-muted-foreground">
                                                        {Object.entries(
                                                            contact.attributes ?? {},
                                                        )
                                                            .map(
                                                                ([k, v]) =>
                                                                    `${k}: ${String(v)}`,
                                                            )
                                                            .join(" · ") || "—"}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {total > contacts.length && (
                                <p className="text-xs text-muted-foreground">
                                    Showing {contacts.length} of {total}. Narrow
                                    it with search.
                                </p>
                            )}
                        </>
                    ) : (
                        !loadingLists && (
                            <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                                Select a list, or create one.
                            </div>
                        )
                    )}
                </div>
            </div>

            <Dialog open={creating} onOpenChange={setCreating}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New contact list</DialogTitle>
                        <DialogDescription>
                            Name it after the number or audience it serves —
                            &ldquo;Policy holders&rdquo;, &ldquo;Support
                            line&rdquo;.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-2">
                        <Label htmlFor="cl-name">Name</Label>
                        <Input
                            id="cl-name"
                            value={newName}
                            onChange={(e) => setNewName(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") void handleCreate();
                            }}
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreating(false)}>
                            Cancel
                        </Button>
                        <Button onClick={() => void handleCreate()}>Create</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
