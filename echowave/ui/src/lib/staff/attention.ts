export type Attention = {
    key: string;
    title: string;
    count: number | null;
    href: string;
    severity: "critical" | "warning" | "info";
    state?: string;
    reason?: string;
};

const RANK = { critical: 0, warning: 1, info: 2 } as const;

/** Exceptions with something in them first, by severity; then the clear
 *  ones; then the ones that cannot be measured yet. Stable within a group,
 *  so rows do not reorder under the pointer on refresh. */
export function orderAttention(items: Attention[]): Attention[] {
    const group = (a: Attention) => (a.count === null ? 2 : a.count > 0 ? 0 : 1);
    return [...items].sort((a, b) => group(a) - group(b) || RANK[a.severity] - RANK[b.severity]);
}
