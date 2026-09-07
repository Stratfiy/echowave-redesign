import { useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { type CallOutcome, DEFAULT_CALL_OUTCOMES } from "@/constants/callOutcomes";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";

/**
 * Every outcome label this organization's calls can carry.
 *
 * The calls list is organization-wide while the taxonomy is per agent, so the
 * filter needs the union rather than one agent's list — otherwise the codes
 * belonging to the other agents are simply missing from the menu and their
 * rows cannot be found.
 *
 * Called through the raw client rather than a generated wrapper: the endpoint
 * is new, and `npm run generate-client` needs a running backend. Swap this for
 * the generated call the next time the client is regenerated.
 */
export function useCallOutcomes() {
    const { user, loading: authLoading } = useAuth();
    const [outcomes, setOutcomes] = useState<CallOutcome[] | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const hasFetched = useRef(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            setIsLoading(true);
            try {
                const response = await client.get<CallOutcome[]>({
                    url: "/api/v1/workflow/call-outcomes",
                });
                if (response.error) {
                    throw new Error(JSON.stringify(response.error));
                }
                setOutcomes(response.data ?? []);
            } catch (error) {
                // The defaults still describe most of what is out there, and a
                // filter offering them beats a filter offering nothing while
                // somebody is trying to find a call.
                logger.error(`Error loading call outcomes: ${error}`);
                setOutcomes([...DEFAULT_CALL_OUTCOMES]);
            } finally {
                setIsLoading(false);
            }
        };

        void load();
    }, [authLoading, user]);

    return { outcomes, isLoading };
}
