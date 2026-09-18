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
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

export default function OverviewPage() {
  const { user } = useAuth();
  const firstName = user?.displayName?.split(" ")[0];
  const [aboutOpen, setAboutOpen] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* One slim line, the way Buzz heads a channel: the name, and the
          door to what this one is. The full title band cost a quarter of the
          screen above a conversation, and said what the reader could already
          see -- the greeting inside the thread says what actually happened,
          which is the sentence worth the space. */}
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border/70 px-4 py-2 sm:px-6">
        <h1 className="truncate text-[15px] font-semibold text-foreground">Decibyl</h1>
        <Button
          size="sm"
          variant={aboutOpen ? "secondary" : "ghost"}
          aria-pressed={aboutOpen}
          onClick={() => setAboutOpen((open) => !open)}
        >
          <Info className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          About
        </Button>
      </div>
      <div className="flex min-h-0 flex-1">
        {/* Edge to edge, and no scroll of its own: the thread inside does
            the scrolling, and the composer stays on the bottom edge where
            somebody's hands already are. The rows keep their own gutter, so
            a wide screen gives the conversation the width rather than a
            column of grey either side of it. */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* What happened, in sentences: the greeting, the composer,
              chips built from this account's own state, and the team. */}
          <HomeAboveTheFold firstName={firstName} />
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
