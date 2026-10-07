"use client";

/**
 * My family (launch stream `care`): who is in my circle and what each one
 * sees. The older person's side.
 *
 * Adding someone, or letting them see more, shows a card naming exactly
 * what they will see; nothing is shared until the person confirms it, and
 * the family member still has to join with their code. Seeing less and
 * removing someone happen at once -- stopping must never wait.
 */

import { Loader2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    addMemberApiV1CareCircleMembersPost,
    careCardApiV1CareCardsEventIdGet,
    changeMemberSharesApiV1CareCircleMembersMemberIdSharesPut,
    myCircleApiV1CareCircleGet,
    nameMyCircleApiV1CareCirclePut,
    removeMemberApiV1CareCircleMembersMemberIdDelete,
} from "@/client/sdk.gen";
import type { Circle, CircleMember, TimelineEvent } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { ConsentCard } from "./ConsentCard";
import { MEMBER_STATE_WORDS, SHARE_LABELS } from "./copy";

const FIELD =
    "min-h-12 w-full rounded-lg border border-input bg-background px-3 text-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function ShareChoices({
    shares,
    chosen,
    onChange,
}: {
    shares: { key: string; label: string }[];
    chosen: string[];
    onChange: (next: string[]) => void;
}) {
    return (
        <fieldset className="flex flex-col gap-1">
            <legend className="mb-2 font-medium">What they can see</legend>
            {shares.map((share) => (
                <label key={share.key} className="flex min-h-11 items-start gap-3 py-1">
                    <input
                        type="checkbox"
                        className="mt-1 h-5 w-5 shrink-0"
                        checked={chosen.includes(share.key)}
                        onChange={(e) => onChange(e.target.checked ? [...chosen, share.key] : chosen.filter((k) => k !== share.key))}
                    />
                    <span className="text-base">{SHARE_LABELS[share.key] ?? share.label}</span>
                </label>
            ))}
        </fieldset>
    );
}

