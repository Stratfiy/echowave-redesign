import {
  Bot,
  ChartColumnBig,
  ContactRound,
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
  Settings,
  Shield,
  ShieldCheck,
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
   *  someone reads; it is rarely what they type. "Agent Runs" is where calls
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
    {
      title: "Review queue",
      url: "/superadmin",
      icon: ShieldCheck,
      keywords: ["staff", "admin", "approve", "kyc"],
    },
    // Its own entry rather than a tab under the KYC queue: the two are read by
    // different people for different reasons — one is a compliance check, the
    // other is a commercial decision that sets a recurring cost.
    {
      title: "Partner applications",
      url: "/superadmin/partners",
      icon: Handshake,
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
      keywords: ["trial", "caller id", "shared", "outbound", "telephony"],
    },
    // Deployment-wide, like the billing readiness screen it mirrors — not an
    // account's page, so it has none to live on.
    {
      title: "Privacy readiness",
      url: "/superadmin/privacy/readiness",
      icon: Shield,
      keywords: ["dpdp", "gdpr", "compliance", "breach", "grievance officer"],
    },
  ],
};

export const NAV_SECTIONS: SidebarNavSection[] = [
  {
    items: [
      {
        title: "Overview",
        url: "/overview",
        icon: Home,
        keywords: ["home", "dashboard", "start"],
      },
      // The partner programme is a commercial arrangement on the account, so
      // it lives as a tab on Billing rather than as its own door in WORKSPACE.
      {
        title: "Billing",
        url: "/billing",
        activePaths: ["/partner"],
        icon: Wallet,
        keywords: [
          "credit",
          "top up",
          "invoice",
          "payment",
          "balance",
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
    label: "BUILD",
    items: [
      // Models live on the agent — a voice, an LLM and a transcriber are
      // properties of an agent, chosen on its Models tab. The workspace
      // default is a setting, under Settings → Model defaults.
      {
        title: "Agents",
        url: "/workflow",
        // The old models page redirects here; keep it lit while it does.
        activePaths: ["/model-configurations"],
        icon: Bot,
        keywords: [
          "workflow",
          "voice agent",
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
      // Pre-recorded audio clips are agent material, like documents: both are
      // things an agent draws on mid-call, so they share one door with a tab
      // between them.
      {
        title: "Knowledge base",
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
      {
        title: "Integrations",
        url: "/integrations/apps",
        activePaths: ["/integrations", "/tools", "/provider-keys"],
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
    ],
  },
  {
    label: "DEPLOY",
    items: [
      {
        title: "Phone numbers",
        url: "/telephony-configurations",
        activePaths: ["/numbers", "/verified-numbers", "/verification", "/missed-calls"],
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
        title: "Contacts",
        url: "/contacts",
        icon: ContactRound,
        keywords: [
          "contact list",
          "caller",
          "inbound",
          "csv",
          "customers",
          "database",
          "import",
        ],
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
      // Daily reports are a view over the same calls, so they are a tab here
      // rather than a fifth MONITOR entry. Missed calls moved to Phone numbers,
      // where the telephony tab strip already listed them.
      {
        title: "Calls",
        url: "/usage",
        activePaths: ["/reports"],
        icon: PhoneCall,
        keywords: [
          "agent runs",
          "call logs",
          "history",
          "logs",
          "transcripts",
          "reports",
          "export",
          "csv",
          "download",
        ],
      },
      {
        title: "Analytics",
        url: "/analytics",
        icon: ChartColumnBig,
        keywords: ["metrics", "latency", "cost", "charts"],
      },
    ],
  },
  {
    label: "DEVELOPERS",
    items: [
      {
        title: "API keys & SDKs",
        url: "/api-keys",
        icon: Key,
        keywords: ["api", "sdk", "mcp", "token"],
      },
      {
        title: "API & webhooks",
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
export function getVisibleNavSections(roles: { isStaff: boolean; isOrganizationAdmin: boolean }): SidebarNavSection[] {
  return (roles.isStaff ? [...NAV_SECTIONS, STAFF_SECTION] : NAV_SECTIONS)
    .map(section => ({ ...section, items: section.items.filter(item => !item.requiresOrganizationAdmin || roles.isOrganizationAdmin) }))
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
