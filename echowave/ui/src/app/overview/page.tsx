"use client";

/**
 * The first screen after signing in, and the only one whose job is to get
 * somebody to a working agent rather than to show them something.
 *
 * It used to open with "Open source alternative to Vapi — help us support the
 * project by giving us a star on GitHub", gated on `provider !== 'stack'`.
 * That condition is true for every local-auth deployment, which is what
 * production runs — so a paying customer's first impression was a request for
 * a GitHub star on a repository they have no relationship with, above a
 * comparison to a competitor. Both are gone. A customer is not a contributor.
 *
 * What replaced them is the order the work actually happens in: describe the
 * business, hear it on a call, put it on a number. The chat panel stays at the
 * top because it is the shortest path to a working agent, and the cards below
 * it are the next two steps rather than a directory of subsystems.
 */

import { HomeAboveTheFold } from "@/components/home/HomeAboveTheFold";
import { HOME_TABS } from "@/components/home/tabs";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useAuth } from "@/lib/auth";

export default function OverviewPage() {
  const { user } = useAuth();
  const firstName = user?.displayName?.split(" ")[0];

  return (
    <>
      <PageHeader
        title="Decibyl"
        // The greeting moved into the body, where it can say what
        // actually happened rather than what the page contains.
        description="Your team's assistant. Ask what happened, or build a new bot."
        tabs={HOME_TABS}
      />
      {/* The one screen that keeps a reading-width column inside the body.
                Everything below is a chat composer and two prose cards; run
                full-bleed at 1440 the input alone would be over a metre of
                line, which is worse than the gutter the shell exists to remove. */}
      <PageBody className="space-y-6">
        {/* What happened, in sentences: the greeting, the composer,
                    chips built from this account's own state, and the team. */}
        <HomeAboveTheFold firstName={firstName} />
        {/* The free-credit steps and the referral link moved to the gift
            menu beside the credits chip: they are about the account, not
            the conversation, and here they pushed the composer up the
            screen and sat under it as two cards of chores. */}
        {/* No charts here. Home is the conversation: a greeting, the
            thread, the composer. How it is trending is the Analytics and
            Spend tabs of Calls, which is where somebody goes to read a
            chart -- and this screen no longer pays for a charting library
            to put two of them under a chat nobody scrolls past. */}
      </PageBody>
    </>
  );
}
