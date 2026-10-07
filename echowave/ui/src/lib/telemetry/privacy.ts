/**
 * What the browser may record (handoff 35; design "Metrics events and
 * release proof").
 *
 * Replay is off everywhere unless the `session_replay` flag is on, and even
 * then it runs only on the screens listed in REPLAY_ALLOWED_PREFIXES, with
 * every input and every piece of text masked. An allowlist rather than a
 * blocklist on purpose: the cost of a screen missing from it is a missing
 * recording, which somebody notices; the cost of a sensitive screen missing
 * from a blocklist is a recording of somebody's secrets, which nobody does.
 *
 * SENSITIVE_SCREENS maps the design's sensitive screens (23-27, 31-33, 35,
 * 38, 42-44) to the routes that carry them today, so a test can prove none of
 * them is ever allowed.
 */

export const REPLAY_ALLOWED_PREFIXES: readonly string[] = [
    "/overview",
    "/tasks",
    "/schedules",
    "/agents",
    "/marketplace",
    "/start",
    "/activity",
];

export const SENSITIVE_SCREENS: Readonly<Record<string, readonly string[]>> = {
    "23 Email identity": ["/settings/channels"],
    "24 Phone and verification": ["/settings/phone-number", "/verified-numbers", "/numbers"],
    "25 Privacy and security": ["/settings/compliance", "/settings/advanced"],
    "26 Model defaults and overrides": ["/settings/models"],
    "27 Skills workspace and developer": ["/settings/developer"],
    "31 User and workspace detail": ["/superadmin"],
    "32 Support inbox and case": ["/superadmin"],
    "33 Support action preview and execution": ["/superadmin"],
    "35 Evaluation case comparison": ["/superadmin/runs", "/review"],
    "38 Ledger transaction and refund": ["/superadmin/billing", "/billing"],
    "42 Providers and secret lifecycle": ["/superadmin/provider-keys", "/settings/apps"],
    "43 Flags budgets and model policy": ["/superadmin/flags"],
    "44 Staff roles and audit": ["/superadmin/audit", "/roles"],
};

function matches(pathname: string, prefix: string): boolean {
    return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Whether replay may run on this path. Unknown paths are not allowed. */
export function isReplayAllowed(pathname: string | null | undefined): boolean {
    if (!pathname) return false;
    const sensitive = Object.values(SENSITIVE_SCREENS).some((routes) =>
        routes.some((route) => matches(pathname, route)),
    );
    if (sensitive) return false;
    return REPLAY_ALLOWED_PREFIXES.some((prefix) => matches(pathname, prefix));
}

/** PostHog replay options: everything masked, whatever the page. */
export const REPLAY_OPTIONS = {
    maskAllInputs: true,
    maskTextSelector: "*",
} as const;

/**
 * Person properties for `posthog.identify`. While `telemetry_redaction` is on
 * no email address or name is sent: the distinct id is enough to join events.
 */
export function identifyProperties(
    email: string | undefined | null,
    name: string | undefined | null,
    redact: boolean,
): Record<string, string> {
    if (redact) return {};
    return {
        ...(email ? { email } : {}),
        ...(name ? { name } : {}),
    };
}
