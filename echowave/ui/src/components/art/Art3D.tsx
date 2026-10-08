"use client";

/**
 * A job's picture, as decoration: no alt text, out of the accessibility tree.
 *
 * It was a glossy 3D render (public/art/3d). The founder found them heavy next
 * to the calm, border-led screens of the design handoff, and the handoff
 * itself says "no decorative 3D objects inside work screens". So each name is
 * now a soft pastel tile with a line icon -- the approved home design's pastel
 * blob, in a set. The names, their meanings and every caller stay the same;
 * the webp files remain in public/ for the marketing site.
 */

import {
    Bell,
    Box,
    Calculator,
    Calendar,
    Check,
    Clock,
    Coffee,
    CreditCard,
    Crown,
    FileText,
    Folder,
    Gift,
    Headphones,
    Heart,
    Image,
    IndianRupee,
    Lock,
    type LucideIcon,
    Mail,
    MapPin,
    Megaphone,
    MessageCircle,
    MessagesSquare,
    Mic,
    Monitor,
    NotebookPen,
    PhoneCall,
    PiggyBank,
    Plane,
    Puzzle,
    Rocket,
    Settings,
    Shield,
    ShoppingBag,
    Smartphone,
    Sparkles,
    Target,
    Trophy,
    Wallet,
    Wrench,
} from "lucide-react";

import type { ArtName } from "@/lib/art";
import { cn } from "@/lib/utils";

const ICON: Record<ArtName, LucideIcon> = {
    bag: ShoppingBag,
    bell: Bell,
    calculator: Calculator,
    calender: Calendar,
    "call-ringing": PhoneCall,
    "chat-bubble": MessagesSquare,
    chat: MessageCircle,
    clock: Clock,
    computer: Monitor,
    "credit-card": CreditCard,
    crown: Crown,
    cube: Box,
    "file-text": FileText,
    folder: Folder,
    gift: Gift,
    headphone: Headphones,
    location: MapPin,
    lock: Lock,
    mail: Mail,
    "map-pin": MapPin,
    megaphone: Megaphone,
    mic: Mic,
    mobile: Smartphone,
    "money-bag": PiggyBank,
    notebook: NotebookPen,
    "notify-heart": Heart,
    picture: Image,
    puzzle: Puzzle,
    rocket: Rocket,
    rupee: IndianRupee,
    setting: Settings,
    shield: Shield,
    sphere: Sparkles,
    target: Target,
    "tea-cup": Coffee,
    tick: Check,
    tools: Wrench,
    travel: Plane,
    trophy: Trophy,
    wallet: Wallet,
};

/** Background and ink per pastel; a name always lands on the same one. */
const PASTELS = [
    "bg-[#FDE9DD] text-[#B4532A] dark:bg-[#B4532A]/20 dark:text-[#F4B79A]", // peach
    "bg-[#DFF3E7] text-[#2F7A52] dark:bg-[#2F7A52]/25 dark:text-[#9FD9B8]", // mint
    "bg-[#ECE7FB] text-[#6550B8] dark:bg-[#6550B8]/25 dark:text-[#C3B6F2]", // lavender
    "bg-[#E1EEFB] text-[#2E69AE] dark:bg-[#2E69AE]/25 dark:text-[#A9CBEF]", // sky
    "bg-[#FBF0CC] text-[#94660A] dark:bg-[#94660A]/25 dark:text-[#EBD08A]", // butter
    "bg-[#FBE4EB] text-[#AE3B63] dark:bg-[#AE3B63]/25 dark:text-[#F0AFC4]", // rose
];

function pastelFor(name: string): string {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return PASTELS[h % PASTELS.length];
}

export function Art3D({ name, size, className }: { name: ArtName; size: number; className?: string }) {
    return <ArtImage name={name} size={size} className={className} />;
}

/** The picture itself: a pastel tile, rounded like the design's avatars. */
export function ArtImage({ name, size, className }: { name: ArtName; size: number; className?: string }) {
    const Icon = ICON[name] ?? Sparkles;
    return (
        <span
            aria-hidden="true"
            data-art={name}
            className={cn(
                "inline-flex shrink-0 select-none items-center justify-center rounded-[30%]",
                pastelFor(name),
                className,
            )}
            style={{ width: size, height: size }}
        >
            <Icon style={{ width: Math.round(size * 0.5), height: Math.round(size * 0.5) }} strokeWidth={1.75} />
        </span>
    );
}
