"use client";

import { Copy, Link2, Loader2, Trash2, UserPlus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    copyWorkspaceRoleApiV1WorkspaceRolesRoleIdCopyToPost,
    deleteWorkspaceRoleApiV1WorkspaceRolesRoleIdDelete,
    hireWorkspaceRoleApiV1WorkspaceRolesRoleIdHirePost,
    listMyOrganizationsApiV1OrganizationsMineGet,
    listWorkspaceRolesApiV1WorkspaceRolesGet,
    shareWorkspaceRoleApiV1WorkspaceRolesRoleIdSharePost,
    unshareWorkspaceRoleApiV1WorkspaceRolesRoleIdShareDelete,
} from "@/client/sdk.gen";
import type { UserOrganizationResponse } from "@/client/types.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { describeNeeds, type WorkspaceRole } from "@/lib/workspaceRoles";

/**
 * The workspace's own roles (MP-2), above the shared shelf: agents somebody
 * here tuned and saved, hired again with nothing asked twice. Sharing
 * (MP-3) lives on each card -- a link shown once, and a copy into another
 * workspace this person belongs to.
 *
 * Renders nothing, and asks for nothing, while the feature is switched off.
 */
export function WorkspaceRolesShelf() {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [roles, setRoles] = useState<WorkspaceRole[] | null>(null);
    const on = useFeature("workspace_roles");
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<number | null>(null);
    const [links, setLinks] = useState<Record<number, string>>({});
    const [others, setOthers] = useState<UserOrganizationResponse[]>([]);
    const { confirm, dialog } = useConfirm();

    useEffect(() => {
        if (!on || authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            const [result, orgs] = await Promise.all([
                listWorkspaceRolesApiV1WorkspaceRolesGet(),
                listMyOrganizationsApiV1OrganizationsMineGet(),
            ]);
            if (result.error) {
                setError(detailFromResult(result, "Your workspace's roles could not be loaded"));
                setRoles([]);
                return;
            }
            setRoles(((result.data as { roles?: WorkspaceRole[] })?.roles) ?? []);
            if (!orgs.error && Array.isArray(orgs.data)) {
                setOthers((orgs.data as UserOrganizationResponse[]).filter((o) => !o.is_selected));
            }
        })();
    }, [on, authLoading, user]);

    if (!on || roles === null) return null;

    const patch = (id: number, change: Partial<WorkspaceRole> | null) =>
        setRoles((prev) =>
            (prev ?? []).flatMap((r) => (r.id !== id ? [r] : change === null ? [] : [{ ...r, ...change }])),
        );

    const hire = async (role: WorkspaceRole) => {
        setBusy(role.id);
        setError(null);
        const result = await hireWorkspaceRoleApiV1WorkspaceRolesRoleIdHirePost({
            path: { role_id: role.id },
            body: { agent_name: null },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not hire that role"));
            return;
        }
        const id = (result.data as { id?: number })?.id;
        if (id) router.push(`/workflow/${id}`);
    };

    const share = async (role: WorkspaceRole) => {
        setError(null);
        const result = await shareWorkspaceRoleApiV1WorkspaceRolesRoleIdSharePost({
            path: { role_id: role.id },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not make a share link"));
            return;
        }
        const url = (result.data as { url?: string })?.url ?? "";
        setLinks((prev) => ({ ...prev, [role.id]: url }));
        patch(role.id, { shared: true });
    };

    const unshare = async (role: WorkspaceRole) => {
        const result = await unshareWorkspaceRoleApiV1WorkspaceRolesRoleIdShareDelete({
            path: { role_id: role.id },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not turn the link off"));
            return;
        }
        setLinks((prev) => {
            const next = { ...prev };
            delete next[role.id];
            return next;
        });
        patch(role.id, { shared: false });
    };

    const copyTo = async (role: WorkspaceRole, organizationId: number, name: string) => {
        const result = await copyWorkspaceRoleApiV1WorkspaceRolesRoleIdCopyToPost({
            path: { role_id: role.id },
            body: { organization_id: organizationId },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not copy that role"));
            return;
        }
        setError(null);
        toast.success(`Copied to ${name}. Switch to that workspace to hire it.`);
    };

    const remove = async (role: WorkspaceRole) => {
        const ok = await confirm({
            title: `Delete ${role.name}?`,
            description:
                "The role is removed from this workspace's shelf. Agents already hired from it keep working. A share link to it stops working.",
            confirmLabel: "Delete role",
            destructive: true,
        });
        if (!ok) return;
        const result = await deleteWorkspaceRoleApiV1WorkspaceRolesRoleIdDelete({
            path: { role_id: role.id },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not delete that role"));
            return;
        }
        patch(role.id, null);
    };

    return (
        <section className="space-y-3" aria-label="Your workspace's roles">
            {dialog}
            <h2 className="text-base font-semibold tracking-tight">Your workspace&apos;s roles</h2>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {roles.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    Tuned an agent the way you like it? Save it from its ⋮ menu as one of your roles, and hire it
                    again from here without answering anything twice.
                </p>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {roles.map((role) => (
                        <Card key={role.id}>
                            <CardContent className="flex h-full flex-col gap-2 p-4">
                                <div className="flex items-start justify-between gap-2">
                                    <p className="font-medium">{role.name}</p>
                                    <div className="flex gap-1">
                                        {role.shared && <Badge variant="brand">Shared</Badge>}
                                        {role.copied && <Badge variant="outline">Copied in</Badge>}
                                    </div>
                                </div>
                                {role.summary && (
                                    <p className="line-clamp-3 text-xs text-muted-foreground">{role.summary}</p>
                                )}
                                <p className="text-xs text-muted-foreground">
                                    {role.steps === 1 ? "1 step" : `${role.steps} steps`}
                                </p>
                                {role.needs.length > 0 && (
                                    <div className="rounded-md bg-muted/50 p-2 text-xs">
                                        <p className="font-medium">To connect after hiring</p>
                                        <ul className="list-disc pl-4 text-muted-foreground">
                                            {describeNeeds(role.needs).map((line) => (
                                                <li key={line}>{line}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                                {links[role.id] && (
                                    <div className="space-y-1 rounded-md border p-2 text-xs">
                                        <p className="font-medium">Share link, shown once</p>
                                        <p className="break-all text-muted-foreground">{links[role.id]}</p>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => void navigator.clipboard?.writeText(links[role.id])}
                                        >
                                            <Copy className="mr-1 h-3 w-3" /> Copy link
                                        </Button>
                                    </div>
                                )}
                                <div className="mt-auto flex flex-wrap gap-2 pt-1">
                                    <Button size="sm" disabled={busy === role.id} onClick={() => void hire(role)}>
                                        {busy === role.id ? (
                                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                        ) : (
                                            <UserPlus className="mr-1 h-3 w-3" />
                                        )}
                                        Hire
                                    </Button>
                                    <Button size="sm" variant="outline" onClick={() => void share(role)}>
                                        <Link2 className="mr-1 h-3 w-3" />
                                        {role.shared ? "New link" : "Share"}
                                    </Button>
                                    {role.shared && (
                                        <Button size="sm" variant="ghost" onClick={() => void unshare(role)}>
                                            Turn link off
                                        </Button>
                                    )}
                                    {others.length > 0 && (
                                        <select
                                            aria-label={`Copy ${role.name} to another workspace`}
                                            className="h-8 rounded-md border bg-background px-2 text-xs"
                                            value=""
                                            onChange={(e) => {
                                                const org = others.find((o) => String(o.id) === e.target.value);
                                                if (org) void copyTo(role, org.id, org.name);
                                            }}
                                        >
                                            <option value="">Copy to…</option>
                                            {others.map((o) => (
                                                <option key={o.id} value={o.id}>
                                                    {o.name}
                                                </option>
                                            ))}
                                        </select>
                                    )}
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        aria-label={`Delete ${role.name}`}
                                        onClick={() => void remove(role)}
                                    >
                                        <Trash2 className="h-3 w-3" />
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}
        </section>
    );
}
