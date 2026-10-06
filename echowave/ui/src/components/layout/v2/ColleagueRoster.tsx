import { Plus } from "lucide-react";
import Link from "next/link";

import type { TeamMember } from "@/client/types.gen";
import { AgentAvatar } from "@/components/avatar/AgentAvatar";
import { type Avatar, faceOf } from "@/components/avatar/avatar";
import { BotAvatar } from "@/components/bot/BotAvatar";
import { useFeature } from "@/lib/features";

import { colleagueState, RAIL_COPY } from "./homes";

/** How many colleagues the rail holds before "n more" (worst-first list). */
export const ROSTER_LIMIT = 8;

type ColleagueRosterProps = {
  colleagues: TeamMember[];
  pathname: string;
  onNavigate?: () => void;
};

/**
 * The org's agents as colleagues, each with a dot: live, needs you, idle.
 * /team/status sorts worst-first, so the one that needs somebody is on top.
 * With agent_faces on (KAN-260), each wears its own still face instead of the
 * job icon: a still frame, because a rail of moving faces pulls the eye from
 * the page beside it.
 */
export function ColleagueRoster({ colleagues, pathname, onNavigate }: ColleagueRosterProps) {
  const shown = colleagues.slice(0, ROSTER_LIMIT);
  const hidden = colleagues.length - shown.length;
  const facesOn = useFeature("agent_faces");

  return (
    <section aria-labelledby="v2-colleagues-label" className="v2-section">
      <div className="v2-eyebrow-row">
        <h2 id="v2-colleagues-label" className="v2-eyebrow">
          {RAIL_COPY.colleagues}
        </h2>
        <Link href="/start" aria-label={RAIL_COPY.addColleague} title={RAIL_COPY.addColleague} className="v2-icon-link" onClick={onNavigate}>
          <Plus aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </div>
      <ul className="v2-roster">
        {shown.map((member) => {
          const href = `/workflow/${member.workflow_id}/thread`;
          const active = pathname === href || pathname.startsWith(`${href}/`);
          const state = colleagueState(member);
          return (
            <li key={member.workflow_id}>
              <Link
                href={href}
                title={member.name}
                aria-current={active ? "page" : undefined}
                onClick={onNavigate}
                data-state={state}
              >
                {facesOn ? (
                  <AgentAvatar
                    avatar={faceOf(member.workflow_id, member.avatar as Avatar | null | undefined)}
                    tone={member.tone}
                    size={28}
                    animate={false}
                  />
                ) : (
                  <BotAvatar id={member.workflow_id} name={member.name} />
                )}
                <span className="v2-roster-text">
                  <span className="v2-roster-name">{member.name}</span>
                  {member.status && <span className="v2-roster-sub">{member.status}</span>}
                </span>
                <span
                  className="v2-dot"
                  data-state={state}
                  role="img"
                  aria-label={RAIL_COPY.state[state]}
                  data-testid={`v2-dot-${member.workflow_id}`}
                />
              </Link>
            </li>
          );
        })}
        {hidden > 0 && (
          <li>
            <Link href="/workflow" className="v2-roster-more" onClick={onNavigate}>
              {RAIL_COPY.moreColleagues(hidden)}
            </Link>
          </li>
        )}
      </ul>
    </section>
  );
}
