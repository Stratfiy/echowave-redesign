"use client";

/**
 * A bot's face: an icon for the job it does, in a colour of its own.
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

import { ArtImage } from "@/components/art/Art3D";
import { jobArt } from "@/lib/art";
import { cn } from "@/lib/utils";

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

const SIZES = {
    sm: { box: "h-6 w-6 rounded-md", glyph: "h-3.5 w-3.5", picture: 20 },
    md: { box: "h-8 w-8 rounded-lg", glyph: "h-4 w-4", picture: 26 },
    lg: { box: "h-12 w-12 rounded-xl", glyph: "h-6 w-6", picture: 40 },
} as const;

export function BotAvatar({
    id,
    name,
    size = "sm",
    art = false,
    className,
}: {
    id: number | string;
    name: string;
    size?: keyof typeof SIZES;
    /** Draw the job as a 3D picture on a soft tile instead of an icon. */
    art?: boolean;
    className?: string;
}) {
    const Icon = botIcon(name);
    const { box, glyph, picture } = SIZES[size];
    if (art) {
        return (
            <span
                aria-hidden="true"
                data-testid="bot-art"
                className={cn("flex shrink-0 items-center justify-center bg-muted", box, className)}
            >
                <ArtImage name={jobArt(name)} size={picture} />
            </span>
        );
    }
    return (
        <span
            aria-hidden="true"
            className={cn("flex shrink-0 items-center justify-center", box, botTone(id), className)}
        >
            <Icon className={glyph} />
        </span>
    );
}

export default BotAvatar;
