import { landingApiV1ShellLandingGet } from "@/client/sdk.gen";

/**
 * Where a signed-in person lands, from the server (`/shell/landing`): the
 * onboarding screen until it is done, then Chat -- never the build-an-agent
 * journey. Null while `first_task_onboarding` is off or on any failure, and
 * the caller keeps its own rule, so a blip never strands anyone.
 */
export async function shellLanding(accessToken: string): Promise<string | null> {
    if (!accessToken) return null;
    try {
        const response = await landingApiV1ShellLandingGet({
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        const path = response.data?.path;
        // Only the two paths the server may name; anything else is ignored.
        return path === "/overview" || path === "/welcome" ? path : null;
    } catch {
        return null;
    }
}
