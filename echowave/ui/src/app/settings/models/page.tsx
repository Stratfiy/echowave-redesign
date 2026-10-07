"use client";

import { Check, ChevronDown, KeyRound } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  chooseModelDefaultApiV1SettingsModelsInheritancePut,
  getWorkspaceModelsApiV1OrganizationsModelsGet,
  modelInheritanceApiV1SettingsModelsInheritanceGet,
  setProviderKeyApiV1ProviderKeysPut,
  setWorkspaceModelApiV1OrganizationsModelsPut,
} from "@/client/sdk.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { type AgentOverride,InheritanceAgents, InheritanceDetail, type InheritanceExtras } from "@/components/settings/ModelInheritance";
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
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

type Ours = {
  value: string;
  label: string;
  blurb: string;
  serves: string;
  // Listed before it is ready (the AWS choices): shown, never choosable.
  status?: string;
  status_note?: string;
};
type More = { value: string; label: string; vendor: string };
type Own = { vendor: string; label: string; models: string[]; has_key: boolean };
export type ModelSlot = {
  key: string;
  label: string;
  blurb: string;
  current: string;
  current_label: string;
  ours: Ours[];
  more: More[];
  own: Own[];
} & Partial<InheritanceExtras>;
type View = {
  slots: ModelSlot[];
  credential_component: Record<string, string>;
  locked?: string | null;
  /** With model_inheritance on (screen 26). */
  agents?: AgentOverride[];
  precedence?: string[];
};

/**
 * Settings -> Models: what runs every agent, chosen once for the workspace.
 * Brain, hearing, voice engine and knowledge search; each is something we
 * provide, or your own key for a vendor we do not. Agents keep only their
 * voice. This replaced the separate API keys page: a key is added right
 * where it is used.
 */
export default function ModelsPage() {
  const { user, loading: authLoading } = useAuth();
  // Screen 26: where each value comes from, whether it can run, agents'
  // overrides and revision-checked saves. Off, this page is as it was.
  const inheritance = useFeature("model_inheritance");
  const [view, setView] = useState<View | null>(null);
  const [failed, setFailed] = useState(false);
  const [ownFor, setOwnFor] = useState<{ slot: ModelSlot; own: Own } | null>(null);
  const started = useRef<string | null>(null);

  const load = useCallback(async () => {
    const res = inheritance
      ? await modelInheritanceApiV1SettingsModelsInheritanceGet()
      : await getWorkspaceModelsApiV1OrganizationsModelsGet();
    if (res.error) {
      setFailed(true);
      return;
    }
    setFailed(false);
    setView(res.data as unknown as View);
  }, [inheritance]);

  useEffect(() => {
    // Once per mode: the switch can arrive after the first load, and the
    // inheritance view is the one that carries revisions.
    const mode = inheritance ? "inheritance" : "plain";
    if (authLoading || !user || started.current === mode) return;
    started.current = mode;
    void load();
  }, [authLoading, user, load, inheritance]);

  // One save per slot at a time, and only the latest answer is drawn. Two
  // quick picks used to race: the slower response could land last and show
  // a choice the server no longer held.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const latest = useRef(0);

  const choose = async (slot: ModelSlot, value: string, label: string) => {
    if (pending[slot.key]) return false;
    const mine = ++latest.current;
    setPending((was) => ({ ...was, [slot.key]: true }));
    try {
      const res = inheritance
        ? await chooseModelDefaultApiV1SettingsModelsInheritancePut({ body: { slot: slot.key, value, revision: slot.revision ?? null } })
        : await setWorkspaceModelApiV1OrganizationsModelsPut({ body: { slot: slot.key, value } });
      if (res.error) {
        toast.error(
          res.response?.status === 409
            ? "This was changed somewhere else. Showing what runs now."
            : res.response?.status === 403
              ? "Only the workspace's admins can change its models."
              : detailFromError(res.error, "Could not change that"),
        );
        // Show what the server actually holds, not the last thing clicked.
        if (mine === latest.current) void load();
        return false;
      }
      if (mine === latest.current) setView(res.data as unknown as View);
      toast.success(`${slot.label}: ${label}, for every agent`);
      return true;
    } catch {
      toast.error("Could not reach the server. Your last saved choice is unchanged.");
      if (mine === latest.current) void load();
      return false;
    } finally {
      setPending((was) => ({ ...was, [slot.key]: false }));
    }
  };

  return (
    <>
      <PageHeader
        title="Models"
        description="What runs every agent. Choose once here; each agent only picks its own voice."
      />
      <PageBody className="max-w-3xl">
        {failed && <p className="text-sm text-muted-foreground">Could not load your models. Refresh to try again.</p>}
        {!view && !failed && (
          <div className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        )}
        {view?.locked && (
          <p className="mb-4 rounded-2xl bg-[var(--paper-2)] px-4 py-3 text-sm text-muted-foreground" data-testid="models-locked">
            {view.locked} This page shows what runs; change it in the full model editor so nothing is lost.
          </p>
        )}
        {view && (
          <ul className="divide-y divide-[var(--line)] border-y border-[var(--line)]" data-testid="model-slots">
            {view.slots.map((slot) => (
              <li key={slot.key} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="text-[15px]">{slot.label}</div>
                  <div className="text-[13px] text-muted-foreground">{slot.blurb}</div>
                  {inheritance && slot.readiness && <InheritanceDetail slot={slot as InheritanceExtras} />}
                </div>
                <SlotPicker
                  slot={slot}
                  locked={Boolean(view.locked)}
                  saving={Boolean(pending[slot.key])}
                  onChoose={(value, label) => void choose(slot, value, label)}
                  onOwn={(own) => setOwnFor({ slot, own })}
                />
              </li>
            ))}
          </ul>
        )}
        {inheritance && view?.agents && <InheritanceAgents agents={view.agents} precedence={view.precedence ?? []} />}
      </PageBody>
      {ownFor && view && (
        <OwnKeyDialog
          slot={ownFor.slot}
          own={ownFor.own}
          component={view.credential_component[ownFor.slot.key] ?? ownFor.slot.key}
          onClose={() => setOwnFor(null)}
          onSaved={async (value, label) => {
            if (await choose(ownFor.slot, value, label)) setOwnFor(null);
          }}
        />
      )}
    </>
  );
}

