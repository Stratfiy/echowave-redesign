"use client";

import { Copy, Loader2, Ticket } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
    listInvitesApiV1SuperuserInvitesGet,
    mintInvitesApiV1SuperuserInvitesPost,
    revokeInviteApiV1SuperuserInvitesInviteIdRevokePost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Invite codes (INVITE-1, KAN-273). Staff mint codes here while signup is
 * invite-only; each code admits one account unless minted for a group.
 */

interface Redemption {
    email: string;
    organization_id: number | null;
    door: string;
    redeemed_at: string | null;
}

interface Invite {
    id: number;
    code: string;
    email: string | null;
    max_uses: number;
    uses: number;
    note: string | null;
    created_at: string | null;
    expires_at: string | null;
    revoked: boolean;
    redemptions: Redemption[];
}

function inviteLink(code: string): string {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}/auth/signup?invite=${encodeURIComponent(code)}`;
}

async function copy(text: string) {
    try {
        await navigator.clipboard.writeText(text);
        toast.success("Copied");
    } catch {
        toast.error("Could not copy; select the text instead.");
    }
}

export default function InvitesPage() {
    const [invites, setInvites] = useState<Invite[] | null>(null);
    const [inviteOnly, setInviteOnly] = useState<boolean | null>(null);
    const [count, setCount] = useState("1");
    const [email, setEmail] = useState("");
    const [maxUses, setMaxUses] = useState("1");
    const [note, setNote] = useState("");
    const [minting, setMinting] = useState(false);
    const [fresh, setFresh] = useState<string[]>([]);

    const load = useCallback(async () => {
        const res = await listInvitesApiV1SuperuserInvitesGet();
        if (res.error || !res.data) {
            toast.error("Could not load invites");
            setInvites([]);
            return;
        }
        const data = res.data as { invites: Invite[]; invite_only: boolean };
        setInvites(data.invites);
        setInviteOnly(data.invite_only);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const mint = async (e: React.FormEvent) => {
        e.preventDefault();
        setMinting(true);
        try {
            const res = await mintInvitesApiV1SuperuserInvitesPost({
                body: {
                    count: Number(count) || 1,
                    email: email.trim() || null,
                    max_uses: Number(maxUses) || 1,
                    note: note.trim() || null,
                },
            });
            if (res.error || !res.data) {
                toast.error((res.error as { detail?: string })?.detail || "Could not mint codes");
                return;
            }
            setFresh((res.data as { codes: string[] }).codes);
            setEmail("");
            setNote("");
            await load();
        } finally {
            setMinting(false);
        }
    };

    const revoke = async (id: number) => {
        const res = await revokeInviteApiV1SuperuserInvitesInviteIdRevokePost({ path: { invite_id: id } });
        if (res.error) {
            toast.error("Could not revoke");
            return;
        }
        await load();
    };

    return (
        <div className="container mx-auto max-w-5xl space-y-6 px-4 py-8">
            <div className="space-y-1">
                <h1 className="flex items-center gap-2 text-2xl font-semibold">
                    <Ticket className="h-5 w-5" /> Invite codes
                </h1>
                <p className="text-sm text-muted-foreground" data-testid="invites-status">
                    {inviteOnly === null
                        ? "Checking signup mode…"
                        : inviteOnly
                            ? "Signup is invite-only: a new account needs one of these codes."
                            : "Signup is open (INVITE_ONLY_SIGNUP_ENABLED is off). Codes still work and are recorded."}
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Mint</CardTitle>
                    <CardDescription>
                        One code per person. Pin it to their email if you know it. A group code (more than one use) is for a clinic or a team.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={mint} className="grid gap-4 md:grid-cols-4">
                        <div className="space-y-1.5">
                            <Label htmlFor="inv-count">How many</Label>
                            <Input id="inv-count" inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value)} />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="inv-email">Email (optional)</Label>
                            <Input id="inv-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="owner@clinic.in" />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="inv-uses">Uses per code</Label>
                            <Input id="inv-uses" inputMode="numeric" value={maxUses} onChange={(e) => setMaxUses(e.target.value)} />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="inv-note">Note</Label>
                            <Input id="inv-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Who it is for" />
                        </div>
                        <div className="md:col-span-4">
                            <Button type="submit" disabled={minting} data-testid="invites-mint">
                                {minting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                Mint codes
                            </Button>
                        </div>
                    </form>

                    {fresh.length > 0 && (
                        <div className="mt-4 space-y-2 rounded-md border p-3" data-testid="invites-fresh">
                            <p className="text-sm font-medium">New codes. Each link opens signup with the code filled in.</p>
                            <ul className="space-y-1">
                                {fresh.map((code) => (
                                    <li key={code} className="flex flex-wrap items-center gap-2 text-sm">
                                        <span className="font-mono">{code}</span>
                                        <span className="min-w-0 break-all text-muted-foreground">{inviteLink(code)}</span>
                                        <Button type="button" size="sm" variant="ghost" onClick={() => copy(inviteLink(code))}>
                                            <Copy className="h-3.5 w-3.5" />
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>All codes</CardTitle>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                    {invites === null ? (
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    ) : invites.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No codes yet.</p>
                    ) : (
                        <table className="w-full text-sm" data-testid="invites-table">
                            <thead className="text-left text-muted-foreground">
                                <tr>
                                    <th className="py-2 pr-4 font-medium">Code</th>
                                    <th className="py-2 pr-4 font-medium">For</th>
                                    <th className="py-2 pr-4 font-medium">Used</th>
                                    <th className="py-2 pr-4 font-medium">Redeemed by</th>
                                    <th className="py-2 pr-4 font-medium" />
                                </tr>
                            </thead>
                            <tbody>
                                {invites.map((invite) => (
                                    <tr key={invite.id} className="border-t align-top">
                                        <td className="py-2 pr-4 font-mono">
                                            {invite.code}
                                            {invite.revoked && <span className="ml-2 text-xs text-destructive">revoked</span>}
                                        </td>
                                        <td className="py-2 pr-4">
                                            <div>{invite.email ?? "Anyone"}</div>
                                            {invite.note && <div className="text-xs text-muted-foreground">{invite.note}</div>}
                                        </td>
                                        <td className="py-2 pr-4 tabular-nums">{invite.uses} / {invite.max_uses}</td>
                                        <td className="py-2 pr-4">
                                            {invite.redemptions.length === 0
                                                ? <span className="text-muted-foreground">—</span>
                                                : invite.redemptions.map((r) => (
                                                    <div key={`${r.email}-${r.redeemed_at}`}>
                                                        {r.email} <span className="text-xs text-muted-foreground">via {r.door}</span>
                                                    </div>
                                                ))}
                                        </td>
                                        <td className="py-2 pr-4 text-right">
                                            <div className="flex justify-end gap-1">
                                                <Button size="sm" variant="ghost" onClick={() => copy(inviteLink(invite.code))}>Copy link</Button>
                                                {!invite.revoked && invite.uses < invite.max_uses && (
                                                    <Button size="sm" variant="ghost" onClick={() => revoke(invite.id)}>Revoke</Button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
