import {
  AudioLines,
  Brain,
  ChartColumnBig,
  ContactRound,
  Database,
  FileText,
  Handshake,
  Home,
  Key,
  KeyRound,
  type LucideIcon,
  Megaphone,
  Phone,
  PhoneOff,
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
    ],
  },
  {
    label: "BUILD",
    items: [
      {
        title: "Voice Agents",
        url: "/workflow",
        icon: Workflow,
        keywords: ["workflow", "agent", "builder", "canvas", "flow"],
      },
      {
        title: "Campaigns",
        url: "/campaigns",
        icon: Megaphone,
        keywords: ["outbound", "dial", "csv", "bulk"],
      },
      {
        title: "Models",
        url: "/model-configurations",
        icon: Brain,
        keywords: ["llm", "stt", "tts", "voice", "provider"],
      },
      // Sits under Models because that is where someone discovers they want it:
      // a slot in the model picker offers "your own key", and this is where the
      // key goes. Storing keys and choosing models are separate jobs, which is
      // why they are separate screens.
      //
      // One entry rather than two: provider keys and Tools used to be separate
      // peers in this list, with Google Calendar buried inside the provider
      // keys screen under no name of its own. All three are the same idea — an
      // outside account or service an agent can reach — so they are grouped
      // the way a competitor product groups them, under one "Integrations"
      // label, with a tab strip (components/integrations/IntegrationsTabs) on
      // the screens themselves. Every route is unchanged, so deep links —
      // including the three from the model editor and the Google OAuth
      // callback — still land where they did.
      //
      // Not admin-gated, despite storing secrets. `GET /api/v1/provider-keys`
      // deliberately takes plain `get_user` — only PUT, POST /active and
      // DELETE require ADMIN. Reading is open because BYOK is a member's job:
      // the model picker offers "your own key" per slot, and a member who
      // cannot see which keys the account holds cannot tell an empty slot from
      // one somebody already filled, or name the key they need in order to ask
      // for it. Keys are masked to their last four characters, so the screen
      // shows what exists rather than what it is. Adding, pausing and removing
      // are gated inside the screen.
      {
        title: "Integrations",
        url: "/integrations",
        icon: KeyRound,
        keywords: [
          "byok",
          "api key",
          "credential",
          "secret",
          "vault",
          "provider keys",
          "tools",
          "function",
          "webhook",
          "calendar",
          "google calendar",
        ],
      },
      // One entry, four screens. Verification, buying a number, carriers and
      // test numbers are one job done in sequence — get verified, buy a
      // number, point it at an agent — and as four peers in this list they
      // read as four unrelated features and took a quarter of the navigation.
      // The sequence now lives in a tab strip on the screens themselves
      // (components/telephony/TelephonyTabs); every route is unchanged, so
      // deep links and the keywords below still land where they did.
      {
        title: "Telephony",
        url: "/telephony-configurations",
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
        title: "Files",
        url: "/files",
        icon: Database,
        keywords: ["knowledge base", "upload", "document"],
      },
      // Next to Files because both are "things the agent reads", and an
      // account looking for where their customer data lives checks there
      // first. Not under Telephony: a list is attached to a number, but it
      // is not a property of one, and burying it inside the number dialog
      // would make it findable only by somebody already editing a number.
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
        title: "Recordings",
        url: "/recordings",
        icon: AudioLines,
        keywords: ["audio", "playback", "transcript"],
      },
      {
        title: "Developers",
        url: "/api-keys",
        icon: Key,
        keywords: ["api", "sdk", "mcp", "token"],
      },
    ],
  },
  {
    label: "MANAGE",
    items: [
      // Above the runs table because it answers the question people arrive
      // with — is this working, and what is it costing — where the table only
      // answers which calls happened.
      {
        title: "Analytics",
        url: "/analytics",
        icon: ChartColumnBig,
        keywords: ["metrics", "latency", "cost", "charts"],
      },
      {
        title: "Agent Runs",
        url: "/usage",
        icon: TrendingUp,
        keywords: ["calls", "history", "logs", "transcripts"],
      },
      {
        title: "Reports",
        url: "/reports",
        icon: FileText,
        keywords: ["export", "csv", "download"],
      },
      // Under MANAGE rather than its own section: on a prepaid account this is
      // where someone looks when calls stop, so it belongs next to the usage
      // that drained the balance.
      {
        title: "Billing",
        url: "/billing",
        icon: Wallet,
        keywords: ["credit", "top up", "invoice", "payment", "balance"],
      },
      // Under MANAGE beside Billing because that is what it is about: this
      // screen changes nothing about the product, only the commercial
      // arrangement behind it. Open to every member — asking is not spending,
      // and the person who notices there is a partner programme is rarely the
      // one holding the billing profile.
      {
        title: "Partner programme",
        url: "/partner",
        icon: Handshake,
        keywords: ["reseller", "agency", "commission", "developer", "referral"],
      },
      // Retention, erasure and export are obligations the account holder owes
      // the people they called, so they belong where an account is managed
      // rather than buried in Settings beside integration toggles.
      {
        title: "Privacy",
        url: "/privacy",
        icon: Shield,
        keywords: ["retention", "erasure", "dpdp", "gdpr"],
      },
      // Next to Privacy for the same reason Privacy is here: both are duties
      // the account holder owes the people they call. Suppression is the one
      // that stops a call from being placed, so it must be findable without
      // being told the URL — the mistake the staff review queue made.
      {
        title: "Do not call",
        url: "/do-not-call",
        icon: PhoneOff,
        keywords: ["dnd", "suppression", "opt out", "tcccpr", "trai", "blocklist"],
      },
    ],
  },
];
