/**
 * Everything Decibyl can be connected to, on one screen.
 *
 * Vapi, Bolna and Gnani all ship a page like this and it is not decoration:
 * it is the answer to the first question a business asks, which is not "how
 * do I build an agent" but "will it talk to the system I already run on". A
 * product that can reach Zoho and one that cannot look identical until
 * somebody says so.
 *
 * **Two states, and no third.** An entry either has somewhere to go — a
 * screen in this product that sets it up — or it does not exist yet and says
 * so. What this page must never do is show a logo for something that is not
 * built: a catalogue of twenty apps where three of them work is the most
 * expensive kind of lie, because the business finds out after signing.
 *
 * "Ask us" is not a euphemism for missing. Most of these can be running the
 * same week over the webhook that already exists — that is what the setup
 * call is for — and the ones that cannot, we would rather hear asked for than
 * guess at.
 */

export type ConnectMethod =
    /** Built in. Set it up on a screen in this product. */
    | "built-in"
    /** Paste a key from that account. */
    | "key"
    /** Sign in to that account and grant access. */
    | "oauth"
    /** The post-call webhook, or a webhook step mid-call. Works today. */
    | "webhook"
    /** A step added to the agent on the canvas. */
    | "agent-step"
    /** Not built. Tell us and we will wire it, usually on the setup call. */
    | "request";

export type IntegrationCategory =
    | "Telephony"
    | "Calendars"
    | "Messaging"
    | "CRM & sales"
    | "Automation"
    | "Models & voices"
    | "Your own systems";

export type CatalogueEntry = {
    id: string;
    name: string;
    category: IntegrationCategory;
    /** What it does for the business, not what it is. */
    blurb: string;
    connect: ConnectMethod;
    /** Where to set it up. Absent only for `request`. */
    href?: string;
};

/** The order the sections appear in, chosen by what a new account needs first. */
export const CATEGORY_ORDER: readonly IntegrationCategory[] = [
    "Telephony",
    "Calendars",
    "Messaging",
    "CRM & sales",
    "Automation",
    "Models & voices",
    "Your own systems",
] as const;

export const CONNECT_LABELS: Record<ConnectMethod, string> = {
    "built-in": "Set up",
    key: "Add key",
    oauth: "Connect",
    webhook: "Connect",
    "agent-step": "Add to an agent",
    request: "Ask us",
};

/** The one-line explanation under the button, so nobody clicks to find out. */
export const CONNECT_HINTS: Record<ConnectMethod, string> = {
    "built-in": "Built in",
    key: "Paste a key from your account",
    oauth: "Sign in and grant access",
    webhook: "Over the webhook — no code",
    "agent-step": "A step on the agent's canvas",
    request: "Not built yet. Ask and we will wire it",
};

