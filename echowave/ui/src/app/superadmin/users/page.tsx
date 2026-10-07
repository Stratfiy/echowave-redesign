"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { CommandFlow } from "@/components/staff/CommandFlow";
import { Empty, PageHeader, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";

type UserRow = {
    id: number;
    email: string | null;
    created_at: string | null;
    access_state: string;
    staff: string | null;
    workspaces: number;
    kyc_status: string | null;
    last_useful_outcome_at: string | null;
    mfa_enabled: boolean;
};

type WaitRow = {
    id: number;
    email: string;
    language: string;
    first_task: string | null;
    occupation: string | null;
    status: string;
    created_at: string | null;
};

type Capacity = { state: string; limit: number | null; people: number; open_invites: number; remaining: number | null; setting: string };

/**
 * Users, invitations and access (screen 30): a searchable list that leads
 * with access state and last useful outcome, and the waitlist with a review
 * panel. Invitations go out only for rows someone ticked, through the
 * `invite.issue` command, which previews the cohort and capacity first and
 * reports each address's outcome.
 */
export default function UsersPage() {
    const { can } = useStaffConsole();
    const [search, setSearch] = useState("");
    const [applied, setApplied] = useState("");
    const [state, setState] = useState("");
    const query = useMemo(() => ({ q: applied || undefined, state: state || undefined }), [applied, state]);
    const users = useStaffData<{ users: UserRow[] }>("/api/v1/admin/staff/users", query);
    const waitlist = useStaffData<{ requests: WaitRow[]; capacity: Capacity }>("/api/v1/admin/staff/waitlist");
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [reviewing, setReviewing] = useState<WaitRow | null>(null);
    const [inviting, setInviting] = useState(false);
    useReportFreshness(users.state, users.refreshedAt);

    function toggle(id: number) {
        setSelected((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }

    return (
        <div className="space-y-4">
            <PageHeader title="Users and access" description="Access state and last useful outcome for each person. KYC shows a status only; documents are never shown here." />
            <form
                className="flex flex-wrap items-end gap-2"
                role="search"
                onSubmit={(event) => {
                    event.preventDefault();
                    setApplied(search.trim());
                }}
            >
                <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
                    <span>Email or user id</span>
                    <Input value={search} onChange={(e) => setSearch(e.target.value)} className="min-h-11 text-base md:min-h-9 md:text-sm" />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                    <span>Access</span>
                    <select value={state} onChange={(e) => setState(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                        <option value="">Everyone</option>
                        <option value="active">Active</option>
                        <option value="suspended">Suspended</option>
                    </select>
                </label>
                <Button type="submit" className="min-h-11 md:min-h-9">
                    Search
                </Button>
            </form>

            <Panel title="People" query={users} skeletonHeight={240}>
                {(data) =>
                    data.users.length === 0 ? (
                        <Empty>No one matches this search.</Empty>
                    ) : (
                        <ul className="divide-y divide-border" data-testid="user-rows">
                            <li className="hidden grid-cols-[2fr_1fr_1fr_1fr_1.2fr] gap-3 pb-2 text-xs text-muted-foreground md:grid">
                                <span>Person</span>
                                <span>Access</span>
                                <span>Workspaces</span>
                                <span>KYC</span>
                                <span>Last useful outcome</span>
                            </li>
                            {data.users.map((u) => (
                                <li key={u.id} className="grid grid-cols-2 gap-x-3 gap-y-1 py-2 text-sm md:grid-cols-[2fr_1fr_1fr_1fr_1.2fr] md:items-center">
                                    <Link href={`/superadmin/users/${u.id}`} className="col-span-2 min-h-11 break-all underline-offset-2 hover:underline md:col-span-1 md:min-h-0">
                                        {u.email ?? `User ${u.id}`}
                                        {u.staff && <span className="ml-1 text-xs text-muted-foreground">(staff: {u.staff})</span>}
                                    </Link>
                                    <span>
                                        <StateBadge state={u.access_state} />
                                    </span>
                                    <span className="text-muted-foreground md:text-foreground">
                                        <span className="md:hidden">Workspaces </span>
                                        {count(u.workspaces)}
                                    </span>
                                    <span className="text-xs">{u.kyc_status ? words(u.kyc_status) : "Not started"}</span>
                                    <span className="text-xs">{u.last_useful_outcome_at ? when(u.last_useful_outcome_at) : "None yet"}</span>
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>

            <Panel title="Waitlist" query={waitlist}>
                {(data) => (
                    <div className="space-y-3">
                        <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="capacity">
                            Capacity{" "}
                            {data.capacity.state === "needs_setup" ? (
                                <>
                                    <StateBadge state="needs_setup" /> <span className="text-xs text-muted-foreground">Set {data.capacity.setting} (founder decision). {count(data.capacity.people)} people in, {count(data.capacity.open_invites)} invitations open.</span>
                                </>
                            ) : (
                                <span>
                                    {count(data.capacity.remaining)} of {count(data.capacity.limit)} places left ({count(data.capacity.people)} in, {count(data.capacity.open_invites)} invited)
                                </span>
                            )}
                        </p>
                        {data.requests.length === 0 ? (
                            <Empty>Nobody is waiting.</Empty>
                        ) : (
                            <ul className="divide-y divide-border">
                                {data.requests.map((w) => (
                                    <li key={w.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                                        {can("users.invite") && (
                                            <Checkbox
                                                aria-label={`Select ${w.email} to invite`}
                                                checked={selected.has(w.id)}
                                                onCheckedChange={() => toggle(w.id)}
                                                className="h-5 w-5"
                                            />
                                        )}
                                        <button type="button" className="min-h-11 min-w-0 flex-1 break-all text-left underline-offset-2 hover:underline md:min-h-8" onClick={() => setReviewing(w)}>
                                            {w.email}
                                        </button>
                                        <span className="text-xs text-muted-foreground">{w.language}</span>
                                        <span className="text-xs text-muted-foreground">{when(w.created_at)}</span>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {can("users.invite") && (
                            <Button variant="outline" className="min-h-11 md:min-h-9" disabled={selected.size === 0} onClick={() => setInviting(true)}>
                                Invite {selected.size || ""} selected
                            </Button>
                        )}
                    </div>
                )}
            </Panel>

            <Sheet open={reviewing !== null} onOpenChange={(open) => !open && setReviewing(null)}>
                <SheetContent side="right" className="w-full max-w-full overflow-y-auto sm:max-w-md">
                    <SheetTitle>Waitlist request</SheetTitle>
                    <SheetDescription className="sr-only">What this person asked for</SheetDescription>
                    {reviewing && (
                        <dl className="mt-4 space-y-3 px-4 text-sm">
                            <div>
                                <dt className="text-muted-foreground">Email</dt>
                                <dd className="break-all">{reviewing.email}</dd>
                            </div>
                            <div>
                                <dt className="text-muted-foreground">First task they want</dt>
                                <dd className="whitespace-pre-wrap">{reviewing.first_task || "Not given"}</dd>
                            </div>
                            <div>
                                <dt className="text-muted-foreground">Language</dt>
                                <dd>{reviewing.language}</dd>
                            </div>
                            <div>
                                <dt className="text-muted-foreground">Occupation</dt>
                                <dd>{reviewing.occupation || "Not given"}</dd>
                            </div>
                        </dl>
                    )}
                </SheetContent>
            </Sheet>

            <Sheet open={inviting} onOpenChange={setInviting}>
                <SheetContent side="right" className="w-full max-w-full overflow-y-auto p-4 sm:max-w-lg">
                    <SheetTitle>Invite from the waitlist</SheetTitle>
                    <SheetDescription>One code per address, bound to it, for one use.</SheetDescription>
                    {inviting && (
                        <div className="mt-4">
                            <CommandFlow
                                command="invite.issue"
                                target={{ waitlist_ids: [...selected] }}
                                targetLabel={`${selected.size} waitlist request(s)`}
                                onDone={() => {
                                    setSelected(new Set());
                                    void waitlist.refresh();
                                }}
                                onCancel={() => setInviting(false)}
                            />
                        </div>
                    )}
                </SheetContent>
            </Sheet>
        </div>
    );
}
