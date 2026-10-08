"use client";

/**
 * A bot's face. Drawn as its blob (BlobFace) since the approved design of
 * October 2026; the job icon and tone below are what it used to draw, kept
 * for the places that still say what a bot does in a glyph.
 *
 * Every bot wore the same grey tile with two letters in it, so a roster of
 * eight read as eight grey squares and the eye had to fall back to reading
 * names. Buzz gives each of its agents a character you recognise before you
 * read anything; this is the same idea without asking anybody to draw one.
 *
 * The icon comes from what the bot is called, because a name in this product
 * is almost always its job -- "Front desk", "Quote desk", "Payment reminders".
 * When the name says nothing recognisable the icon falls back to a plain bot,
 * which is honest: we do not know what it does either.
 *
 * The colour is derived from the id, so it never changes under somebody as
 * they rename a bot, and two bots side by side almost never match.
 */

import {
    Bot,
    CalendarCheck,
    ClipboardList,
    FileText,
    Headset,
    type LucideIcon,
    MessageSquare,
    Package,
    PhoneCall,
    Receipt,
    Stethoscope,
    Truck,
    UtensilsCrossed,
    Wallet,
} from "lucide-react";

import type { Avatar } from "@/components/avatar/avatar";
import { BlobFace } from "@/components/brand/BlobFace";

/** Name fragment to icon, first match wins. Ordered most specific first:
 *  "appointment reminder" is a diary, not a bell. */
const BY_JOB: [RegExp, LucideIcon][] = [
    [/apostle|appointment|booking|schedul|calendar|slot/i, CalendarCheck],
    [/quote|quotation|estimate|pricing|invoice/i, FileText],
    [/payment|collect|dues|reminder|recover|emi/i, Wallet],
    [/receipt|billing|account/i, Receipt],
    [/order|delivery|dispatch|courier|logistics|shipment/i, Truck],
    [/stock|inventory|warehouse|parcel/i, Package],
    [/clinic|dental|doctor|patient|health|hospital/i, Stethoscope],
    [/restaurant|kitchen|menu|food|table/i, UtensilsCrossed],
    [/front desk|reception|answer|inbound|helpline|support|helpdesk/i, Headset],
    [/call|dial|outbound|telecall/i, PhoneCall],
    [/survey|feedback|form|checklist|audit/i, ClipboardList],
    [/chat|whatsapp|message|enquiry|inquiry|lead/i, MessageSquare],
];

/** The icon for a bot called this. Exported for the tests, and because the
 *  bots list and the panel draw the same face as the rail. */
export function botIcon(name: string): LucideIcon {
    for (const [pattern, icon] of BY_JOB) {
        if (pattern.test(name)) return icon;
    }
    return Bot;
}

/** Catppuccin's accents, the palette the rest of the frame is built from.
 *  Eight, so a roster of the size anybody actually runs rarely repeats. */
const TONES = [
    "text-slate-700 bg-slate-500/12", // slate
    "text-[#1e66f5] bg-[#1e66f5]/12", // blue
    "text-[#179299] bg-[#179299]/12", // teal
    "text-[#fe640b] bg-[#fe640b]/12", // peach
    "text-[#40a02b] bg-[#40a02b]/12", // green
    "text-[#ea76cb] bg-[#ea76cb]/12", // pink
    "text-[#df8e1d] bg-[#df8e1d]/12", // yellow
    "text-[#04a5e5] bg-[#04a5e5]/12", // sky
] as const;

/** The colour for a bot with this id. From the id rather than the name, so
 *  renaming a bot does not repaint it under somebody mid-sentence. */
export function botTone(id: number | string): string {
    const n = typeof id === "number" ? id : [...String(id)].reduce((a, c) => a + c.charCodeAt(0), 0);
    return TONES[Math.abs(n) % TONES.length];
}

/** Pixel size of the face for each named size. */
const SIZES = { sm: 24, md: 32, lg: 48 } as const;

/**
 * The agent's face: its blob (BlobFace), the same one the rail, the agents
 * grid and the agent's page draw. The job icon and tone above stay exported
 * for anything that still wants to say what a bot does in a glyph.
 */
export function BotAvatar({
    id,
    size = "sm",
    avatar,
    className,
}: {
    id: number | string;
    /** Beside the face wherever it is drawn; not read out a second time. */
    name: string;
    size?: keyof typeof SIZES;
    /** The agent's stored face, if its owner picked one. */
    avatar?: Partial<Avatar> | null;
    className?: string;
}) {
    return <BlobFace seed={id} avatar={avatar} size={SIZES[size]} className={className} />;
}

export default BotAvatar;
