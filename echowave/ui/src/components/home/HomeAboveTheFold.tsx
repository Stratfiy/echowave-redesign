"use client";

/**
 * Home is a conversation with Decibyl.
 *
 * The way a Slack workspace opens on Slackbot: a hello, the thread, and a
 * composer. Decibyl is the workspace's own assistant -- it knows the team's
 * numbers, the company's documents, what the business has confirmed, and
 * what every bot did lately (see services/workflow/decibyl.py). The two
 * openers are the questions an owner arrives with, sent as messages so the
 * answer comes from the same brain as everything else typed here.
 *
 * The chips under the hello are built from the account's own state: a
 * failing connector, a paused bot, a missed call to return. A link chip
 * opens the screen that fixes it; a prompt chip opens the shelf.
 */

import { AlertTriangle, ArrowRight } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getWorkflowsApiV1WorkflowFetchGet,
  postMessageApiV1TimelineMessagePost,
  teamHomeApiV1TeamHomeGet,
} from "@/client/sdk.gen";
import type { Headline, Opener, Suggestion } from "@/client/types.gen";
import { ArtImage } from "@/components/art/Art3D";
import { type ChannelBot, ChannelComposer } from "@/components/channel/ChannelComposer";
import { ChannelStream } from "@/components/channel/ChannelStream";
import { ThreadList } from "@/components/home/ThreadList";
import { jobArt } from "@/lib/art";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** The fallback when the server sends no cards of its own: the two
 *  questions an owner arrives with. The server's cards (`openers` on the
 *  home response, see api/services/workflow/home_openers.py) come from
 *  this account's own life -- what they asked last, their busiest bot,
 *  callers nobody rang back -- and replace these when present. */
export const OPENERS = [
  "What happened this week?",
  "What needs my attention today?",
] as const;

/** What a brand-new account is asked instead: the first job, as things
 *  you would say to a colleague. Each one is a message to Decibyl, which
 *  knows the templates and starts the bot. */
export const FIRST_JOBS = [
  "Answer my phone and book appointments",
  "Reply to customers on WhatsApp",
  "Chase overdue payments",
  "Answer staff questions from our documents",
  "Send me a summary every morning",
] as const;

/** Built from the reader's own clock. The server's is in a data centre, and
 *  half the accounts would be wished good morning at nine in the evening. */
