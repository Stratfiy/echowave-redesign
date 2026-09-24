/**
 * Pictures for the product: a CC0 set of 3D icons (3dicons.co, by Vijay
 * Verma), served from public/art/3d. See public/art/3d/LICENSE.md.
 *
 * Only names that exist as files are ever returned, and never the purple
 * ones (lab, chart, bulb, magic-trick, chat-text): the palette is neutral
 * and the founder turned purple down.
 */

export type ArtName =
    | "bag"
    | "bell"
    | "calculator"
    | "calender"
    | "call-ringing"
    | "chat-bubble"
    | "chat"
    | "clock"
    | "computer"
    | "credit-card"
    | "crown"
    | "cube"
    | "file-text"
    | "folder"
    | "gift"
    | "headphone"
    | "location"
    | "lock"
    | "mail"
    | "map-pin"
    | "megaphone"
    | "mic"
    | "mobile"
    | "money-bag"
    | "notebook"
    | "notify-heart"
    | "picture"
    | "puzzle"
    | "rocket"
    | "rupee"
    | "setting"
    | "shield"
    | "sphere"
    | "target"
    | "tea-cup"
    | "tick"
    | "tools"
    | "travel"
    | "trophy"
    | "wallet";

/** The URL of one picture. */
export function art3d(name: ArtName): string {
    return `/art/3d/${name}.webp`;
}

/** Name fragment to picture, first match wins. The middle block is
 *  BotAvatar's BY_JOB in the same order, so an agent's picture and its icon
 *  agree; the phrases before it are more specific than anything in it
 *  ("answer staff questions" is the knowledge base, not the front desk),
 *  and the words after it only apply when the job list said nothing. */
const BY_JOB: [RegExp, ArtName][] = [
    [/knowledge|staff question|faq/i, "folder"],
    [/voice note/i, "mic"],
    [/data entry/i, "computer"],
    [/expense|invoice clerk/i, "credit-card"],
    [/real estate|listing|propert/i, "map-pin"],
    [/reservation|hotel/i, "tea-cup"],
    [/admission|education|school|college/i, "notebook"],
    [/kyc|compliance/i, "shield"],
    // BotAvatar's list, in its order.
    [/apostle|appointment|booking|schedul|calendar|slot/i, "calender"],
    [/quote|quotation|estimate|pricing|invoice/i, "file-text"],
    [/payment|collect|dues|reminder|recover|emi/i, "wallet"],
    [/receipt|billing|account/i, "rupee"],
    [/order|delivery|dispatch|courier|logistics|shipment/i, "travel"],
    [/stock|inventory|warehouse|parcel/i, "cube"],
    [/clinic|dental|doctor|patient|health|hospital/i, "notify-heart"],
    [/restaurant|kitchen|menu|food|table/i, "tea-cup"],
    [/front desk|reception|answer|inbound|helpline|support|helpdesk/i, "headphone"],
    [/call|dial|outbound|telecall/i, "call-ringing"],
    [/survey|feedback|form|checklist|audit/i, "notebook"],
    [/chat|whatsapp|message|enquiry|inquiry|lead/i, "chat-bubble"],
    // Only when nothing above matched.
    [/prospect|outreach|sales|customer/i, "megaphone"],
    [/document/i, "shield"],
    [/report|summary/i, "target"],
    [/\bbill/i, "credit-card"],
    [/approv/i, "tick"],
    [/return/i, "bag"],
    [/ticket/i, "tools"],
    [/coach/i, "trophy"],
];

/** The picture for an agent or a role called this. */
export function jobArt(name: string, fallback: ArtName = "rocket"): ArtName {
    for (const [pattern, art] of BY_JOB) {
        if (pattern.test(name)) return art;
    }
    return fallback;
}

const BY_INDUSTRY: Record<string, ArtName> = {
    Healthcare: "notify-heart",
    "Real estate": "map-pin",
    Lending: "money-bag",
    Education: "notebook",
    "E-commerce": "bag",
    Hospitality: "tea-cup",
    "Any business": "rocket",
    "Retail and D2C": "gift",
    Logistics: "travel",
};

/** The picture for a marketplace industry shelf. */
export function industryArt(name: string): ArtName {
    return BY_INDUSTRY[name] ?? "cube";
}
