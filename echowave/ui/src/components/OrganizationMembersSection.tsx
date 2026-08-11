"use client";

import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    getAuthUserApiV1UserAuthUserGet,
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

    if (loading) {
        return <p className="text-sm text-muted-foreground">Loading...</p>;
    }

    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                Everyone with access to this organization.
                {!isOwner && " Only an Owner can change roles or remove members."}
            </p>
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
