"use client";

import { Check, ChevronDown, Pencil } from "lucide-react";
import { useEffect, useState } from "react";

import {
    listMyOrganizationsApiV1OrganizationsMineGet,
    renameOrganizationApiV1OrganizationsSelectedPatch,
    switchOrganizationApiV1OrganizationsSelectedPut,
} from "@/client/sdk.gen";
import type { UserOrganizationResponse } from "@/client/types.gen";
import { useAuthReady } from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { detailFromError } from "@/lib/apiError";
import { cn } from "@/lib/utils";

/** Roles that may rename the workspace. Mirrors the ADMIN gate on the route. */
const CAN_RENAME = new Set(["admin", "owner"]);

/**
 * The workspace name at the head of the panel, and the menu behind it.
 *
 * Slack puts the workspace name where the eye lands first and hangs the
 * account menu off it; this is that. The menu is where you switch to another
 * organization you belong to, and where an admin renames this one -- the
 * name is the thing on every colleague's screen, so it lives with the
 * control that shows it rather than three pages away in settings.
 *
 * **Switching reloads the page, deliberately.** Nearly everything cached in
 * the browser is scoped to an organization: agents, runs, numbers, the
 * balance. Swapping the selection and letting each store notice in its own
 * time is how one account's balance ends up beside another's calls. A reload
 * is blunt, happens once, and cannot leave two accounts on screen at the same
 * moment. Renaming does not reload: nothing else on screen carries the name.
 *
 * A member of one organization with no right to rename it sees a plain
 * label rather than a menu that opens onto nothing they can do.
 */
export function OrganizationSwitcher({ collapsed = false }: { collapsed?: boolean }) {
    const authReady = useAuthReady();
    const [organizations, setOrganizations] = useState<UserOrganizationResponse[]>([]);
    const [switching, setSwitching] = useState<number | null>(null);
    const [renaming, setRenaming] = useState(false);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        void (async () => {
            const result = await listMyOrganizationsApiV1OrganizationsMineGet();
            if (cancelled || result.error) return;
            setOrganizations((result.data as UserOrganizationResponse[]) ?? []);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    if (collapsed || organizations.length === 0) return null;

    const current = organizations.find((o) => o.is_selected) ?? organizations[0];
    const canRename = CAN_RENAME.has(current.role);
    const canSwitch = organizations.length > 1;

    if (!canRename && !canSwitch) {
        return (
            <div className="flex items-center gap-1 px-2 py-1.5 text-sm font-semibold">
                <span className="truncate">{current.name}</span>
            </div>
        );
    }

    const switchTo = async (organizationId: number) => {
        if (organizationId === current.id) return;
        setSwitching(organizationId);
        const result = await switchOrganizationApiV1OrganizationsSelectedPut({
            body: { organization_id: organizationId },
        });
        if (result.error) {
            setSwitching(null);
            return;
        }
        window.location.assign("/");
    };

    return (
        <>
            <DropdownMenu>
                <DropdownMenuTrigger
                    className="flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-sm font-semibold outline-none hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label="Workspace menu"
                >
                    <span className="truncate">{current.name}</span>
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-60">
                    {canSwitch && (
                        <>
                            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                                Your organizations
                            </DropdownMenuLabel>
                            {organizations.map((organization) => (
                                <DropdownMenuItem
                                    key={organization.id}
                                    onSelect={() => void switchTo(organization.id)}
                                    disabled={switching !== null}
                                    className="gap-2"
                                >
                                    <span className="truncate">{organization.name}</span>
                                    {/* The role is here because it is the thing that
                                        differs between two accounts with similar names,
                                        and because a viewer who cannot edit should know
                                        before they try. */}
                                    <span className="ml-auto flex items-center gap-2">
                                        <span className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                                            {organization.role}
                                        </span>
                                        <Check
                                            className={cn(
                                                "h-3.5 w-3.5",
                                                organization.is_selected ? "opacity-100" : "opacity-0",
                                            )}
                                        />
                                    </span>
                                </DropdownMenuItem>
                            ))}
                        </>
                    )}
                    {canRename && (
                        <>
                            {canSwitch && <DropdownMenuSeparator />}
                            <DropdownMenuItem onSelect={() => setRenaming(true)} className="gap-2">
                                <Pencil className="h-3.5 w-3.5 text-muted-foreground" />
                                Rename organization
                            </DropdownMenuItem>
                        </>
                    )}
                </DropdownMenuContent>
            </DropdownMenu>
            {canRename && (
                <RenameDialog
                    open={renaming}
                    current={current}
                    onClose={() => setRenaming(false)}
                    onRenamed={(renamed) =>
                        setOrganizations((all) =>
                            all.map((o) => (o.id === renamed.id ? { ...o, name: renamed.name } : o)),
                        )
                    }
                />
            )}
        </>
    );
}

function RenameDialog({
    open,
    current,
    onClose,
    onRenamed,
}: {
    open: boolean;
    current: UserOrganizationResponse;
    onClose: () => void;
    onRenamed: (organization: UserOrganizationResponse) => void;
}) {
    const [name, setName] = useState(current.name);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Reopening starts from the name on screen, not from a half-typed one
    // abandoned last time.
    useEffect(() => {
        if (open) {
            setName(current.name);
            setError(null);
        }
    }, [open, current.name]);

    const save = async () => {
        const trimmed = name.trim();
        if (!trimmed || trimmed === current.name) {
            onClose();
            return;
        }
        setSaving(true);
        setError(null);
        const result = await renameOrganizationApiV1OrganizationsSelectedPatch({
            body: { name: trimmed },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not rename the organization"));
            return;
        }
        onRenamed(result.data as UserOrganizationResponse);
        onClose();
    };

    return (
        <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
            <DialogContent className="sm:max-w-sm">
                <form
                    onSubmit={(event) => {
                        event.preventDefault();
                        void save();
                    }}
                >
                    <DialogHeader>
                        <DialogTitle>Rename organization</DialogTitle>
                        <DialogDescription>
                            Everyone in it sees this name in the sidebar.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="py-4">
                        <Input
                            aria-label="Organization name"
                            value={name}
                            maxLength={120}
                            autoFocus
                            onChange={(event) => setName(event.target.value)}
                        />
                        {error && (
                            <p role="alert" className="mt-2 text-sm text-destructive">
                                {error}
                            </p>
                        )}
                    </div>
                    <DialogFooter>
                        <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={saving || !name.trim()}>
                            {saving ? "Saving…" : "Save"}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
