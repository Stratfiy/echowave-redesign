import {
  AudioLines,
  Brain,
  ChartColumnBig,
  ContactRound,
  Database,
  FileText,
  Globe,
  Handshake,
  Home,
  Key,
  KeyRound,
  type LucideIcon,
  Megaphone,
  Phone,
  PhoneIncoming,
  PhoneOff,
  Settings,
  Shield,
  ShieldCheck,
  TrendingUp,
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
      {
        title: "Billing",
        url: "/billing",
        icon: Wallet,
        keywords: ["credit", "top up", "invoice", "payment", "balance"],
      },
    ],
  },
  {
    label: "BUILD",
    items: [
      {
        title: "Voice agents",
        url: "/workflow",
        icon: Workflow,
        keywords: ["workflow", "agent", "builder", "canvas", "flow"],
      },
      {
        title: "Models & voices",
        url: "/model-configurations",
        icon: Brain,
        keywords: ["llm", "stt", "tts", "voice", "provider"],
      },
      {
        title: "Knowledge base",
        url: "/files",
        icon: Database,
        keywords: ["files", "knowledge base", "upload", "document"],
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
      {
        title: "Call logs",
        url: "/usage",
        icon: TrendingUp,
        keywords: ["agent runs", "calls", "history", "logs", "transcripts"],
      },
      {
        title: "Analytics",
        url: "/analytics",
        icon: ChartColumnBig,
        keywords: ["metrics", "latency", "cost", "charts"],
      },
      {
        title: "Recordings",
        url: "/recordings",
        icon: AudioLines,
        keywords: ["audio", "playback", "transcript"],
      },
      { title: "Missed calls", url: "/missed-calls", icon: PhoneIncoming, keywords: ["callback", "refused", "inbound"] },
      {
        title: "Reports",
        url: "/reports",
        icon: FileText,
        keywords: ["export", "csv", "download"],
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
      {
        title: "Privacy",
        url: "/privacy",
        icon: Shield,
        keywords: ["retention", "erasure", "dpdp", "gdpr"],
      },
      {
        title: "Do not call",
        url: "/do-not-call",
        icon: PhoneOff,
        keywords: ["dnd", "suppression", "opt out", "tcccpr", "trai", "blocklist"],
      },
      {
        title: "Partner programme",
        url: "/partner",
        icon: Handshake,
        keywords: ["reseller", "agency", "commission", "developer", "referral"],
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