function MemberRow({
    member,
    shares,
    onChanged,
    onCard,
}: {
    member: CircleMember;
    shares: { key: string; label: string }[];
    onChanged: () => void;
    onCard: (card: TimelineEvent) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [chosen, setChosen] = useState<string[]>(member.shares);
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const saveShares = async () => {
        setBusy(true);
        setError(null);
        const response = await changeMemberSharesApiV1CareCircleMembersMemberIdSharesPut({
            path: { member_id: member.id },
            body: { shares: chosen },
        });
        setBusy(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "That was not saved. Try again."));
            return;
        }
        setEditing(false);
        if (response.data.card) onCard(response.data.card);
        onChanged();
    };
    const remove = async () => {
        setBusy(true);
        const response = await removeMemberApiV1CareCircleMembersMemberIdDelete({ path: { member_id: member.id } });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "They were not removed. Try again."));
            return;
        }
        onChanged();
    };

    return (
        <li className="flex flex-col gap-3 rounded-xl border border-border p-4" data-testid="circle-member">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                    <p className="text-lg font-semibold">{member.name}</p>
                    <p className="break-all text-sm text-muted-foreground">{member.email}</p>
                </div>
                <span className="rounded-full border border-border px-3 py-1 text-sm">{MEMBER_STATE_WORDS[member.status] ?? member.status}</span>
            </div>
            {member.shares.length > 0 ? (
                <ul className="flex list-disc flex-col gap-1 pl-5 text-base">
                    {member.shares.map((key) => (
                        <li key={key}>{SHARE_LABELS[key] ?? key}</li>
                    ))}
                </ul>
            ) : (
                member.status !== "revoked" && <p className="text-sm text-muted-foreground">Nothing shared yet.</p>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {member.status !== "revoked" && member.status !== "proposed" && (
                <>
                    {editing ? (
                        <div className="flex flex-col gap-3">
                            <ShareChoices shares={shares} chosen={chosen} onChange={setChosen} />
                            <div className="flex flex-wrap gap-2">
                                <Button type="button" className="min-h-11" disabled={busy} onClick={() => void saveShares()}>
                                    Save
                                </Button>
                                <Button type="button" variant="ghost" className="min-h-11" onClick={() => setEditing(false)}>
                                    Cancel
                                </Button>
                            </div>
                        </div>
                    ) : confirmRemove ? (
                        <div className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3">
                            <p>Stop sharing everything with {member.name}? They will not see anything from now on.</p>
                            <div className="flex flex-wrap gap-2">
                                <Button type="button" variant="destructive" className="min-h-11" disabled={busy} onClick={() => void remove()}>
                                    Yes, stop sharing
                                </Button>
                                <Button type="button" variant="ghost" className="min-h-11" onClick={() => setConfirmRemove(false)}>
                                    Keep sharing
                                </Button>
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-wrap gap-2">
                            <Button type="button" variant="outline" className="min-h-11" onClick={() => setEditing(true)}>
                                Change what they see
                            </Button>
                            <Button type="button" variant="ghost" className="min-h-11" onClick={() => setConfirmRemove(true)}>
                                Remove
                            </Button>
                        </div>
                    )}
                </>
            )}
        </li>
    );
}

export function CirclePanel() {
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [circle, setCircle] = useState<Circle | null>(null);
    const [cards, setCards] = useState<TimelineEvent[]>([]);
    const [name, setName] = useState("");
    const [adding, setAdding] = useState(false);
    const [newName, setNewName] = useState("");
    const [newEmail, setNewEmail] = useState("");
    const [newShares, setNewShares] = useState<string[]>(["medicine_alerts"]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saved, setSaved] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await myCircleApiV1CareCircleGet();
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not load your circle."));
            return;
        }
        setCircle(response.data);
        setName(response.data.display_name ?? "");
        const waiting = response.data.members.filter((m) => (m.status === "proposed" || m.pending_shares.length > 0) && m.consent_event_id);
        const found: TimelineEvent[] = [];
        for (const member of waiting) {
            const card = await careCardApiV1CareCardsEventIdGet({ path: { event_id: member.consent_event_id as number } });
            if (card.data && (card.data.payload as { state?: string }).state === "proposed") found.push(card.data);
        }
        setCards((had) => [...found, ...had.filter((c) => !found.some((f) => f.id === c.id))]);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    const saveName = async () => {
        setSaved(null);
        const response = await nameMyCircleApiV1CareCirclePut({ body: { display_name: name } });
        if (response.error) {
            setError(detailFromResult(response, "Not saved. Try again."));
            return;
        }
        setSaved("Saved.");
    };

    const add = async () => {
        setBusy(true);
        setError(null);
        const response = await addMemberApiV1CareCircleMembersPost({
            body: { name: newName, email: newEmail, shares: newShares },
        });
        setBusy(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "They were not added. Try again."));
            return;
        }
        if (response.data.card) setCards((all) => [response.data!.card as TimelineEvent, ...all]);
        setAdding(false);
        setNewName("");
        setNewEmail("");
        void load();
    };

    if (!circle) {
        return error ? (
            <p role="alert" className="text-destructive">
                {error}
            </p>
        ) : (
            <p className="text-muted-foreground">Loading your family circle…</p>
        );
    }

    const members = circle.members.filter((m) => m.status !== "revoked");
    return (
        <section className="flex flex-col gap-5" data-testid="circle">
            <p className="text-base">Your family sees only what you choose here. You can change it or stop at any time.</p>
            <label className="flex flex-col gap-2">
                <span className="font-medium">What does your family call you?</span>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <input className={FIELD} value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="For example: Amma" />
                    <Button type="button" variant="outline" className="min-h-12" onClick={() => void saveName()}>
                        Save
                    </Button>
                </div>
                {saved && <span role="status" className="text-sm text-muted-foreground">{saved}</span>}
            </label>
            {cards.map((card) => (
                <ConsentCard key={card.id} card={card} onChanged={() => void load()} />
            ))}
            {members.length === 0 ? (
                <p className="text-lg">Nobody in your circle yet.</p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {members.map((member) => (
                        <MemberRow
                            key={`${member.id}-${member.shares.join(",")}-${member.status}`}
                            member={member}
                            shares={circle.shares}
                            onChanged={() => void load()}
                            onCard={(card) => setCards((all) => [card, ...all])}
                        />
                    ))}
                </ul>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {adding ? (
                <form
                    className="flex flex-col gap-4 rounded-xl border border-border p-4"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void add();
                    }}
                    data-testid="circle-add"
                >
                    <h3 className="text-lg font-semibold">Add someone</h3>
                    <label className="flex flex-col gap-2">
                        <span className="font-medium">Their name</span>
                        <input className={FIELD} value={newName} maxLength={60} onChange={(e) => setNewName(e.target.value)} placeholder="For example: Priya (daughter)" required />
                    </label>
                    <label className="flex flex-col gap-2">
                        <span className="font-medium">Their email</span>
                        <input className={FIELD} type="email" autoComplete="off" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} required />
                    </label>
                    <ShareChoices shares={circle.shares} chosen={newShares} onChange={setNewShares} />
                    <Button type="submit" className="min-h-12 text-base" disabled={busy || newShares.length === 0}>
                        {busy && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                        Review before sharing
                    </Button>
                    <Button type="button" variant="ghost" className="min-h-11" onClick={() => setAdding(false)}>
                        Cancel
                    </Button>
                </form>
            ) : (
                <Button type="button" className="motion-m1 min-h-12 gap-2 self-start px-6 text-base" onClick={() => setAdding(true)}>
                    <UserPlus aria-hidden className="h-4 w-4" />
                    Add someone in my family
                </Button>
            )}
        </section>
    );
}

export default CirclePanel;
