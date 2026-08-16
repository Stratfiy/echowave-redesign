import {
  AudioLines,
  Brain,
  ChartColumnBig,
  Database,
  FileText,
  Home,
  Key,
  KeyRound,
  type LucideIcon,
  Megaphone,
  Phone,
  PhoneCall,
  PhoneOff,
  PhoneOutgoing,
  Shield,
  ShieldCheck,
  TrendingUp,
  Wallet,
  Workflow,
  Wrench,
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
      // Admin-gated end to end: every route under /api/v1/provider-keys
      // requires ADMIN, because a key is a secret and spend under someone
      // else's contract. A member who is shown this screen can only collect a
      // 403 from it.
      {
        title: "Provider Keys",
        url: "/provider-keys",
        icon: KeyRound,
        keywords: ["byok", "api key", "credential", "secret", "vault"],
        requiresOrganizationAdmin: true,
      },
      {
        title: "Telephony",
        url: "/telephony-configurations",
        icon: Phone,
        showsTelephonyWarning: true,
        keywords: ["plivo", "twilio", "telnyx", "vonage", "sip", "carrier"],
      },
      // Sits beside Telephony because that is where someone discovers they
      // need it — a phone number is the only thing verification gates.
      {
        title: "Verification",
        url: "/verification",
        icon: ShieldCheck,
        keywords: ["kyc", "gst", "compliance", "documents"],
      },
      // Between Verification and "Get a number" because it is the cheaper way
      // to reach the same goal: hearing an agent on a real phone. Someone who
      // finds this first does not need to rent a number to try the product.
      {
        title: "Verified numbers",
        url: "/verified-numbers",
        icon: PhoneCall,
        keywords: ["otp", "test number", "my number", "trial", "verify phone"],
      },
      // Directly under Verification: approval is the first gate on this flow,
      // so the thing it unlocks belongs next to it.
      {
        title: "Get a number",
        url: "/numbers",
        icon: PhoneOutgoing,
        keywords: ["phone number", "did", "rent", "buy"],
      },
      {
        title: "Tools",
        url: "/tools",
        icon: Wrench,
        keywords: ["function", "integration", "webhook", "calendar"],
      },
      {
        title: "Files",
        url: "/files",
        icon: Database,
        keywords: ["knowledge base", "upload", "document"],
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