function SlotPicker({
  slot,
  locked,
  saving,
  onChoose,
  onOwn,
}: {
  slot: ModelSlot;
  locked: boolean;
  saving: boolean;
  onChoose: (value: string, label: string) => void;
  onOwn: (own: Own) => void;
}) {
  const ownKey = slot.current.startsWith("own:");
  if (locked) {
    return (
      <span className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full bg-[var(--paper-2)] px-3.5 text-sm text-muted-foreground">
        {ownKey && <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />}
        {slot.current_label}
      </span>
    );
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${slot.label}: ${slot.current_label}. Change`}
          disabled={saving}
          aria-busy={saving || undefined}
          className="inline-flex h-9 max-w-full shrink-0 items-center gap-1.5 rounded-full bg-[var(--paper-2)] px-3.5 text-sm transition-colors hover:bg-[var(--line)] disabled:opacity-60"
        >
          {ownKey && <KeyRound className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />}
          <span className="truncate">{saving ? "Saving…" : slot.current_label}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-[70vh] w-80 overflow-y-auto">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Included, free</DropdownMenuLabel>
        {slot.ours.map((option) => {
          const notReady = Boolean(option.status && option.status !== "available");
          return (
            <DropdownMenuItem
              key={option.value}
              disabled={notReady}
              onClick={() => onChoose(option.value, option.label)}
              className="flex items-start gap-2"
            >
              <Check
                className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${option.value === slot.current ? "" : "invisible"}`}
                aria-hidden="true"
              />
              <span className="min-w-0">
                <span className="block">
                  {option.label}
                  {notReady && <span className="ml-1.5 text-xs text-muted-foreground">· Needs setup</span>}
                </span>
                <span className="block text-xs text-muted-foreground">{option.blurb || option.serves}</span>
                {option.blurb && <span className="block text-xs text-muted-foreground">{option.serves}</span>}
                {notReady && option.status_note && (
                  <span className="block text-xs text-muted-foreground">{option.status_note}</span>
                )}
              </span>
            </DropdownMenuItem>
          );
        })}
        {slot.more.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">More models</DropdownMenuLabel>
            {slot.more.map((option) => (
              <DropdownMenuItem
                key={option.value}
                onClick={() => onChoose(option.value, option.label)}
                className="flex items-center gap-2"
              >
                <Check
                  className={`h-3.5 w-3.5 shrink-0 ${option.value === slot.current ? "" : "invisible"}`}
                  aria-hidden="true"
                />
                <span>{option.label}</span>
                <span className="ml-auto text-xs text-muted-foreground">{option.vendor}</span>
              </DropdownMenuItem>
            ))}
          </>
        )}
        {slot.own.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Your own key</DropdownMenuLabel>
            {slot.own.map((own) => (
              <DropdownMenuItem key={own.vendor} onClick={() => onOwn(own)} className="flex items-center gap-2">
                <Check
                  className={`h-3.5 w-3.5 shrink-0 ${slot.current.startsWith(`own:${own.vendor}/`) ? "" : "invisible"}`}
                  aria-hidden="true"
                />
                <span>{own.label}</span>
                <span className="ml-auto text-xs text-muted-foreground">{own.has_key ? "Key added" : "Add key"}</span>
              </DropdownMenuItem>
            ))}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function OwnKeyDialog({
  slot,
  own,
  component,
  onClose,
  onSaved,
}: {
  slot: ModelSlot;
  own: Own;
  component: string;
  onClose: () => void;
  onSaved: (value: string, label: string) => Promise<void>;
}) {
  const current = slot.current.startsWith(`own:${own.vendor}/`) ? slot.current.split("/").slice(1).join("/") : "";
  const [model, setModel] = useState(current || own.models[0] || "");
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const needsKey = !own.has_key;

  const save = async () => {
    setSaving(true);
    if (key.trim()) {
      const res = await setProviderKeyApiV1ProviderKeysPut({
        body: { component, provider: own.vendor, api_key: key.trim(), label: null, apply_to_all_components: false },
      });
      if (res.error) {
        toast.error(detailFromError(res.error, "Could not save that key"));
        setSaving(false);
        return;
      }
    }
    await onSaved(`own:${own.vendor}/${model}`, `${own.label} · ${model}`);
    setSaving(false);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {slot.label} on your {own.label} key
          </DialogTitle>
          <DialogDescription>
            Every agent will use it. Your key is stored encrypted and only its last four characters are ever shown.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="block space-y-1.5 text-sm">
            <span className="text-muted-foreground">Model</span>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
            >
              {own.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1.5 text-sm">
            <span className="text-muted-foreground">{needsKey ? "API key" : "API key (leave empty to keep the one you added)"}</span>
            <Input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={needsKey ? `Your ${own.label} API key` : "••••"}
            />
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving || !model || (needsKey && !key.trim())}>
            {saving ? "Saving…" : "Use it"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
