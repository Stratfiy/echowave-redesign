import {
  Bot,
  CalendarClock,
  ChartColumnBig,
  Database,
  Globe,
  Handshake,
  Home,
  Key,
  KeyRound,
  type LucideIcon,
  Megaphone,
  Phone,
  PhoneCall,
  Rocket,
  Settings,
  Shield,
  ShieldCheck,
  ShoppingBag,
  SlidersHorizontal,
  UserCog,
  Wallet,
  Workflow,
} from "lucide-react";

export type SidebarNavItem = {
  title: string;
  url: string;
  icon: LucideIcon;
  showsTelephonyWarning?: boolean;
  /** Related routes belonging to this destination. */
  activePaths?: string[];
  /** Extra words the top-bar search should match on. The visible title is what
   *  someone reads; it is rarely what they type. "Bot Runs" is where calls
   *  are listed, and nobody searching for a call types "runs". */
  keywords?: string[];
  /** Hide from members below organization admin.
   *
   *  Set it on a destination whose *whole* purpose is admin-gated on the
   *  server, never on one that merely contains an admin control. Billing is
   *  the counter-example: topping up is deliberately open to every member —
   *  "a member who cannot top up when the balance runs out is a member who
   *  cannot work" — so hiding the screen would stop a member paying us. Gate
   *  the mandate and the tax profile inside that screen instead, and leave
   *  the door open. */
  requiresOrganizationAdmin?: boolean;
  /** Hide from support-tier staff; show only to the superadmin tier.
   *
   *  For staff destinations whose server routes are `get_superuser`, not
   *  `get_staff` — the billing console, impersonation, provider keys, the
   *  partner and privacy screens. Support staff review KYC and nothing else,
   *  so a link they can only be refused is a link they should not see. The
   *  KYC review queue itself carries no flag: it is `get_staff` by design and
   *  is the one staff screen support is meant to use. */
  requiresSuperadmin?: boolean;
};

export type SidebarNavSection = {
  label?: string;
  items: SidebarNavItem[];
};

// Shown only to staff. The review queue, the platform key vault and the
// cross-account run list are reached from here.
//
// It was previously reachable only by typing /superadmin into the address bar:
// nothing in the product linked to it, so a reviewer had to be told the URL by
// somebody who already knew it. A queue whose promise is turnaround cannot
// depend on that.
export const STAFF_SECTION: SidebarNavSection = {
  label: "STAFF",
  items: [
    // The one staff screen support is meant to use: KYC review, backed by
    // get_staff. It points at the queue itself (/superadmin/verification), not
    // the /superadmin console hub, which is superadmin-only below.
    {
      title: "Review queue",
      url: "/superadmin/verification",
      icon: ShieldCheck,
      keywords: ["staff", "admin", "approve", "kyc", "verification"],
    },
    // The console hub, whose headline act is impersonation — superadmin only.
    // Support renders none of it; showing them the door is showing them a 403.
    {
      title: "Staff console",
      url: "/superadmin",
      icon: UserCog,
      requiresSuperadmin: true,
      keywords: ["impersonate", "superadmin", "console", "support login"],
    },
    // Its own entry rather than a tab under the KYC queue: the two are read by
    // different people for different reasons — one is a compliance check, the
    // other is a commercial decision that sets a recurring cost.
    {
      title: "Partner applications",
      url: "/superadmin/partners",
      icon: Handshake,
      requiresSuperadmin: true,
      keywords: ["reseller", "agency", "commission", "developer", "partner"],
    },
    // Whether a customer's calls sit on Decibyl's carrier account lives on
    // the account's own page (superadmin/billing/accounts/[id]) next to the
    // rest of that account's settings — this entry is only the platform-wide
    // pool, which has no account of its own to live on.
    {
      title: "Shared outbound numbers",
      url: "/superadmin/telephony/shared-outbound",
      icon: Phone,
      requiresSuperadmin: true,
      keywords: ["trial", "caller id", "shared", "outbound", "telephony"],
    },
    // Deployment-wide, like the billing readiness screen it mirrors — not an
    // account's page, so it has none to live on.
    {
      title: "Privacy readiness",
      url: "/superadmin/privacy/readiness",
      icon: Shield,
      requiresSuperadmin: true,
      keywords: ["dpdp", "gdpr", "compliance", "breach", "grievance officer"],
    },
  ],
};

