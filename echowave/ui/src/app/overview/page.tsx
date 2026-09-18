"use client";

/**
 * Decibyl: the thread, and nothing above it but its own name.
 *
 * It used to carry a five-tab strip -- Messages, Tasks, Requests, Memory,
 * About -- and four separate screens were titled "Decibyl" beneath it. Two of
 * those tabs were doors the sidebar already holds, and the strip on About was
 * a second, differently-written copy that offered History where this one
 * offered Tasks. Which strip a reader saw depended on which tab they had
 * clicked, which is the kind of thing somebody feels without being able to
 * name it.
 *
 * Buzz heads a room with its name and gives the rest to the messages; what the
 * room is sits in the panel beside it. So: Tasks and Requests are one section
 * in the sidebar, About opens on the right the way a bot's does, and the
 * memory graph is a link inside About rather than a tab of its own.
 */

import { Info } from "lucide-react";
import { useState } from "react";

import { DecibylAbout } from "@/components/home/DecibylAbout";
import { HomeAboveTheFold } from "@/components/home/HomeAboveTheFold";
import { AuxiliaryPanel } from "@/components/layout/AuxiliaryPanel";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export default function OverviewPage() {
  const { user } = useAuth();
  const firstName = user?.displayName?.split(" ")[0];
  const [aboutOpen, setAboutOpen] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="Decibyl"
        // The greeting moved into the body, where it can say what
        // actually happened rather than what the page contains.
        description="Your team's assistant. Ask what happened, or build a new bot."
        actions={
          // The same control a bot's thread carries, in the same place: who
          // this one is, beside the conversation rather than a tab away.
          <Button
            size="sm"
            variant={aboutOpen ? "secondary" : "outline"}
            aria-pressed={aboutOpen}
            onClick={() => setAboutOpen((open) => !open)}
          >
            <Info className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            About
          </Button>
        }
      />
      <div className="flex min-h-0 flex-1">
        {/* The one screen that keeps a reading-width column inside the body.
            Everything below is a chat composer and two prose cards; run
            full-bleed at 1440 the input alone would be over a metre of
            line, which is worse than the gutter the shell exists to remove. */}
        <div className="min-w-0 flex-1 overflow-y-auto">
          <PageBody className="space-y-6">
            {/* What happened, in sentences: the greeting, the composer,
                chips built from this account's own state, and the team. */}
            <HomeAboveTheFold firstName={firstName} />
          </PageBody>
        </div>
        {aboutOpen && (
          <AuxiliaryPanel label="About Decibyl" onClose={() => setAboutOpen(false)}>
            <DecibylAbout />
          </AuxiliaryPanel>
        )}
      </div>
    </div>
  );
}
