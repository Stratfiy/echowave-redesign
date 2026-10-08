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
  // Prefix: everything under /analytics is this tab, including the day view
  // (?date=), which was /reports.
  { href: "/analytics", label: "Analytics", prefix: true },
  // The same work added up: runs, tokens and cost per agent and per model.
  { href: "/activity/usage", label: "Usage", prefix: true },
];

export const KNOWLEDGE_TABS: PageTab[] = [
  { href: "/settings/knowledge", label: "Files", prefix: true },
  { href: "/recordings", label: "Audio clips", prefix: true },
];

export const COMPLIANCE_TABS: PageTab[] = [
  { href: "/settings/compliance", label: "Privacy", prefix: true },
  { href: "/do-not-call", label: "Do not call", prefix: true },
];

export const BILLING_TABS: PageTab[] = [
  { href: "/billing", label: "Billing", prefix: true },
  // Spend used to sit in the call strip, beside Calls and Review. But
  // "what did this cost" is a money question, and the person asking it is
  // already on Billing looking at the balance -- they were being sent to a
  // tab filed under the phone.
  { href: "/billing/spend", label: "Spend", prefix: true },
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
  // Verification is not a fourth thing to do: it is step 1 of getting a
  // number, and its form opens in place inside Get a number (UI-0 folded
  // /verification in), so somebody sees what the paperwork is for.
  { href: "/numbers", label: "Get a number", prefix: true },
  // Named for what it holds, which is what the page has always been titled:
  // "Carriers & numbers" on the tab against "Phone numbers" on the screen
  // meant the strip and the heading disagreed about where you were.
  { href: "/settings/phone-number", label: "Your numbers", prefix: true },
  { href: "/verified-numbers", label: "Test numbers", prefix: true },
];

/** The two developer screens, a tab apart. They were two sidebar rows
 *  under one heading and reached each other only through a button in the
 *  corner of one of them; somebody with a key in hand looking for where to
 *  point a webhook had to go back to the sidebar to find out. */
export const DEVELOPER_TABS: PageTab[] = [
  // One name per screen, the same one the sidebar and the page use. These
  // read "API keys & SDKs" and "API & webhooks" while the second page called
  // itself Connect, so the row, the tab and the heading were three names for
  // two things.
  { href: "/settings/developer", label: "API keys", prefix: true },
  { href: "/deploy/connect", label: "Connect", prefix: true },
];

/** The desk: the work, the clock, the contact book and what was handed over.
 *
 *  Tasks is the board people and agents hand work on (the full board with
 *  TB-1, the simpler one until then); Requests was a second door to the same
 *  tasks and now redirects here (UI-0). Schedules are the routines, which
 *  fire whether or not anybody is watching. One list for every desk page, so
 *  the strip reads the same from each of them.
 *
 *  Contacts came from Setup: a contact is a person you deal with, which is
 *  desk work, not delivery. */
export const DESK_TABS: PageTab[] = [
  { href: "/tasks", label: "Today", prefix: true },
  { href: "/schedules", label: "Routines", prefix: true },
  // Activity sits under Today (product handoff, section 19): what happened,
  // beside what is due. Its own tabs -- calls, missed, review, analytics,
  // usage -- open from here.
  { href: "/usage", label: "Activity", prefix: true },
  { href: "/contacts", label: "Contacts", prefix: true },
  // What the bots handed over. The timeline has marked these rows since it
  // was built and the route has taken `deliverables_only` for as long;
  // nothing ever asked for it, so the only way to find what a bot produced
  // was to scroll its thread past every message it also wrote.
  { href: "/deliverables", label: "Handed over", prefix: true },
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
  { href: "/marketplace", label: "Agents" },
  { href: "/marketplace/tools", label: "Tools" },
  { href: "/marketplace/skills", label: "Skills" },
  { href: "/marketplace/integrations", label: "Integrations" },
];