export const NAV_SECTIONS: SidebarNavSection[] = [
  {
    items: [
      {
        // "Home", not "Overview". The screen stopped being an overview the
        // day it opened on a greeting and a composer: it says what happened
        // and lets you act, and the charts it used to lead with are below the
        // fold. "Overview" told a reader to expect a dashboard.
        title: "Decibyl",
        url: "/overview",
        icon: Home,
        keywords: ["home", "dashboard", "overview", "start"],
      },
      // Beside Home rather than under a BUILD heading of its own. In the Home
      // panel that heading had exactly one item under it, and a group label
      // over a single row is a heading that explains nothing and costs a line.
      // Models live on the agent — a voice, an LLM and a transcriber are
      // properties of an agent, chosen on its Models tab. The workspace
      // default is a setting, under Settings → Model defaults.
      {
        // The desk: the diary, the in-tray and the contact book, three tabs
        // of one screen. Tasks means the scheduled ones, Requests is the
        // board people and bots file work on, and Contacts came from Setup --
        // it sat beside Campaigns because a campaign dials a list, but that
        // is delivery, and a person you deal with is desk work.
        title: "Tasks",
        url: "/tasks",
        activePaths: ["/requests", "/schedules", "/contacts", "/deliverables"],
        icon: CalendarClock,
        keywords: [
          "desk", "tasks", "scheduled", "routine", "schedule", "every morning", "daily",
          "requests", "board", "kanban", "delegate", "hand-off", "todo",
          "contacts", "contact list", "caller", "customers", "phone book",
          "email", "address",
        ],
      },
      {
        // "Bots", not "Team", and not because "Team" was unfriendly.
        //
        // It meant three different things in one product: this list of bots,
        // `organization_members` (the people), and the Hire-an-Expert form
        // (hiring a human). A word that names three things names none of
        // them, and the one it named least well is this one -- a list of
        // software you hired, shown with a line about what each just did.
        //
        // Channels are the groups inside this list. A bot belongs to at most
        // one, which is what `WorkflowModel.folder_id` has always modelled.
        //
        // "bots" and "team" both stay as search keywords: a rename that
        // makes a destination unsearchable is a rename that loses it.
        title: "Agents",
        url: "/workflow",
        // The old models page redirects here; keep it lit while it does.
        activePaths: ["/model-configurations"],
        icon: Bot,
        keywords: [
          // "bots" first: it is what the rail used to say and what most
          // people still type. A rename that makes a destination unsearchable
          // is a rename that loses it.
          "agents", "agent", "your bots",
          "bot",
          "team",
          "bots",
          "workflow",
          "voice bot",
          "builder",
          "canvas",
          "flow",
          "model",
          "llm",
          "stt",
          "tts",
          "voice",
          "provider",
        ],
      },
      // The partner programme is a commercial arrangement on the account, so
      // it lives as a tab on Billing rather than as its own door in WORKSPACE.
    ],
  },
  {
    items: [
      // Pre-recorded audio clips are agent material, like documents: both are
      // things an agent draws on mid-call, so they share one door with a tab
      // between them.
      {
        // "Files", not "Knowledge base". The row names what is in it -- the
        // documents and clips a bot reads -- rather than the category the
        // industry files them under.
        title: "Knowledge",
        url: "/files",
        activePaths: ["/recordings"],
        icon: Database,
        keywords: [
          "files",
          "knowledge base",
          "upload",
          "document",
          "recordings",
          "audio",
          "clips",
          "playback",
        ],
      },
      // The shop, one row. Its departments -- Bots, Tools, Skills,
      // Integrations -- are tabs across the top of it, the way Buzz's own
      // directory carries All channels / Joined / Archived. As four rows they
      // read as four unrelated features, and one of them shared a word with
      // the account's own Your tools below.
      {
        title: "Marketplace",
        url: "/marketplace",
        activePaths: [
          "/marketplace/tools",
          "/marketplace/skills",
          "/marketplace/integrations",
          "/integrations/apps",
        ],
        icon: ShoppingBag,
        keywords: [
          "marketplace",
          "bots",
          "templates",
          "hire",
          "add",
          "shop",
          "tools",
          "actions",
          "skills",
          "procedure",
          "playbook",
          "integrations",
          "apps",
          "connectors",
          "connect",
          "gmail",
          "whatsapp",
        ],
      },
      // What this account has, not what it could add: the catalogue of apps
      // is the Marketplace's Integrations tab, and it was a second copy of
      // the same list under a second name.
      {
        title: "Your tools",
        url: "/tools",
        activePaths: ["/integrations", "/provider-keys"],
        icon: KeyRound,
        keywords: [
          "byok",
          "api key",
          "credential",
          "secret",
          "vault",
          "provider keys",
          "tools",
          "apps",
          "crm",
          "zoho",
          "hubspot",
          "salesforce",
          "zapier",
          "n8n",
          "make",
          "sheets",
          "slack",
          "whatsapp",
          "function",
          "webhook",
          "calendar",
          "google calendar",
        ],
      },
      {
        title: "Billing",
        url: "/billing",
        // /analytics/spend lives under /analytics for the shared date range,
        // but it answers a money question and wears the Billing tab strip.
        // The match is most-specific-first, so it lights Billing here while
        // /analytics itself still lights Calls.
        activePaths: ["/partner", "/analytics/spend"],
        icon: Wallet,
        keywords: [
          "credit",
          "top up",
          "invoice",
          "payment",
          "balance",
          "spend",
          "partner",
          "reseller",
          "agency",
          "commission",
          "referral",
        ],
      },
    ],
  },
  {
    label: "DEPLOY",
    items: [
      {
        title: "Phone numbers",
        url: "/telephony-configurations",
        activePaths: ["/numbers", "/verified-numbers", "/verification"],
        icon: Phone,
        showsTelephonyWarning: true,
        keywords: [
          "plivo",
          "twilio",
          "telnyx",
          "vonage",
          "sip",
          "carrier",
          "kyc",
          "gst",
          "compliance",
          "documents",
          "verification",
          "phone number",
          "did",
          "rent",
          "buy",
          "otp",
          "test number",
          "my number",
          "trial",
          "verify phone",
          "missed calls",
          "callback",
        ],
      },
      {
        title: "Campaigns",
        url: "/campaigns",
        icon: Megaphone,
        keywords: ["outbound", "dial", "csv", "bulk"],
      },
      {
        title: "Web widget",
        url: "/deploy/web-widget",
        icon: Globe,
        keywords: [
          "embed",
          "website",
          "script",
          "widget",
          "web call",
          "snippet",
          "install",
          "wordpress",
          "shopify",
          "wix",
        ],
      },
    ],
  },
  {
    label: "MONITOR",
    items: [
      // One entry for one subject. Review, Calls and Analytics were three
      // sidebar rows and two tab strips over the same calls; somebody asking
      // "how did yesterday go" had to guess which. They are tabs of this now
      // (CALLS_TABS), so the answer is one click from one place.
      {
        title: "Calls",
        url: "/usage",
        activePaths: ["/reports", "/review", "/analytics", "/missed-calls"],
        icon: PhoneCall,
        keywords: [
          "bot runs",
          "call logs",
          "history",
          "logs",
          "transcripts",
          "reports",
          "export",
          "csv",
          "download",
          "review",
          "qa",
          "grades",
          "quality",
          "analytics",
          "charts",
          "trends",
        ],
      },
    ],
  },
  {
    label: "DEVELOPERS",
    items: [
      {
        // "API keys", not "API keys & SDKs". It sat directly above another
        // row beginning with the same word, and a panel where two of four
        // rows start "API" is a panel somebody reads twice.
        title: "API keys",
        url: "/api-keys",
        icon: Key,
        keywords: ["api", "sdk", "mcp", "token"],
      },
      {
        // "Connect", which is what its own page has always called itself:
        // make something else start a call and get the result back. The
        // sidebar said "API & webhooks", so the row and the page it opened
        // disagreed about what this is.
        title: "Connect",
        url: "/deploy/connect",
        icon: Workflow,
        keywords: [
          "api",
          "webhook",
          "trigger",
          "n8n",
          "zapier",
          "make",
          "integration",
          "crm",
          "zoho",
          "hubspot",
          "meta",
          "facebook",
          "lead ads",
          "google sheet",
          "automation",
        ],
      },
    ],
  },
  {
    label: "WORKSPACE",
    items: [
      // Privacy and the do-not-call list are the two halves of one obligation
      // — the rights of the people being called — so they share a door.
      {
        title: "Compliance",
        url: "/privacy",
        activePaths: ["/do-not-call"],
        icon: Shield,
        keywords: [
          "privacy",
          "retention",
          "erasure",
          "dpdp",
          "gdpr",
          "do not call",
          "dnd",
          "suppression",
          "opt out",
          "tcccpr",
          "trai",
          "blocklist",
        ],
      },
      { title: "Settings", url: "/settings", icon: Settings, keywords: ["account", "workspace", "preferences"] },
    ],
  },
];

