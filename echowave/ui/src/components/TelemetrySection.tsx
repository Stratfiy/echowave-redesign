"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  deleteLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsDelete,
  getLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsGet,
  saveLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsPost,
} from "@/client/sdk.gen";
import type { LangfuseCredentialsResponse } from "@/client/types.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { useAuth } from "@/lib/auth";

const emptyCredentials: LangfuseCredentialsResponse = {
  host: "",
  public_key: "",
  secret_key: "",
  configured: false,
};

export function TelemetrySection() {
  const { user, loading: authLoading } = useAuth();
  const { confirm, dialog } = useConfirm();
  const [credentials, setCredentials] =
    useState<LangfuseCredentialsResponse>(emptyCredentials);
  // The stored values, as last read back from the server. See the same pattern
  // in OrganizationPreferencesSection: "dirty" has to mean "differs from what
  // is saved", not "was typed in".
  const [baseline, setBaseline] =
    useState<LangfuseCredentialsResponse>(emptyCredentials);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    fetchCredentials();
  }, [authLoading, user]);

  async function fetchCredentials() {
    try {
      const { data } = await getLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsGet();
      if (data) {
        setCredentials(data);
        setBaseline(data);
      }
    } catch {
      // No credentials configured yet — that's fine
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const { error } = await saveLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsPost({
        body: {
          host: credentials.host ?? "",
          public_key: credentials.public_key ?? "",
          secret_key: credentials.secret_key ?? "",
        },
      });
      if (error) {
        throw new Error("Failed to save");
      }
      toast.success("Telemetry credentials saved");
      // Re-reads the stored row, which moves the baseline with it and clears
      // the dirty flag.
      await fetchCredentials();
    } catch {
      toast.error("Failed to save telemetry credentials");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    // This button used to remove the credentials on a single click, with no
    // warning and no undo — the only destructive action on the settings page
    // that did not ask. Tracing stops silently for every call after it.
    const ok = await confirm({
      title: "Remove telemetry credentials?",
      description:
        "Call tracing to Langfuse stops immediately and the stored keys are deleted. You will need to paste them again to turn it back on.",
      confirmLabel: "Remove credentials",
      destructive: true,
    });
    if (!ok) return;

    setSaving(true);
    try {
      await deleteLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsDelete();
      setCredentials(emptyCredentials);
      setBaseline(emptyCredentials);
      toast.success("Telemetry credentials removed");
    } catch {
      toast.error("Failed to remove telemetry credentials");
    } finally {
      setSaving(false);
    }
  }

  // Above the early return: hook order has to be identical on every render.
  const isDirty =
    (credentials.host ?? "") !== (baseline.host ?? "") ||
    (credentials.public_key ?? "") !== (baseline.public_key ?? "") ||
    (credentials.secret_key ?? "") !== (baseline.secret_key ?? "");

  useUnsavedChanges("telemetry", isDirty);

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      {dialog}
      <p className="text-sm text-muted-foreground">
        Connect your Langfuse project to receive call tracing data.
      </p>
      <div className="space-y-2">
        <Label htmlFor="langfuse-host">Host</Label>
        <Input
          id="langfuse-host"
          placeholder="https://cloud.langfuse.com"
          value={credentials.host}
          onChange={(e) => setCredentials({ ...credentials, host: e.target.value })}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="langfuse-public-key">Public Key</Label>
        <Input
          id="langfuse-public-key"
          placeholder="pk-lf-..."
          value={credentials.public_key}
          onChange={(e) => setCredentials({ ...credentials, public_key: e.target.value })}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="langfuse-secret-key">Secret Key</Label>
        <Input
          id="langfuse-secret-key"
          type="password"
          placeholder="sk-lf-..."
          value={credentials.secret_key}
          onChange={(e) => setCredentials({ ...credentials, secret_key: e.target.value })}
          required
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={saving || !isDirty}>
          {saving ? "Saving..." : isDirty ? "Save" : "Saved"}
        </Button>
        {credentials.configured && (
          <Button type="button" variant="destructive" disabled={saving} onClick={handleDelete}>
            Remove
          </Button>
        )}
      </div>
    </form>
  );
}
