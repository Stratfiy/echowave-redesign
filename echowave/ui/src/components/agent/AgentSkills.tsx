"use client";

/**
 * An agent's skills, on its side panel: what it has been taught, a cross to
 * take one off, and "Add skill" -- from the list, or described in your own
 * words (POST /skills/own), the way the founder asked for both.
 */

import { Plus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  installSkillApiV1SkillsInstallPost,
  listSkillsApiV1SkillsGet,
  setSkillBotsApiV1SkillsBotsPost,
  skillsOnWorkflowApiV1SkillsOnWorkflowIdGet,
  takeSkillOffApiV1SkillsOffPost,
  writeOwnSkillApiV1SkillsOwnPost,
} from "@/client/sdk.gen";
import type { SkillCard } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/** How many skills the list offers before "describe your own". */
const OFFER = 8;

export function AgentSkills({ workflowId, agentName }: { workflowId: number; agentName: string }) {
  const { user, loading: authLoading } = useAuth();
  const [onAgent, setOnAgent] = useState<SkillCard[] | null>(null);
  const [installed, setInstalled] = useState<SkillCard[]>([]);
  const [catalogue, setCatalogue] = useState<SkillCard[]>([]);
  const [describing, setDescribing] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const started = useRef(false);

  const load = useCallback(async () => {
    const [mine, shelf] = await Promise.all([
      skillsOnWorkflowApiV1SkillsOnWorkflowIdGet({ path: { workflow_id: workflowId } }),
      listSkillsApiV1SkillsGet(),
    ]);
    setOnAgent(mine.data?.skills ?? []);
    setInstalled(shelf.data?.installed ?? []);
    setCatalogue(shelf.data?.skills ?? []);
  }, [workflowId]);

  useEffect(() => {
    if (authLoading || !user || started.current) return;
    started.current = true;
    void load();
  }, [authLoading, user, load]);

  const have = new Set((onAgent ?? []).map((s) => s.slug));
  const installedSlugs = new Set(installed.map((s) => s.slug));
  const offers = [...installed, ...catalogue.filter((s) => !installedSlugs.has(s.slug))]
    .filter((s) => !have.has(s.slug))
    .slice(0, OFFER);

  const add = async (skill: SkillCard) => {
    if (!installedSlugs.has(skill.slug)) {
      const installedRes = await installSkillApiV1SkillsInstallPost({ body: { slug: skill.slug } });
      if (installedRes.error) {
        toast.error(detailFromError(installedRes.error, "Could not add that skill"));
        return;
      }
    }
    const current = installed.find((s) => s.slug === skill.slug)?.on_bots?.map((b) => b.id) ?? [];
    const res = await setSkillBotsApiV1SkillsBotsPost({
      body: { slug: skill.slug, workflow_ids: [...new Set([...current, workflowId])] },
    });
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not add that skill"));
      return;
    }
    toast.success(`${agentName} can ${skill.title.toLowerCase()} now`);
    void load();
  };

  const remove = async (skill: SkillCard) => {
    const res = await takeSkillOffApiV1SkillsOffPost({ body: { slug: skill.slug, workflow_id: workflowId } });
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not take that skill off"));
      return;
    }
    void load();
  };

  const writeOwn = async () => {
    setSaving(true);
    const res = await writeOwnSkillApiV1SkillsOwnPost({
      body: { workflow_id: workflowId, title: title.trim(), description: description.trim() },
    });
    setSaving(false);
    if (res.error) {
      toast.error(detailFromError(res.error, "Could not save that skill"));
      return;
    }
    setDescribing(false);
    setTitle("");
    setDescription("");
    toast.success(`${agentName} learned “${title.trim()}”`);
    void load();
  };

  return (
    <section aria-labelledby="agent-skills" className="space-y-2" data-testid="agent-skills">
      <div className="flex items-baseline justify-between">
        <h3 id="agent-skills" className="text-[13px] font-normal text-muted-foreground">
          Skills
        </h3>
        {onAgent && onAgent.length > 0 && <span className="text-xs text-muted-foreground">{onAgent.length} on</span>}
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {(onAgent ?? []).map((skill) => (
          <li key={skill.slug}>
            <span
              className="inline-flex h-[30px] items-center gap-1 rounded-full border border-[var(--line)] bg-[var(--v2-card)] pl-3 pr-1 text-[13px]"
              title={skill.description}
            >
              {skill.title}
              <button
                type="button"
                aria-label={`Take ${skill.title} off ${agentName}`}
                onClick={() => void remove(skill)}
                className="grid h-6 w-6 place-items-center rounded-full text-muted-foreground hover:bg-[var(--line)] hover:text-foreground"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          </li>
        ))}
        <li>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="inline-flex h-[30px] items-center gap-1 rounded-full border border-dashed border-black/20 px-3 text-[13px] text-muted-foreground hover:bg-[var(--line)] dark:border-white/20"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add skill
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="max-h-80 w-64 overflow-y-auto">
              {offers.length > 0 && (
                <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">From the list</DropdownMenuLabel>
              )}
              {offers.map((skill) => (
                <DropdownMenuItem key={skill.slug} onClick={() => void add(skill)} className="flex-col items-start gap-0">
                  <span>{skill.title}</span>
                  <span className="line-clamp-1 text-xs text-muted-foreground">{skill.description}</span>
                </DropdownMenuItem>
              ))}
              {offers.length > 0 && <DropdownMenuSeparator />}
              <DropdownMenuItem onClick={() => setDescribing(true)}>Describe your own…</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </li>
      </ul>

      <Dialog open={describing} onOpenChange={setDescribing}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Teach {agentName} a skill</DialogTitle>
            <DialogDescription>Say what it is and when to use it, in your own words.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-sm">
              <span className="text-muted-foreground">Name</span>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Check stock" maxLength={200} />
            </label>
            <label className="block space-y-1.5 text-sm">
              <span className="text-muted-foreground">What it should do</span>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                maxLength={4000}
                placeholder="Before quoting a price, look the item up in our stock sheet and say if it is out of stock."
              />
            </label>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDescribing(false)}>
              Cancel
            </Button>
            <Button onClick={() => void writeOwn()} disabled={saving || !title.trim() || !description.trim()}>
              {saving ? "Saving…" : "Teach it"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
