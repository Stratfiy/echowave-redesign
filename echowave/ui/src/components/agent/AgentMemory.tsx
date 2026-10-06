"use client";

/**
 * An agent's memory, on its side panel: what it knows (the business's and
 * its own, MemoryList), and a box to teach it something that is true of
 * this agent alone -- written with the agent's id, so the bot on the phone
 * does not start saying it too.
 */

import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { writeFactsApiV1OrganisationMemoryFactsPost } from "@/client/sdk.gen";
import { MemoryList } from "@/components/memory/MemoryList";
import { detailFromError } from "@/lib/apiError";

/** A short, stable key for a sentence somebody typed: its first words. */
export function memoryKey(text: string): string {
  const words = text
    .toLowerCase()
    .replace(/[^a-z0-9\s]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 6)
    .join("_");
  return words || "note";
}

export function AgentMemory({ workflowId, agentName }: { workflowId: number; agentName: string }) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  // Bumped after a write so the list reads again; it fetches once per mount.
  const [version, setVersion] = useState(0);

  const teach = async (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    setSaving(true);
    const res = await writeFactsApiV1OrganisationMemoryFactsPost({
      body: { facts: { [memoryKey(value)]: value }, workflow_id: workflowId },
    });
    setSaving(false);
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not save that"));
      return;
    }
    setText("");
    setVersion((v) => v + 1);
    toast.success(`${agentName} will remember that`);
  };

  return (
    <section aria-labelledby="agent-memory" className="space-y-2" data-testid="agent-memory">
      <h3 id="agent-memory" className="text-[13px] font-normal text-muted-foreground">
        Memory
      </h3>
      <MemoryList key={version} workflowId={workflowId} botName={agentName} />
      <form onSubmit={teach} className="flex gap-2">
        <label className="sr-only" htmlFor={`teach-${workflowId}`}>
          Teach {agentName} something
        </label>
        <input
          id={`teach-${workflowId}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={`Teach ${agentName} something to remember`}
          maxLength={500}
          className="h-9 min-w-0 flex-1 rounded-[10px] border border-[var(--line)] bg-[var(--v2-card)] px-3 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="submit"
          disabled={saving || !text.trim()}
          className="h-9 rounded-full bg-primary px-3.5 text-[13px] text-primary-foreground disabled:opacity-40"
        >
          Save
        </button>
      </form>
    </section>
  );
}