function partOfDay(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/**
 * What the team did, as a sentence rather than a row of tiles.
 *
 * ``span`` comes from the server rather than being written here. This
 * sentence used to say "today" over a window that was really the last 24
 * hours, so it disagreed with the operator's own idea of today and with
 * Decibyl, which had the same bug -- the same person was given two
 * different totals for "today" minutes apart.
 */
function summarise(headline: Headline, span: string): string {
  if (headline.agents === 0) return "Let's put your first agent to work.";
  const parts: string[] = [];
  if (headline.calls > 0) {
    parts.push(
      `${headline.calls} ${headline.calls === 1 ? "call" : "calls"} ${span}`,
    );
    if (headline.answered > 0) parts.push(`${headline.answered} answered`);
    if (headline.outcomes > 0) parts.push(`${headline.outcomes} finished`);
  }
  if (parts.length === 0) {
    return headline.live === 0
      ? "No agent is taking calls right now."
      : `Nothing has come in ${span === "today" ? "yet today" : span}.`;
  }
  const sentence = `${parts.join(", ")}.`;
  if (headline.needs_attention > 0) {
    return `${sentence} ${headline.needs_attention} ${
      headline.needs_attention === 1 ? "agent needs" : "agents need"
    } you.`;
  }
  return sentence;
}

function Chip({ chip }: { chip: Suggestion }) {
  const className =
    "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
  if (chip.action === "link" && chip.href) {
    return (
      <Link href={chip.href} className={className}>
        <AlertTriangle className="h-3 w-3" />
        {chip.text}
        <ArrowRight className="h-3 w-3" />
      </Link>
    );
  }
  return (
    <Link href="/marketplace" className={className}>
      {chip.text}
      <ArrowRight className="h-3 w-3" />
    </Link>
  );
}

export function HomeAboveTheFold({ firstName }: { firstName?: string }) {
  const { user, loading: authLoading } = useAuth();
  const [headline, setHeadline] = useState<Headline | null>(null);
  // What the headline's counts are over, in the server's words. Not written
  // here: this sentence said "today" over a rolling 24-hour window.
  const [span, setSpan] = useState("today");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  // The server's question cards, built from this account's own history.
  const [openers, setOpeners] = useState<Opener[]>([]);
  const [waitingFor, setWaitingFor] = useState<{
    since: string;
    bots: number[];
  } | null>(null);
  const [sendingOpener, setSendingOpener] = useState<string | null>(null);
  // How many rows the thread holds. Until it is known, nothing is drawn
  // above the thread: a greeting that appears and then jumps away as the
  // rows load reads as a glitch. Once there is a conversation the greeting
  // steps aside, the way every chat product's empty-state does.
  const [rows, setRows] = useState<number | null>(null);
  // The roster for `@`: every bot on the account. Decibyl's thread had
  // none, so the @ button opened nothing.
  const [bots, setBots] = useState<ChannelBot[]>([]);
  // "?say=" on the URL: words for the box, from "Describe your bot" at the
  // door. Read once and taken off the address, so a refresh does not put
  // them back after the person has sent or deleted them.
  const [prefill, setPrefill] = useState<string>("");
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const say = params.get("say");
      if (!say) return;
      setPrefill(say);
      params.delete("say");
      const rest = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${rest ? `?${rest}` : ""}`);
    } catch {
      // No URL to read: the box opens empty, as it always did.
    }
  }, []);
  // Which of Decibyl's conversations. Null is the one the account has
  // always had. Read from "?thread=" so a refresh or a shared link opens
  // the same chat, and written back on every switch for the same reason.
  const [threadId, setThreadId] = useState<string | null>(() => {
    try {
      return new URLSearchParams(window.location.search).get("thread");
    } catch {
      return null;
    }
  });
  // Bumped after every send: a new chat is not in the list until its first
  // line exists, and the list is how the person finds their way back.
  const [threadsVersion, setThreadsVersion] = useState(0);
  const switchThread = useCallback((next: string | null) => {
    setThreadId(next);
    // The count belongs to the chat that was on screen. Until the new one
    // reports, nothing is drawn above it -- the same rule as first load.
    setRows(null);
    setWaitingFor(null);
    try {
      const params = new URLSearchParams(window.location.search);
      if (next) params.set("thread", next);
      else params.delete("thread");
      const rest = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${rest ? `?${rest}` : ""}`);
    } catch {
      // No URL to write: the switch still happens on screen.
    }
  }, []);
  const newThread = useCallback(() => switchThread(crypto.randomUUID()), [switchThread]);
  const onCountChange = useCallback((count: number) => setRows(count), []);
  const refreshStream = useRef<() => void>(() => {});
  const registerRefresh = useCallback((refresh: () => void) => {
    refreshStream.current = refresh;
  }, []);

  const greeting = useMemo(() => partOfDay(new Date()), []);
  // No bot yet: the door has just closed behind them. The two questions
  // about what happened have no answer, so the cards are the first job.
  const brandNew = headline !== null && headline.agents === 0;
  const empty = rows === 0;

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await teamHomeApiV1TeamHomeGet({
          query: { hours: 24 },
        });
        if (cancelled || response.error || !response.data) return;
        setHeadline(response.data.headline ?? null);
        setSpan(response.data.span ?? "today");
        setSuggestions(response.data.suggestions ?? []);
        setOpeners(response.data.openers ?? []);
      } catch {
        // The thread below still works. A greeting that failed to
        // load is a missing sentence, not a broken screen.
      }
    })();
    (async () => {
      const response = await getWorkflowsApiV1WorkflowFetchGet();
      if (cancelled || response.error || !response.data) return;
      setBots(
        response.data.map((w) => ({ id: w.id, name: w.name, handle: w.handle ?? null })),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  // Decibyl has no workflow id; the thinking row is keyed on 0 and the
  // stream, in assistant mode, clears it on any reply after `since`.
  const asked = () =>
    setWaitingFor({ since: new Date().toISOString(), bots: [0] });

  const sendOpener = async (text: string) => {
    setSendingOpener(text);
    const response = await postMessageApiV1TimelineMessagePost({
      body: { assistant: true, thread_id: threadId, text },
    });
    setSendingOpener(null);
    if (response.error) return;
    asked();
    setThreadsVersion((v) => v + 1);
    refreshStream.current();
  };

  return (
    // Fills what the shell gives it, rather than guessing the header's
    // height in `vh` -- the guess was wrong the moment the header changed.
    //
    // Empty, it is Grok's first screen: the hello, the box right under it,
    // and the first questions as cards beneath -- one column in the middle
    // of the page. Talking, it is a reading column with the box docked at
    // the bottom. The stream and the box keep their places in the tree in
    // both, so nothing remounts when the first reply lands.
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div
        className={cn(
          "mx-auto flex min-h-0 w-full flex-1 flex-col",
          empty ? "max-w-2xl justify-center overflow-y-auto py-6" : "max-w-4xl",
        )}
      >
      {empty && (
      <div className="flex shrink-0 flex-col items-center px-2 pb-6 text-center">
        {/* The real mark, on a round tile with a soft grey halo: the
            greeting's face. */}
        <div
          aria-hidden="true"
          data-testid="decibyl-mark"
          className="mt-1.5 flex h-[72px] w-[72px] items-center justify-center rounded-full border border-border bg-card shadow-[0_0_0_6px_var(--muted),0_10px_30px_-8px_rgba(0,0,0,0.25)]"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/decibyl-mark.svg" alt="" width={56} height={56} className="h-14 w-14 dark:invert" />
        </div>
        <h2 className="mt-5 text-3xl font-semibold tracking-tight sm:text-4xl">
          Hi, I&apos;m Decibyl!
        </h2>
        <p className="mt-2 max-w-lg text-[15px] text-muted-foreground">
          {greeting}
          {firstName ? `, ${firstName}` : ""}.{" "}
          {brandNew
            ? "Say hi, or tell me one thing you'd love off your plate this week. I'll set up an agent for it and you can hear it in a minute."
            : `${headline ? summarise(headline, span) : ""} I know your agents, your numbers and your company's documents.`}
        </p>
      </div>
      )}
      {rows !== null && rows > 0 && (
        <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
          <span
            aria-hidden="true"
            className="flex h-6 w-6 items-center justify-center rounded-md bg-rail text-xs font-semibold text-rail-foreground"
          >
            d
          </span>
          <span className="font-medium text-foreground">Decibyl</span>
          <span>· {greeting}{firstName ? `, ${firstName}` : ""}.{" "}{headline ? summarise(headline, span) : ""}</span>
        </div>
      )}

      {/* No card around the room. Buzz gives the whole pane to the
          messages; a bordered, tinted box inside a bordered, tinted page was
          three surfaces deep before a word of the conversation. */}
      <div
        className={cn(
          "flex min-h-0 flex-col overflow-hidden",
          // Empty: the stream stays mounted (it reports the count) but is
          // not drawn -- "This channel is quiet" under a greeting that says
          // hello is the same thing said twice.
          empty ? "hidden" : "flex-1",
        )}
      >
        {/* Keyed on the chat, so switching remounts the stream clean:
            no rows from the last chat showing until the poll catches up,
            no cursor pointing into a different conversation. */}
        <ChannelStream
          key={threadId ?? "original"}
          assistant
          assistantName="Decibyl"
          threadId={threadId}
          botNames={{}}
          onRegisterRefresh={registerRefresh}
          onCountChange={onCountChange}
          waitingFor={waitingFor}
        />
      </div>
      <ChannelComposer
        hero={empty}
        assistant
        threadId={threadId}
        bots={bots}
        initialText={prefill || undefined}
        channelName="Decibyl"
        onSent={() => {
          asked();
          setThreadsVersion((v) => v + 1);
          refreshStream.current();
        }}
      />
      {empty && (
        <div className="flex flex-col items-center">
        <div
          className="mt-4 grid w-full gap-2 sm:grid-cols-2"
          aria-label="Ask Decibyl"
        >
          {(openers.length > 0
            ? openers
            : (brandNew ? FIRST_JOBS : OPENERS).map((text, index) => ({
                kind: brandNew || index === 0 ? "time" : "attention",
                text,
              }))
          ).map(({ text }) => {
            return (
              <button
                key={text}
                type="button"
                disabled={sendingOpener !== null}
                onClick={() => void sendOpener(text)}
                className="group flex w-full items-center gap-3 rounded-2xl border border-border bg-card/70 px-3.5 py-3 text-left text-sm font-medium transition-colors hover:border-[var(--accent-brand)]/40 hover:bg-card disabled:opacity-60"
              >
                <ArtImage name={jobArt(text, "sphere")} size={28} />
                <span className="line-clamp-2 min-w-0 flex-1">
                  {sendingOpener === text ? "Asking…" : text}
                </span>
                <span
                  aria-hidden="true"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-all group-hover:translate-x-0.5 group-hover:bg-[var(--accent-brand)] group-hover:text-white"
                >
                  <ArrowRight className="h-3.5 w-3.5" />
                </span>
              </button>
            );
          })}
        </div>
        {suggestions.length > 0 ? (
          <div className="mt-3 flex flex-wrap justify-center gap-2">
            {suggestions.map((chip) => (
              <Chip key={`${chip.kind}-${chip.text}`} chip={chip} />
            ))}
          </div>
        ) : null}
        </div>
      )}
      </div>
      {/* Under the composer, out of the thread's way. */}
      <ThreadList
        current={threadId}
        onPick={switchThread}
        onNew={newThread}
        refreshKey={threadsVersion}
      />
    </div>
  );
}
