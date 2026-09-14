import { existsSync } from 'node:fs';

import { defineConfig } from '@hey-api/openapi-ts';
import { loadEnvConfig } from '@next/env';

// Load .env.local / .env the same way Next.js does, so client generation targets
// the backend THIS worktree actually runs on (per-worktree BACKEND_URL set by
// scripts/worktree-assign-port.sh). Falls back to the default dev port if unset.
loadEnvConfig(process.cwd());

const backendUrl = (
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    'http://127.0.0.1:8000'
).replace(/\/+$/, '');

export default defineConfig({
    // The client needs the WHOLE app, superadmin operations included, and a
    // running backend serves only the public document at /openapi.json (see
    // api/services/openapi_surface.py). So generation reads the internal
    // dump `python -m scripts.dump_docs_openapi` writes to
    // ui/openapi.internal.json; OPENAPI_FILE overrides it, and the live
    // backend is the last resort for a public-only client.
    input:
        process.env.OPENAPI_FILE ||
        (existsSync('openapi.internal.json') ? 'openapi.internal.json' : `${backendUrl}/api/v1/openapi.json`),
    output: 'src/client',
    plugins: [{
        name: '@hey-api/client-fetch',
        runtimeConfigPath: './src/lib/apiClient',
    }],
});
