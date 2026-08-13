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
    // OPENAPI_FILE points generation at a spec dumped to disk instead of a
    // running backend — `python -c "from api.app import app; app.openapi()"`.
    // Needed where the dev port is claimed by something else, and it is the
    // only way to generate against routes that are not deployed yet.
    input: process.env.OPENAPI_FILE || `${backendUrl}/api/v1/openapi.json`,
    output: 'src/client',
    plugins: [{
        name: '@hey-api/client-fetch',
        runtimeConfigPath: './src/lib/apiClient',
    }],
});
