"use client";

import { Copy, Loader2, Trash2, UserPlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    createInvitationApiV1OrganizationsInvitationsPost,
    getAuthUserApiV1UserAuthUserGet,
    listInvitationsApiV1OrganizationsInvitationsGet,
    listMembersApiV1OrganizationsMembersGet,
    removeMemberApiV1OrganizationsMembersUserIdDelete,
    revokeInvitationApiV1OrganizationsInvitationsInvitationIdDelete,
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

/** An offer of a seat that has not been taken up yet. */
type PendingInvitation = {
    id: number;
    email: string;
    role: OrganizationRole;
    expires_at: string;
};

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

    // Invitations. Kept beside the roster rather than on a screen of their
    // own: "who is in this account" and "who is on their way in" are one
    // question, and separating them is how somebody gets invited twice.
    const [invites, setInvites] = useState<PendingInvitation[]>([]);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<OrganizationRole>("member");
    const [inviting, setInviting] = useState(false);
    // Shown after a successful invite. The link is returned exactly once, and
    // on a deployment with no SMTP configured it is the only way the
    // invitation ever reaches anybody — so it goes on screen, not just in a
    // mail that may have silently failed.
    const [lastLink, setLastLink] = useState<string | null>(null);

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

            const invitesResponse = await listInvitationsApiV1OrganizationsInvitationsGet();
            if (!invitesResponse.error) {
                setInvites(
                    ((invitesResponse.data as { invitations?: PendingInvitation[] })
                        ?.invitations ?? []) as PendingInvitation[],
                );
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

    async function handleInvite() {
        const email = inviteEmail.trim();
        if (!email) return;
        setInviting(true);
        setLastLink(null);
        try {
            const response = await createInvitationApiV1OrganizationsInvitationsPost({
                body: { email, role: inviteRole },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not send the invitation"));
                return;
            }
            const body = response.data as {
                invitation: PendingInvitation;
                link: string;
                email_sent: boolean;
                email_error: string | null;
            };
            setInvites((prev) => [...prev, body.invitation]);
            setInviteEmail("");
            setLastLink(body.link);
            if (body.email_sent) {
                toast.success(`Invitation sent to ${email}`);
            } else {
                // Said plainly rather than shown as a success. An operator on a
                // deployment with no SMTP needs to know the mail did not go and
                // that the link below is the whole delivery mechanism.
                toast.warning(
                    "Invitation created, but the email could not be sent. Copy the link below.",
                );
            }
        } finally {
            setInviting(false);
        }
    }

    async function handleRevokeInvite(invite: PendingInvitation) {
        const response =
            await revokeInvitationApiV1OrganizationsInvitationsInvitationIdDelete({
                path: { invitation_id: invite.id },
            });
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not revoke the invitation"));
            return;
        }
        setInvites((prev) => prev.filter((i) => i.id !== invite.id));
        toast.success(`Invitation to ${invite.email} revoked`);
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

    if (loading) {
        return <p className="text-sm text-muted-foreground">Loading...</p>;
    }

    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                Everyone with access to this organization.
                {!isOwner && " Only an Owner can change roles or invite people."}
            </p>

            {isOwner && (
                <div className="space-y-3 rounded-lg border border-border p-4">
                    <div className="flex flex-wrap items-end gap-2">
                        <div className="min-w-[220px] flex-1 space-y-1.5">
                            <label
                                htmlFor="invite-email"
                                className="text-xs font-medium text-muted-foreground"
                            >
                                Invite someone by email
                            </label>
                            <Input
                                id="invite-email"
                                type="email"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") void handleInvite();
                                }}
                                placeholder="colleague@company.com"
                                disabled={inviting}
                            />
                        </div>
                        <Select
                            value={inviteRole}
                            onValueChange={(v) => setInviteRole(v as OrganizationRole)}
                            disabled={inviting}
                        >
                            <SelectTrigger className="w-32">
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
                        <Button
                            onClick={() => void handleInvite()}
                            disabled={inviting || !inviteEmail.trim()}
                        >
                            {inviting ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <UserPlus className="mr-2 h-4 w-4" />
                            )}
                            Invite
                        </Button>
                    </div>

                    {/* The link is returned exactly once. On a deployment with
                        no SMTP configured it is the only way the invitation
                        reaches anybody, so it goes on screen rather than only
                        into a mail that may have quietly failed. */}
                    {lastLink && (
                        <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
                            <code className="min-w-0 flex-1 truncate text-xs">
                                {lastLink}
                            </code>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                    void navigator.clipboard.writeText(lastLink);
                                    toast.success("Invitation link copied");
                                }}
                            >
                                <Copy className="mr-1.5 h-3.5 w-3.5" />
                                Copy
                            </Button>
                        </div>
                    )}
                </div>
            )}

            {invites.length > 0 && (
                <div className="space-y-2">
                    <p className="text-xs font-medium text-muted-foreground">
                        Invited, not yet accepted
                    </p>
                    <ul className="divide-y rounded-lg border border-border">
                        {invites.map((invite) => (
                            <li
                                key={invite.id}
                                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                            >
                                <span className="min-w-0 truncate text-sm">
                                    {invite.email}
                                    <span className="ml-2 text-xs text-muted-foreground">
                                        {ROLE_LABELS[invite.role] ?? invite.role}
                                    </span>
                                </span>
                                {isOwner && (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => void handleRevokeInvite(invite)}
                                    >
                                        Revoke
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
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
