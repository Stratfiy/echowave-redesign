"use client";

import { Loader2, Mail, Trash2, UserPlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    getAuthUserApiV1UserAuthUserGet,
    inviteMemberApiV1OrganizationsMembersPost,
    listMembersApiV1OrganizationsMembersGet,
    removeMemberApiV1OrganizationsMembersUserIdDelete,
    updateMemberRoleApiV1OrganizationsMembersUserIdPatch,
} from "@/client/sdk.gen";
import type {
    OrganizationMemberResponse,
    OrganizationRole,
} from "@/client/types.gen";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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

const ROLE_LABELS: Record<OrganizationRole, string> = {
    owner: "Owner",
    admin: "Admin",
    member: "Member",
};

export function OrganizationMembersSection() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [members, setMembers] = useState<OrganizationMemberResponse[]>([]);
    const [myUserId, setMyUserId] = useState<number | null>(null);
    const [isOwner, setIsOwner] = useState(false);
    const [loading, setLoading] = useState(true);
    const [pendingUserId, setPendingUserId] = useState<number | null>(null);
    const [removeTarget, setRemoveTarget] =
        useState<OrganizationMemberResponse | null>(null);

    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<OrganizationRole>("member");
    const [inviting, setInviting] = useState(false);
    // Kept on screen rather than only toasted: the person who sent it needs to
    // be able to tell the invitee what to expect, and a toast is gone by then.
    const [lastInvited, setLastInvited] = useState<string | null>(null);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void fetchMembers();
    }, [authLoading, user]);

    async function fetchMembers() {
        setLoading(true);
        try {
            const [authResponse, membersResponse] = await Promise.all([
                getAuthUserApiV1UserAuthUserGet(),
                listMembersApiV1OrganizationsMembersGet(),
            ]);

            if (authResponse.error) {
                toast.error(detailFromError(authResponse.error, "Failed to load your role"));
            } else if (authResponse.data) {
                setMyUserId(authResponse.data.id);
                setIsOwner(authResponse.data.organization_role === "owner");
            }

            if (membersResponse.error) {
                toast.error(
                    detailFromError(membersResponse.error, "Failed to load team members"),
                );
                return;
            }
            setMembers(membersResponse.data?.members ?? []);
        } catch {
            toast.error("Failed to load team members");
        } finally {
            setLoading(false);
        }
    }

    async function handleRoleChange(
        member: OrganizationMemberResponse,
        role: OrganizationRole,
    ) {
        setPendingUserId(member.user_id);
        try {
            const response = await updateMemberRoleApiV1OrganizationsMembersUserIdPatch({
                path: { user_id: member.user_id },
                body: { role },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not change the role"));
                return;
            }
            setMembers((prev) =>
                prev.map((m) => (m.user_id === member.user_id ? { ...m, role } : m)),
            );
            toast.success(`${member.email ?? "Member"} is now ${ROLE_LABELS[role]}`);
        } finally {
            setPendingUserId(null);
        }
    }

    async function handleRemove() {
        if (!removeTarget) return;
        setPendingUserId(removeTarget.user_id);
        try {
            const response = await removeMemberApiV1OrganizationsMembersUserIdDelete({
                path: { user_id: removeTarget.user_id },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not remove the member"));
                return;
            }
            setMembers((prev) => prev.filter((m) => m.user_id !== removeTarget.user_id));
            toast.success(`Removed ${removeTarget.email ?? "member"}`);
        } finally {
            setPendingUserId(null);
            setRemoveTarget(null);
        }
    }

    async function handleInvite() {
        const email = inviteEmail.trim();
        if (!email) return;
        setInviting(true);
        try {
            const response = await inviteMemberApiV1OrganizationsMembersPost({
                body: { email, role: inviteRole },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not send the invitation"));
                return;
            }
            // A failed send is reported rather than swallowed. The invitation is
            // staged either way, and an owner who is not told will wait for
            // somebody to accept an email that never arrived.
            const data = response.data as unknown as {
                email: string;
                email_sent: boolean;
                email_error: string | null;
            };
            if (data.email_sent) {
                toast.success(`Invitation sent to ${data.email}`);
                setLastInvited(data.email);
            } else {
                toast.error(
                    `The invitation is ready but the email could not be sent (${data.email_error}). Fix mail delivery and invite again.`,
                );
            }
            setInviteEmail("");
        } finally {
            setInviting(false);
        }
    }

    if (loading) {
        return <p className="text-sm text-muted-foreground">Loading...</p>;
    }

    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                Everyone with access to this organization.
                {!isOwner && " Only an Owner can change roles or remove members."}
            </p>
            {isOwner && (
                <div className="rounded-xl border border-border bg-muted/30 px-4 py-3">
                    <div className="flex flex-wrap items-end gap-3">
                        <div className="min-w-[220px] flex-1 space-y-1.5">
                            <Label htmlFor="invite-email" className="text-xs">
                                Invite by email
                            </Label>
                            <Input
                                id="invite-email"
                                type="email"
                                autoComplete="off"
                                placeholder="colleague@company.com"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") void handleInvite();
                                }}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="invite-role" className="text-xs">
                                Role
                            </Label>
                            <Select
                                value={inviteRole}
                                onValueChange={(v) => setInviteRole(v as OrganizationRole)}
                            >
                                <SelectTrigger id="invite-role" className="w-32">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {(Object.keys(ROLE_LABELS) as OrganizationRole[]).map(
                                        (role) => (
                                            <SelectItem key={role} value={role}>
                                                {ROLE_LABELS[role]}
                                            </SelectItem>
                                        ),
                                    )}
                                </SelectContent>
                            </Select>
                        </div>
                        <Button
                            onClick={() => void handleInvite()}
                            disabled={inviting || !inviteEmail.trim()}
                        >
                            {inviting ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <UserPlus className="mr-2 h-4 w-4" />
                            )}
                            Send invitation
                        </Button>
                    </div>
                    {/* Says what happens next, because the invitee does not
                        appear in the table until they accept — and an owner who
                        expects a row and sees none assumes it failed. */}
                    <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
                        <Mail className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        {lastInvited
                            ? `${lastInvited} has a code and 72 hours to set a password. They appear here once they accept.`
                            : "They get a code and choose their own password. Nobody joins the list until they accept."}
                    </p>
                </div>
            )}

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Email</TableHead>
                        <TableHead>Role</TableHead>
                        {isOwner && <TableHead className="text-right"></TableHead>}
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {members.map((member) => {
                        const isBusy = pendingUserId === member.user_id;
                        const isSelf = member.user_id === myUserId;
                        return (
                            <TableRow key={member.user_id}>
                                <TableCell className="whitespace-nowrap">
                                    {member.email || `User #${member.user_id}`}
                                    {isSelf && (
                                        <span className="ml-1 text-xs text-muted-foreground">
                                            (you)
                                        </span>
                                    )}
                                </TableCell>
                                <TableCell>
                                    {isOwner ? (
                                        <Select
                                            value={member.role}
                                            disabled={isBusy}
                                            onValueChange={(value) =>
                                                void handleRoleChange(
                                                    member,
                                                    value as OrganizationRole,
                                                )
                                            }
                                        >
                                            <SelectTrigger className="w-32">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {(
                                                    Object.keys(ROLE_LABELS) as OrganizationRole[]
                                                ).map((role) => (
                                                    <SelectItem key={role} value={role}>
                                                        {ROLE_LABELS[role]}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    ) : (
                                        ROLE_LABELS[member.role as OrganizationRole] ?? member.role
                                    )}
                                </TableCell>
                                {isOwner && (
                                    <TableCell className="text-right">
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8"
                                            disabled={isBusy}
                                            onClick={() => setRemoveTarget(member)}
                                            aria-label={`Remove ${member.email || "member"}`}
                                        >
                                            {isBusy ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : (
                                                <Trash2 className="h-4 w-4" />
                                            )}
                                        </Button>
                                    </TableCell>
                                )}
                            </TableRow>
                        );
                    })}
                </TableBody>
            </Table>

            <AlertDialog
                open={removeTarget !== null}
                onOpenChange={(open) => !open && setRemoveTarget(null)}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            Remove {removeTarget?.email || "this member"}?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            They will immediately lose access to this organization.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(e) => {
                                e.preventDefault();
                                void handleRemove();
                            }}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            Remove
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