export const CATALOGUE: readonly CatalogueEntry[] = [
    // --- Telephony -------------------------------------------------------
    // Every one of these is a provider package under
    // api/services/telephony/providers/, so all of them genuinely dial.
    {
        id: "twilio",
        name: "Twilio",
        category: "Telephony",
        blurb: "Buy numbers and place calls anywhere Twilio reaches",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "plivo",
        name: "Plivo",
        category: "Telephony",
        blurb: "India-friendly numbers and per-second billing",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "telnyx",
        name: "Telnyx",
        category: "Telephony",
        blurb: "Numbers and SIP on Telnyx's own network",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "vonage",
        name: "Vonage",
        category: "Telephony",
        blurb: "Numbers and voice on Vonage",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "cloudonix",
        name: "Cloudonix",
        category: "Telephony",
        blurb: "Numbers and SIP on Cloudonix",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "vobiz",
        name: "Vobiz",
        category: "Telephony",
        blurb: "Indian numbers and outbound trunks",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "asterisk",
        name: "Asterisk / SIP",
        category: "Telephony",
        blurb: "Point your existing PBX or SIP trunk at an agent",
        connect: "key",
        href: "/telephony-configurations",
    },
    {
        id: "managed-numbers",
        name: "Decibyl numbers",
        category: "Telephony",
        blurb: "Take a number from us and skip the carrier account entirely",
        connect: "built-in",
        href: "/numbers",
    },

    // --- Calendars -------------------------------------------------------
    {
        id: "google-calendar",
        name: "Google Calendar",
        category: "Calendars",
        blurb: "Read free slots and book the appointment during the call",
        connect: "oauth",
        // Connects on its own card; the href is only a fallback.
        href: "/integrations/apps",
    },
    {
        id: "calcom",
        name: "Cal.com",
        category: "Calendars",
        blurb: "Book into a Cal.com event type",
        connect: "request",
    },
    {
        id: "calendly",
        name: "Calendly",
        category: "Calendars",
        blurb: "Book into a Calendly event type",
        connect: "request",
    },
    {
        id: "outlook-calendar",
        name: "Outlook Calendar",
        category: "Calendars",
        blurb: "Book into a Microsoft 365 calendar",
        connect: "request",
    },

    // --- Messaging -------------------------------------------------------
    // SMS and WhatsApp both ship: `api/services/messaging/send.py` sends over
    // Twilio, Plivo and Twilio's WhatsApp Business API.
    {
        id: "whatsapp",
        name: "WhatsApp",
        category: "Messaging",
        blurb: "Send the brochure, the location or the payment link after the call",
        connect: "agent-step",
        href: "/workflow",
    },
    {
        id: "sms",
        name: "SMS",
        category: "Messaging",
        blurb: "Text a confirmation the moment the call ends",
        connect: "agent-step",
        href: "/workflow",
    },
    {
        id: "email",
        name: "Email",
        category: "Messaging",
        blurb: "Email the summary and recording to whoever needs it",
        connect: "agent-step",
        href: "/workflow",
    },
    {
        id: "slack",
        name: "Slack",
        category: "Messaging",
        blurb: "Drop every booked call into a channel as it happens",
        connect: "webhook",
        href: "/deploy/connect",
    },

    // --- CRM & sales -----------------------------------------------------
    // Nothing here is a bespoke package. All of them take the post-call
    // webhook, which is why they say webhook rather than pretending.
    {
        id: "zoho",
        name: "Zoho CRM",
        category: "CRM & sales",
        blurb: "Create or update the lead with the outcome and the transcript",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "hubspot",
        name: "HubSpot",
        category: "CRM & sales",
        blurb: "Log the call on the contact and move the deal stage",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "salesforce",
        name: "Salesforce",
        category: "CRM & sales",
        blurb: "Write the call and its outcome onto the lead",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "leadsquared",
        name: "LeadSquared",
        category: "CRM & sales",
        blurb: "Post the call activity against the lead",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "freshsales",
        name: "Freshsales",
        category: "CRM & sales",
        blurb: "Log the call and its outcome on the contact",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "google-sheets",
        name: "Google Sheets",
        category: "CRM & sales",
        blurb: "Append a row per call — the fastest way to see it working",
        connect: "webhook",
        href: "/deploy/connect",
    },

    // --- Automation ------------------------------------------------------
    {
        id: "n8n",
        name: "n8n",
        category: "Automation",
        blurb: "Trigger a workflow on every finished call",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "zapier",
        name: "Zapier",
        category: "Automation",
        blurb: "Send calls into any of Zapier's apps",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "make",
        name: "Make",
        category: "Automation",
        blurb: "Send calls into a Make scenario",
        connect: "webhook",
        href: "/deploy/connect",
    },
    {
        id: "mcp",
        name: "MCP servers",
        category: "Automation",
        blurb: "Give the agent your own tools to call mid-conversation",
        connect: "built-in",
        href: "/tools",
    },

    // --- Models & voices -------------------------------------------------
    // One card, not twenty. Which models exist is the model picker's job;
    // this page only needs to say that your own account can be used.
    {
        id: "provider-keys",
        name: "Your model accounts",
        category: "Models & voices",
        blurb:
            "Use your own OpenAI, Anthropic, Deepgram, ElevenLabs, Cartesia or "
            + "Sarvam account instead of ours",
        connect: "key",
        href: "/integrations",
    },

    // --- Your own systems ------------------------------------------------
    {
        id: "webhook",
        name: "Webhook",
        category: "Your own systems",
        blurb: "Post every call to your own endpoint, with retries",
        connect: "built-in",
        href: "/deploy/connect",
    },
    {
        id: "api",
        name: "API",
        category: "Your own systems",
        blurb: "Start calls and read results from your own software",
        connect: "built-in",
        href: "/api-keys",
    },
    {
        id: "widget",
        name: "Web widget",
        category: "Your own systems",
        blurb: "Put the agent on your own site as a call button",
        connect: "built-in",
        href: "/deploy/web-widget",
    },
] as const;

/** The catalogue grouped for rendering, in `CATEGORY_ORDER`. */
export function catalogueByCategory(
    entries: readonly CatalogueEntry[] = CATALOGUE,
): { category: IntegrationCategory; entries: CatalogueEntry[] }[] {
    return CATEGORY_ORDER.map((category) => ({
        category,
        entries: entries.filter((entry) => entry.category === category),
    })).filter((group) => group.entries.length > 0);
}

/** Case-insensitive search over the name and what it does. */
export function searchCatalogue(
    query: string,
    entries: readonly CatalogueEntry[] = CATALOGUE,
): CatalogueEntry[] {
    const needle = query.trim().toLowerCase();
    if (!needle) return [...entries];
    return entries.filter(
        (entry) =>
            entry.name.toLowerCase().includes(needle)
            || entry.blurb.toLowerCase().includes(needle)
            || entry.category.toLowerCase().includes(needle),
    );
}