/** Shared by the sidebar and page search so both respect the same roles. */
export function getVisibleNavSections(roles: { isStaff: boolean; isOrganizationAdmin: boolean; isSuperadmin?: boolean }): SidebarNavSection[] {
  return (roles.isStaff ? [...NAV_SECTIONS, STAFF_SECTION] : NAV_SECTIONS)
    .map(section => ({ ...section, items: section.items.filter(item =>
      (!item.requiresOrganizationAdmin || roles.isOrganizationAdmin) &&
      (!item.requiresSuperadmin || Boolean(roles.isSuperadmin))
    ) }))
    .filter(section => section.items.length > 0);
}

/** Segment boundaries avoid matching /workflow-templates as /workflow.
 * The most specific match keeps nested staff pages from selecting two links. */
export function getActiveNavUrl(pathname: string, sections: SidebarNavSection[]): string | undefined {
  return sections.flatMap(section => section.items)
    .flatMap(item => [item.url, ...(item.activePaths ?? [])].map(path => ({ path, url: item.url })))
    .filter(({ path }) => pathname === path || pathname.startsWith(`${path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0]?.url;
}


/* ------------------------------------------------------------------------ *
 * The rail
 * ------------------------------------------------------------------------ */

/**
 * The five contexts the rail offers, each opening its own panel.
 *
 * Seventeen destinations in one scrolling list is a control panel. Every
 * workspace tool the market has settled on uses the same shape instead: a
 * narrow rail of a few contexts, and one wide panel that swaps entirely. Slack
 * has five — Home, DMs, Activity, More, Admin — and "Bots & tools" gets a
 * panel of its own rather than three entries in a list.
 *
 * `NAV_SECTIONS` stays the single source of truth for the items themselves, so
 * search, the role filter and the active-URL match are untouched by this. A
 * context only says which panel an item is reached through.
 */
export type NavContextId = "home" | "activity" | "marketplace" | "setup" | "account";

export type NavContext = {
  id: NavContextId;
  title: string;
  icon: LucideIcon;
  /** Item urls reached through this panel. See CONTEXT_FALLBACK for the rest. */
  urls: string[];
};

/**
 * Where an unassigned destination goes.
 *
 * A rail built from an allowlist is the silent-absence bug with a map: add a
 * nav entry, forget to place it, and it does not appear anywhere — no error,
 * no symptom, just a screen nobody can reach. Account is the catch-all, and
 * `every nav item reaches a panel` in the tests is what proves nothing falls
 * through. Deliberately a blocklist-shaped rule: the worst case is a
 * destination filed under the wrong heading, which somebody notices.
 */
export const CONTEXT_FALLBACK: NavContextId = "account";

export const NAV_CONTEXTS: NavContext[] = [
  {
    id: "home",
    title: "Home",
    icon: Home,
    // The bots are listed under Home by SidebarBots, which reads the roster
    // rather than this list — this is the door to all of them.
    urls: ["/overview", "/workflow", "/tasks", "/requests", "/schedules"],
  },
  {
    id: "activity",
    title: "Activity",
    icon: ChartColumnBig,
    // What the bots have been doing, and the work being pushed at them.
    urls: ["/review", "/usage", "/analytics", "/campaigns", "/contacts"],
  },
  {
    id: "marketplace",
    title: "Marketplace",
    icon: ShoppingBag,
    // The whole shop -- tools, bots, the apps a bot reaches -- and beside
    // it what this account has taken from it.
    urls: [
      "/marketplace",
      "/marketplace/tools",
      "/marketplace/skills",
      "/marketplace/integrations",
      "/tools",
    ],
  },
  {
    id: "setup",
    title: "Setup",
    icon: SlidersHorizontal,
    // The things a bot needs before it can work: a number, what it knows,
    // where it is embedded, and the keys other software reaches it with.
    urls: [
      "/telephony-configurations",
      "/files",
      "/deploy/web-widget",
      "/api-keys",
      "/deploy/connect",
    ],
  },
  {
    id: "account",
    title: "Account",
    icon: Settings,
    // Plus anything unplaced, and the whole staff section.
    urls: ["/billing", "/privacy", "/settings"],
  },
];

/** Which panel a destination is reached through. Never undefined. */
export function contextIdForUrl(url: string): NavContextId {
  return NAV_CONTEXTS.find(context => context.urls.includes(url))?.id ?? CONTEXT_FALLBACK;
}

/**
 * The sections to render in one panel, keeping each section's own label and
 * order. A section contributing no items to this context is dropped rather
 * than rendered as an empty heading.
 */
export function getContextSections(
  contextId: NavContextId,
  sections: SidebarNavSection[],
): SidebarNavSection[] {
  return sections
    .map(section => ({
      ...section,
      items: section.items.filter(item => contextIdForUrl(item.url) === contextId),
    }))
    .filter(section => section.items.length > 0);
}


/* ------------------------------------------------------------------------ *
 * The shell (SHELL_2026_09_ENABLED)
 * ------------------------------------------------------------------------ */

/**
 * Everyday work, always on the sidebar: the same five rows as before.
 */
export const SHELL_WORK_URLS = ["/overview", "/tasks", "/workflow", "/files", "/usage"] as const;

export type ShellEntry = {
  title: string;
  icon: LucideIcon;
  /** A single destination... */
  url?: string;
  /** ...or a group that opens to several, each with the label it has there. */
  children?: { url: string; title: string }[];
};

/**
 * Everything else, in one short "Manage" group at the foot of the sidebar.
 *
 * Before this, ten destinations -- the Marketplace, phone numbers, billing,
 * settings among them -- were reachable only from the profile menu, under
 * headings (Marketplace, Setup, Account) that appeared nowhere else, and the
 * visible sidebar had an "Activity tools" heading over one row. Five entries,
 * two of which open, put every screen on the sidebar without making it long.
 *
 * Urls, never copies of items: titles, icons, roles and the active match all
 * still come from NAV_SECTIONS, and `every destination is on the sidebar` in
 * the tests fails if a new nav item is placed nowhere.
 */
export const SHELL_MANAGE: ShellEntry[] = [
  { title: "Marketplace", icon: ShoppingBag, url: "/marketplace" },
  { title: "Apps & tools", icon: KeyRound, url: "/tools" },
  {
    title: "Deploy",
    icon: Rocket,
    children: [
      { url: "/telephony-configurations", title: "Phone numbers" },
      { url: "/campaigns", title: "Campaigns" },
      { url: "/deploy/web-widget", title: "Web widget" },
      { url: "/api-keys", title: "API keys" },
      { url: "/deploy/connect", title: "Webhooks & triggers" },
    ],
  },
  { title: "Billing", icon: Wallet, url: "/billing" },
  {
    title: "Settings",
    icon: Settings,
    children: [
      { url: "/settings", title: "General" },
      { url: "/privacy", title: "Compliance" },
    ],
  },
];

/** SHELL_MANAGE as this person may see it: an entry or child whose nav item
 *  their role hides is dropped, and a group left empty goes with it. */
export function visibleShellManage(sections: SidebarNavSection[]): ShellEntry[] {
  const visible = new Set(sections.flatMap((s) => s.items.map((i) => i.url)));
  return SHELL_MANAGE.flatMap((entry) => {
    if (entry.url) return visible.has(entry.url) ? [entry] : [];
    const children = (entry.children ?? []).filter((c) => visible.has(c.url));
    return children.length ? [{ ...entry, children }] : [];
  });
}

/** Every url the shell sidebar reaches, work rows and Manage together. */
export function shellUrls(): string[] {
  return [
    ...SHELL_WORK_URLS,
    ...SHELL_MANAGE.flatMap((e) => (e.url ? [e.url] : (e.children ?? []).map((c) => c.url))),
  ];
}
