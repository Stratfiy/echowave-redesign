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

export const CALLS_TABS: PageTab[] = [
  { href: "/usage", label: "Calls", prefix: true },
  { href: "/reports", label: "Daily reports", prefix: true },
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
 */
export const TELEPHONY_TABS: PageTab[] = [
  { href: "/verification", label: "Verification", prefix: true },
  { href: "/numbers", label: "Get a number", prefix: true },
  { href: "/telephony-configurations", label: "Carriers & numbers", prefix: true },
  { href: "/verified-numbers", label: "Test numbers", prefix: true },
  { href: "/missed-calls", label: "Missed calls", prefix: true },
];
