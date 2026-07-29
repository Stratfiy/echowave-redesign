# @decibyl/sdk

Typed builder for Decibyl voice-AI workflows. Fetches the node-spec catalog from
the Decibyl backend at session start, validates every call against it at the
call site, and produces wire-format JSON that round-trips through the Python
`ReactFlowDTO`.

## Install

```bash
npm install @decibyl/sdk
# or
pnpm add @decibyl/sdk
```

For local development against a checked-out monorepo, add a tsconfig paths
entry:

```json
{
  "paths": {
    "@decibyl/sdk": ["../sdk/typescript/src/index.ts"]
  }
}
```

## Usage

```ts
import { DecibylClient, Workflow } from "@decibyl/sdk";

const client = new DecibylClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.DECIBYL_API_KEY,
});

const wf = new Workflow({ client, name: "loan_qualification" });

const start = await wf.add({
  type: "startCall",
  name: "greeting",
  prompt: "You are Sarah from Acme Loans. Greet the caller warmly.",
  greeting_type: "text",
  greeting: "Hi {{first_name}}, this is Sarah.",
});

const qualify = await wf.add({
  type: "agentNode",
  name: "qualify",
  prompt: "Ask about loan amount and timeline.",
});

const done = await wf.add({ type: "endCall", name: "done", prompt: "Thank them." });

wf.edge(start, qualify, { label: "interested", condition: "Caller expressed interest." });
wf.edge(qualify, done, { label: "done", condition: "Qualification complete." });

await client.saveWorkflow(123, wf);
```

## Client-side validation

Each `add()` call validates kwargs against the fetched spec. `ValidationError`
is thrown immediately when:

- an unknown field is passed (catches typos)
- a required field is missing or empty
- a scalar type is wrong (e.g., string for a boolean)
- an `options` value isn't in the allowed list

When a spec carries an `llm_hint`, the hint is appended to the error so an LLM
agent can self-correct on retry:

```
tool_uuids: expected tool_refs, got string
  Hint: List of tool UUIDs from `list_tools`.
```

Server-side Pydantic validators run on save and surface anything the client
lets through.

## Environment

```bash
DECIBYL_API_URL=http://localhost:8000   # default
DECIBYL_API_KEY=sk-...                  # sent as X-API-Key
```

## License

BSD 2-Clause — see `LICENSE`.
