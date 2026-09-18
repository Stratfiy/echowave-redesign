"use client";

/**
 * The tab lists for destinations that share one sidebar entry.
 *
 * The sidebar names a job — Calls, Compliance, Knowledge base, Billing — and
 * the screens that make up that job sit a tab apart from each other rather
 * than each taking a row in the navigation. The routes are unchanged: every
 * deep link and bookmark still lands where it did.
 *
 * This file is now only the lists. The strip itself is `PageTabs`, rendered by
 * `PageHeader` beneath the page title, because there were two tab strips in
 * this product that looked and sat differently: this one drew its active tab
 * in `--accent-brand` and hung *above* the `<h1>`, while `PageHeader`'s drew it
 * in `--primary` and hung below. Whether the title came before or after the
 * tabs depended on which screen you had clicked, which is the kind of thing a
 * reader feels without being able to name.
 *
 * `prefix: true` on every entry: a detail page keeps its tab lit rather than
 * dropping the reader out of the section they are standing in.
 */

import type { PageTab } from "./PageHeader";

export type SectionTab = PageTab;

/** Everything about what the calls did, as one strip.
 *
 *  These were three sidebar entries -- Review, Calls, Analytics -- and two
 *  tab strips, for one subject. Somebody looking for "how did yesterday go"
 *  had to guess which of the three was the one.
 *
 *  Now one entry and four tabs, in the order the questions get asked: which
 *  calls happened, which never did, which need a human, and what is the
 *  shape of all of them. Missed calls arrived here from Phone numbers,
 *  where it was filed with the screens for buying one; it is the only
 *  record of a caller we refused, so it belongs beside the calls we took.
 *  Two entries left: Spend went to Billing (a money question), and
 *  Daily reports is linked from the foot of Analytics, because it is one
 *  day in detail -- something you want after the shape tells you which day,
 *  not a peer of it. Five tabs meant guessing again, one level down. */
export const CALLS_TABS: PageTab[] = [
  { href: "/usage", label: "Calls", prefix: true },
  // The calls that are missing from the list beside it. A callback we
  // declined -- cooldown, daily cap, closed window -- produces no call at
  // all, so a quiet Calls tab and a tab silently refusing every caller look
  // identical. It was filed under Phone numbers, which is where you go to
  // buy one, not to find out why nobody is getting through.
  { href: "/missed-calls", label: "Missed calls", prefix: true },
  { href: "/review", label: "Review", prefix: true },
  // Prefix now: /analytics/spend has moved to Billing, so nothing under
  // /analytics belongs to another tab.
  { href: "/analytics", label: "Analytics", prefix: true },
];

/** Working bots and archived ones. Archived used to be a collapsed
 *  section at the foot of a long page, which is indistinguishable from
 *  not existing: people archived a bot and could not find it again. */
export const BOTS_TABS: PageTab[] = [
  { href: "/workflow", label: "Bots" },
  { href: "/workflow/archived", label: "Archived" },
];

export const KNOWLEDGE_TABS: PageTab[] = [
  { href: "/files", label: "Documents", prefix: true },
  { href: "/recordings", label: "Audio clips", prefix: true },
];

export const COMPLIANCE_TABS: PageTab[] = [
  { href: "/privacy", label: "Privacy", prefix: true },
  { href: "/do-not-call", label: "Do not call", prefix: true },
];

export const BILLING_TABS: PageTab[] = [
  { href: "/billing", label: "Billing", prefix: true },
  // Spend used to sit in the call strip, beside Calls and Review. But
  // "what did this cost" is a money question, and the person asking it is
  // already on Billing looking at the balance -- they were being sent to a
  // tab filed under the phone.
  { href: "/analytics/spend", label: "Spend", prefix: true },
  { href: "/partner", label: "Partner programme", prefix: true },
];

/**
 * Everything to do with phone numbers.
 *
 * Carriers, verification, test numbers and buying a number were four separate
 * entries in the left navigation — a quarter of it — for a set of screens that
 * are one job done in sequence: get verified, then buy a number, then point it
 * at an agent. Presented as peers they read as four unrelated features, and
 * the order they have to be done in was visible only to somebody who already
 * knew it.
 *
 * Ordered the way the work happens, not alphabetically. A customer arriving
 * for the first time reads this left to right and gets the sequence.
 *
 * Missed calls used to end this strip and has moved to Calls. It is not a
 * step in getting set up -- it is the record of callers a working number
 * refused -- and reading it as the fifth thing to do before you can take
 * calls was exactly backwards.
 */
export const TELEPHONY_TABS: PageTab[] = [
  { href: "/verification", label: "Verification", prefix: true },
  { href: "/numbers", label: "Get a number", prefix: true },
  { href: "/telephony-configurations", label: "Carriers & numbers", prefix: true },
  { href: "/verified-numbers", label: "Test numbers", prefix: true },
];

/** The two developer screens, a tab apart. They were two sidebar rows
 *  under one heading and reached each other only through a button in the
 *  corner of one of them; somebody with a key in hand looking for where to
 *  point a webhook had to go back to the sidebar to find out. */
export const DEVELOPER_TABS: PageTab[] = [
  { href: "/api-keys", label: "API keys & SDKs", prefix: true },
  { href: "/deploy/connect", label: "API & webhooks", prefix: true },
];

/** The desk: the diary, the in-tray and the contact book.
 *
 *  Tasks and Requests were two tabs of Decibyl, beside its thread. But the
 *  assistant is a colleague you talk to, not a container for the workspace's
 *  screens, and Tasks was a third way to reach a door the sidebar already
 *  pins.
 *
 *  Contacts joins them, from Setup. It sat there beside Campaigns because a
 *  campaign dials a list -- but that is delivery, and a contact is a person
 *  you deal with, which is desk work. The page's own words already said so:
 *  "everything you know about them is loaded before the bot speaks".
 *
 *  Schedules first: a routine fires whether or not anybody is watching, a
 *  request waits for somebody, and the contact book is looked up rather than
 *  worked through. */
export const DESK_TABS: PageTab[] = [
  { href: "/tasks", label: "Tasks", prefix: true },
  { href: "/requests", label: "Requests", prefix: true },
  { href: "/contacts", label: "Contacts", prefix: true },
];

/** The shop, as one screen with departments across the top.
 *
 *  These were four sidebar rows -- Tools, Skills, Bots, Integrations -- for
 *  one shop, with nothing tying them together, so they read as four unrelated
 *  features and one of them shared a word with a Setup row that means
 *  something else entirely (the shop's Tools, against the account's own Your
 *  tools). Buzz's own directory is one surface with its tabs across the top.
 *
 *  Bots first: it is what somebody comes to a shop of bots for, and it is the
 *  screen the one remaining row lands on. Then what a bot can do, how it can
 *  be taught to do it, and the systems it reaches. */
export const MARKETPLACE_TABS: PageTab[] = [
  { href: "/marketplace", label: "Bots" },
  { href: "/marketplace/tools", label: "Tools" },
  { href: "/marketplace/skills", label: "Skills" },
  { href: "/marketplace/integrations", label: "Integrations" },
];
