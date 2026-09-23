"use client";

/**
 * A workspace's own roles (MP-2, MP-3): shared types and wording. Whether
 * the feature is on is ``useFeature("workspace_roles")``.
 */

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
