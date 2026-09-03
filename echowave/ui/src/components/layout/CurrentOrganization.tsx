"use client";

import { Building2 } from "lucide-react";
import { useEffect, useState } from "react";

import { getCurrentOrganizationContextApiV1OrganizationsContextGet } from "@/client";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";

/**
 * Which organization the app is currently acting as.
 *
 * Everything an account owns is scoped to one organization — numbers, agents,
 * campaigns, credits — and until now nothing on screen said which one. An
 * account with two teams could verify a number under one, look at the list
 * under the other, and see nothing, with no way to tell that the two views
 * were of different places. That is what this is here to prevent.
 *
 * The id comes from the backend rather than from the client's own idea of the
 * selected team. The two are supposed to agree: the server resolves the
 * organization on every request from the auth token's selected team. But the
 * failure this exists to make visible is precisely the case where they do not,
 * and a badge that reports the client's assumption back to itself would show
 * "correct" in exactly that situation. So the label is the team name, which is
 * the readable part, and the number beside it is the server's answer.
 */
export function CurrentOrganization() {
  const { getSelectedTeam } = useAuth() as {
    getSelectedTeam?: () => { displayName?: string; id?: string } | null;
  };
  const [orgId, setOrgId] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const response = await getCurrentOrganizationContextApiV1OrganizationsContextGet();
      if (cancelled) return;
      if (response.error || response.data?.organization_id == null) {
        setFailed(true);
        return;
      }
      setOrgId(response.data.organization_id);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const team = getSelectedTeam?.() ?? null;
  const name = team?.displayName?.trim();

  // Nothing useful to say yet. An empty badge is worse than no badge: it
  // occupies the spot where the answer belongs and reads as "no organization".
  if (!name && orgId === null && !failed) return null;

  const label = name || (orgId !== null ? `Organization ${orgId}` : "Organization");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className="hidden items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground sm:flex"
          data-testid="current-organization"
        >
          <Building2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="max-w-[14ch] truncate font-medium">{label}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-xs">
        {failed ? (
          <p>Could not read the current organization from the server.</p>
        ) : (
          <>
            <p>
              You are working in <strong>{label}</strong>
              {orgId !== null ? ` (id ${orgId})` : ""}.
            </p>
            <p className="mt-1">
              Numbers, agents and credits belong to this organization. Switch
              teams and you are looking at a different set.
            </p>
          </>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
