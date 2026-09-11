"use client";

import { Building2, Check, ChevronsUpDown } from "lucide-react";
import { useEffect, useState } from "react";

import {
    listMyOrganizationsApiV1OrganizationsMineGet,
    switchOrganizationApiV1OrganizationsSelectedPut,
} from "@/client/sdk.gen";
import type { UserOrganizationResponse } from "@/client/types.gen";
import { useAuthReady } from "@/components/charts/primitives";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * Which account you are looking at, in the place a product puts that.
 *
 * It replaced the build number, which was the first thing a customer saw and
 * told them nothing they could use — and read as stale the moment it was one
 * release behind.
 *
 * **Switching reloads the page, deliberately.** Nearly everything cached in
 * the browser is scoped to an organization: agents, runs, numbers, the
 * balance. Swapping the selection and letting each store notice in its own
 * time is how one account's balance ends up beside another's calls. A reload
 * is blunt, happens once, and cannot leave two accounts on screen at the same
 * moment.
 *
 * A single-organization account sees a plain label rather than a control that
 * opens onto one choice.
 */
export function OrganizationSwitcher({ collapsed = false }: { collapsed?: boolean }) {
    const authReady = useAuthReady();
    const [organizations, setOrganizations] = useState<UserOrganizationResponse[]>([]);
    const [switching, setSwitching] = useState<number | null>(null);

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

    // One organization is not a choice. Showing a menu that opens onto the
    // thing already on screen is a control that does nothing.
    if (organizations.length === 1) {
        return (
            <div className="flex items-center gap-2 px-1 py-1 text-sm font-medium">
                <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
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
        <DropdownMenu>
            <DropdownMenuTrigger
                className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-sm font-medium outline-none hover:bg-sidebar-accent"
                aria-label="Switch organization"
            >
                <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="truncate">{current.name}</span>
                <ChevronsUpDown className="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-60">
                <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                    Your organizations
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                {organizations.map((organization) => (
                    <DropdownMenuItem
                        key={organization.id}
                        onSelect={() => void switchTo(organization.id)}
                        disabled={switching !== null}
                        className="gap-2"
                    >
                        <span className="truncate">{organization.name}</span>
                        {/* The role is here because it is the thing that differs
                            between two accounts with similar names, and because
                            a viewer who cannot edit should know before they try. */}
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
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
