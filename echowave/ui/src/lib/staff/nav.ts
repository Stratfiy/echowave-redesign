/**
 * The staff console's eight destinations (handoff 32) and the existing
 * /superadmin screens grouped under them. The backend's role matrix is the
 * authority (`GET /admin/staff/me`); this file only decides where links go
 * and which destination a path belongs to.
 *
 * Existing screens keep their URLs and stay superadmin-only, because their
 * routes are: a finance person is not sent to /superadmin/billing to collect
 * 403s. The KYC queue is the exception -- support has always used it.
 */

export type DestinationKey =
    | "overview"
    | "users"
    | "support"
    | "quality"
    | "analytics"
    | "revenue"
    | "operations"
    | "controls";

export type StaffLink = {
    href: string;
    label: string;
    /** Existing superadmin screens: owner only. */
    legacy?: boolean;
    /** Built by another stream; shown, not linked, until it lands. */
    needsSetup?: string;
    /** Behind a switch: linked while it is on, shown as needs setup while off. */
    feature?: string;
    /** A page inside a destination that needs more than the destination. */
    capability?: string;
};

export type DestinationSpec = {
    key: DestinationKey;
    label: string;
    href: string;
    /** Where the destination opens instead while a switch is on. */
    hrefWhenOn?: { feature: string; href: string };
    children: StaffLink[];
};

/** The support stream's inbox and actions (screens 32-33). */
export const SUPPORT_INBOX_HREF = "/superadmin/support";

export const DESTINATIONS: DestinationSpec[] = [
    {
        key: "overview",
        label: "Overview",
        href: "/superadmin/overview",
        children: [{ href: "/superadmin", label: "Classic console", legacy: true }],
    },
    {
        key: "users",
        label: "Users and access",
        href: "/superadmin/users",
        children: [
            { href: "/superadmin/users", label: "People and waitlist" },
            { href: "/superadmin/invites", label: "Invite codes", legacy: true },
            { href: "/superadmin/billing/accounts", label: "Accounts", legacy: true },
        ],
    },
    {
        key: "support",
        label: "Support",
        href: "/superadmin/verification",
        hrefWhenOn: { feature: "support_inbox", href: SUPPORT_INBOX_HREF },
        children: [
            { href: SUPPORT_INBOX_HREF, label: "Inbox and cases", feature: "support_inbox" },
            { href: `${SUPPORT_INBOX_HREF}/actions`, label: "Support actions", feature: "support_actions" },
            { href: "/superadmin/verification", label: "KYC queue" },
        ],
    },
    {
        key: "quality",
        label: "Quality and evaluations",
        href: "/superadmin/quality",
        children: [{ href: "/superadmin/quality", label: "Evaluation runs" }],
    },
    {
        key: "analytics",
        label: "Product analytics",
        href: "/superadmin/analytics",
        children: [
            { href: "/superadmin/analytics", label: "Activation and usefulness" },
            { href: "/superadmin/billing/activation", label: "Signup activation", legacy: true },
            { href: "/superadmin/billing/retention", label: "Account retention", legacy: true },
        ],
    },
    {
        key: "revenue",
        label: "Revenue and costs",
        href: "/superadmin/revenue",
        children: [
            { href: "/superadmin/revenue", label: "Revenue and costs" },
            { href: "/superadmin/revenue/ledger", label: "Ledger and refunds" },
            { href: "/superadmin/billing", label: "Billing", legacy: true },
            { href: "/superadmin/billing/unit-economics", label: "Unit economics", legacy: true },
        ],
    },
    {
        key: "operations",
        label: "Operations",
        href: "/superadmin/operations",
        children: [
            { href: "/superadmin/operations", label: "Jobs and delivery" },
            { href: "/superadmin/operations/latency", label: "Voice latency" },
            { href: "/superadmin/operations/incidents", label: "Incidents", capability: "operations.read" },
            { href: "/superadmin/system", label: "System", legacy: true },
            { href: "/superadmin/runs", label: "Runs", legacy: true },
        ],
    },
    {
        key: "controls",
        label: "Controls and audit",
        href: "/superadmin/controls",
        children: [
            { href: "/superadmin/controls/providers", label: "Providers and secrets", capability: "providers.read" },
            { href: "/superadmin/controls/policy", label: "Flags, budgets, models", capability: "policy.read" },
            { href: "/superadmin/controls/roles", label: "Staff roles", capability: "roles.manage" },
            { href: "/superadmin/controls/audit", label: "Audit", capability: "audit.read" },
            { href: "/superadmin/flags", label: "Feature flags", legacy: true },
            { href: "/superadmin/provider-keys", label: "Provider keys", legacy: true },
        ],
    },
];

const NEW_PREFIXES: Array<[string, DestinationKey]> = [
    ["/superadmin/overview", "overview"],
    ["/superadmin/users", "users"],
    ["/superadmin/quality", "quality"],
    ["/superadmin/analytics", "analytics"],
    ["/superadmin/revenue", "revenue"],
    ["/superadmin/operations", "operations"],
    ["/superadmin/controls", "controls"],
    ["/superadmin/verification", "support"],
    [SUPPORT_INBOX_HREF, "support"],
];

function under(pathname: string, prefix: string): boolean {
    return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Which destination a path is in, and whether it is an existing
 *  superadmin-only screen. Unknown /superadmin paths are legacy (owner only):
 *  the safe direction for a page nobody mapped. */
export function classify(pathname: string): { destination: DestinationKey | null; legacy: boolean } {
    for (const [prefix, key] of NEW_PREFIXES) {
        if (under(pathname, prefix)) return { destination: key, legacy: false };
    }
    for (const d of DESTINATIONS) {
        if (d.children.some((c) => c.legacy && (c.href === "/superadmin" ? pathname === "/superadmin" : under(pathname, c.href)))) {
            return { destination: d.key, legacy: true };
        }
    }
    return { destination: null, legacy: true };
}

export type Me = {
    user_id: number;
    email: string | null;
    tier: string | null;
    mfa_enabled: boolean;
    roles: string[];
    capabilities: string[];
    destinations: Array<{ key: DestinationKey; label: string; href: string; allowed: boolean }>;
    environment: string;
    features: Record<string, boolean>;
};

/** Why a link is shown but not linked, or null when it is a link. */
export function linkNeedsSetup(me: Me, link: StaffLink): string | null {
    if (link.needsSetup) return link.needsSetup;
    if (link.feature && !me.features[link.feature]) return `${link.feature} is off`;
    return null;
}

/** Where a destination opens for this person. */
export function destinationHref(me: Me, d: DestinationSpec): string {
    return d.hrefWhenOn && me.features[d.hrefWhenOn.feature] ? d.hrefWhenOn.href : d.href;
}

/** The capability a page needs beyond its destination, if any. */
export function pageCapability(pathname: string): string | null {
    for (const d of DESTINATIONS) {
        for (const c of d.children) {
            if (c.capability && under(pathname, c.href)) return c.capability;
        }
    }
    return null;
}

export function canOpen(me: Me, pathname: string): boolean {
    const { destination, legacy } = classify(pathname);
    if (legacy) return me.roles.includes("owner");
    if (!destination) return false;
    if (!me.destinations.some((d) => d.key === destination && d.allowed)) return false;
    const needed = pageCapability(pathname);
    return needed === null || me.capabilities.includes(needed);
}

export function firstAllowed(me: Me): string {
    const d = me.destinations.find((x) => x.allowed);
    return d ? (DESTINATIONS.find((x) => x.key === d.key)?.href ?? "/superadmin/overview") : "/overview";
}
