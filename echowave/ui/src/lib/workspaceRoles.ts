"use client";

/**
 * A workspace's own roles (MP-2, MP-3): shared types and one question --
 * whether the feature is switched on here. The routes are a 404 while it is
 * off, which is the only signal the UI has; asked once per page load, after
 * auth has loaded, and shared.
 */

import { useEffect, useState } from "react";

import { listWorkspaceRolesApiV1WorkspaceRolesGet } from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";

export interface RoleNeed {
    step: string;
    kind: string;
}

export interface WorkspaceRole {
    id: number;
    name: string;
    summary: string | null;
    template_id: string | null;
    steps: number;
    needs: RoleNeed[];
    shared: boolean;
    copied: boolean;
}

let available: Promise<boolean> | null = null;

export function resetWorkspaceRolesAvailability(): void {
    available = null;
}

export function useWorkspaceRolesAvailable(): boolean {
    const [on, setOn] = useState(false);
    const { user, loading } = useAuth();
    useEffect(() => {
        if (loading || !user) return;
        let live = true;
        available ??= listWorkspaceRolesApiV1WorkspaceRolesGet()
            .then((result) => result.response?.status !== 404)
            .catch(() => false);
        void available.then((value) => {
            if (live) setOn(value);
        });
        return () => {
            live = false;
        };
    }, [loading, user]);
    return on;
}

/** "Answer: tools, documents" -- what a copied role still has to connect. */
export function describeNeeds(needs: RoleNeed[]): string[] {
    const byStep = new Map<string, string[]>();
    for (const need of needs) {
        const kinds = byStep.get(need.step) ?? [];
        if (!kinds.includes(need.kind)) kinds.push(need.kind);
        byStep.set(need.step, kinds);
    }
    return [...byStep].map(([step, kinds]) => `${step}: ${kinds.join(", ")}`);
}
