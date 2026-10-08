/**
 * The marketplace's filing system, as pure functions.
 *
 * Two shelves. Bots are filed by **industry** (the business they are for)
 * and by **function** (the job they do), both supplied by the template
 * catalogue. Tools are filed by the category the connector catalogue
 * already assigns. Nothing here fetches; the screen does that, so the
 * filing can be tested on a list.
 *
 * Every filter below keeps what it cannot place rather than dropping it:
 * a template with no industry is filed under its vertical's first words, a
 * function nobody declared is filed under "Other". An absence cannot be
 * reviewed; a heading called Other can.
 */

import type { LucideIcon } from "lucide-react";
import {
    BookOpen,
    Bot,
    Briefcase,
    Building2,
    CalendarDays,
    ClipboardList,
    Cpu,
    FileText,
    FolderKanban,
    GraduationCap,
    Image,
    IndianRupee,
    Landmark,
    Mail,
    Megaphone,
    MessageCircle,
    MessageSquare,
    Puzzle,
    ShoppingCart,
    Sparkles,
    Stethoscope,
    Table2,
    Ticket,
    UserRound,
    Users,
    UtensilsCrossed,
    Video,
    Wrench,
} from "lucide-react";

export interface BotTemplate {
    id: string;
    name: string;
    vertical: string;
    industry?: string;
    function?: string;
    direction: "inbound" | "outbound" | "message" | "scheduled";
    summary: string;
    languages: string[];
    /** The life stage it is filed under, and the heading to show, both from
     *  the catalogue (`LIFE_STAGES` in agent_templates/_base.py). */
    life_stage?: string | null;
    life_stage_label?: string | null;
    /** A role that works without a number but rings somebody when a line
     *  exists: what the call is, that it needs a phone line, what it does
     *  instead, and where a line is set up. */
    call_step?: CallStep | null;
}

export interface CallStep {
    what: string;
    needs: string;
    instead: string;
    href: string;
}

export interface Shelf {
    name: string;
    count: number;
}

/** What the bot does with its channel, in words a business owner uses. */
export const DIRECTION_LABELS: Record<BotTemplate["direction"], string> = {
    inbound: "Takes calls",
    outbound: "Makes calls",
    message: "Answers messages",
    scheduled: "Runs on a schedule",
};

/** The industry a bot is filed under. Falls back to the vertical's first
 *  words ("Healthcare — clinics…" → "Healthcare") so a template that has not
 *  declared one still lands on a shelf. */
export function industryOf(template: Pick<BotTemplate, "industry" | "vertical">): string {
    if (template.industry) return template.industry;
    return template.vertical.split(/—|--/)[0].trim() || "Other";
}

export function functionOf(template: Pick<BotTemplate, "function">): string {
    return template.function || "Other";
}

/** Shelves in order of first appearance, with how many bots sit on each. */
function shelves(names: string[]): Shelf[] {
    const out: Shelf[] = [];
    for (const name of names) {
        const shelf = out.find((s) => s.name === name);
        if (shelf) shelf.count += 1;
        else out.push({ name, count: 1 });
    }
    return out;
}

export function industries(templates: BotTemplate[]): Shelf[] {
    return shelves(templates.map(industryOf));
}

export function functions(templates: BotTemplate[]): Shelf[] {
    return shelves(templates.map(functionOf));
}

/** The life stage a bot is filed under, by its heading; null for a bot the
 *  catalogue files under no stage. Falls back to the key, never to nothing,
 *  so a stage the screen has no heading for still shows. */
export function lifeStageOf(template: Pick<BotTemplate, "life_stage" | "life_stage_label">): string | null {
    if (!template.life_stage) return null;
    return template.life_stage_label || template.life_stage;
}

/** The life-stage shelves, in the catalogue's order. Only bots filed under a
 *  stage are counted: the rest are on the industry and function shelves. */
export function lifeStages(templates: BotTemplate[]): Shelf[] {
    return shelves(templates.map(lifeStageOf).filter((name): name is string => name !== null));
}

export interface BotFilter {
    query?: string;
    industry?: string | null;
    fn?: string | null;
    /** A life stage, by its heading. */
    stage?: string | null;
}

/** The bots that match every filter set. A blank query matches all. */
export function filterBots(templates: BotTemplate[], filter: BotFilter): BotTemplate[] {
    const q = (filter.query ?? "").trim().toLowerCase();
    return templates.filter((t) => {
        if (filter.stage && lifeStageOf(t) !== filter.stage) return false;
        if (filter.industry && industryOf(t) !== filter.industry) return false;
        if (filter.fn && functionOf(t) !== filter.fn) return false;
        if (!q) return true;
        const haystack = [t.name, t.vertical, industryOf(t), functionOf(t), lifeStageOf(t) ?? "", t.summary]
            .join(" ")
            .toLowerCase();
        return haystack.includes(q);
    });
}

/**
 * The tile behind a shelf's icon. Neutral, the way Buzz's directory is: the
 * icon says what the shelf is, and a rainbow of pastel tiles said nothing
 * but "colour". One tone, from the theme, so it follows light, dark and
 * whichever theme is chosen.
 */
const TONES = ["bg-muted text-foreground"] as const;

export function toneFor(name: string): string {
    let hash = 0;
    for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    return TONES[hash % TONES.length];
}

const INDUSTRY_ICONS: Record<string, LucideIcon> = {
    Healthcare: Stethoscope,
    "Real estate": Building2,
    Lending: Landmark,
    Education: GraduationCap,
    "E-commerce": ShoppingCart,
    Hospitality: UtensilsCrossed,
    "Any business": Briefcase,
    Procurement: ClipboardList,
    Personal: UserRound,
    Creators: Megaphone,
};

export function industryIcon(name: string): LucideIcon {
    return INDUSTRY_ICONS[name] ?? Bot;
}

/** The connector catalogue's groups, by the name the API sends. Anything
 *  not listed gets the puzzle piece rather than nothing. */
const TOOL_ICONS: Record<string, LucideIcon> = {
    Messaging: MessageSquare,
    whatsapp: MessageCircle,
    Email: Mail,
    "Calendar & booking": CalendarDays,
    "Customers & sales": Users,
    Money: IndianRupee,
    "Shop & shipping": ShoppingCart,
    "Spreadsheets & data": Table2,
    "Forms & intake": ClipboardList,
    "Files & documents": FileText,
    "Work management": FolderKanban,
    Marketing: Megaphone,
    Meetings: Video,
    People: UserRound,
    Learning: BookOpen,
    Events: Ticket,
    Devices: Cpu,
    Media: Image,
    AI: Sparkles,
    Developer: Wrench,
};

export function toolIcon(group: string): LucideIcon {
    return TOOL_ICONS[group] ?? Puzzle;
}

/** The template a marketplace card hands to the first-agent flow. */
export function hireHref(templateId: string): string {
    return `/start?template=${encodeURIComponent(templateId)}`;
}
