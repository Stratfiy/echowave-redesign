import { defineConfig } from '@hey-api/openapi-ts';

// The app talks to the same API as the web app, so its client is generated
// from the same internal dump the web client reads (ui/openapi.internal.json,
// written by `python -m scripts.dump_docs_openapi` from echowave/). No
// endpoint is written by hand: regenerate after a backend route changes.
export default defineConfig({
    input: process.env.OPENAPI_FILE || '../ui/openapi.internal.json',
    output: 'src/client',
    plugins: [
        {
            name: '@hey-api/client-fetch',
            runtimeConfigPath: './src/lib/api/runtimeConfig',
        },
    ],
});
