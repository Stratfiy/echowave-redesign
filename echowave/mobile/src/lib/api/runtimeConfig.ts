import type { CreateClientConfig } from '@/client/client.gen';

import { apiBaseUrl } from '@/lib/config';

/**
 * The generated client's starting configuration: the API this build talks
 * to. The bearer token is attached per request by `installAuth` (auth.ts),
 * never baked into the config, so signing out takes effect at once.
 */
export const createClientConfig: CreateClientConfig = (config) => ({
    ...config,
    baseUrl: apiBaseUrl(),
});
