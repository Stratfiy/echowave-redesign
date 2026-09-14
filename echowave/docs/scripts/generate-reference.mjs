#!/usr/bin/env node
/**
 * The API reference, generated: one page per method, families the way
 * Slack lays them out (`bots.create`, `calls.start`), from the public spec.
 *
 *   node scripts/generate-reference.mjs          # write pages + nav
 *   node scripts/generate-reference.mjs --check  # fail if anything would change
 *
 * Inputs: `api-reference/openapi.json` (the public document the API serves;
 * the drift check keeps it current) and the FAMILIES map below, which is
 * the one editorial decision here -- which operations are a customer's to
 * call, under which object, with what name. An operation in the spec and
 * not in the map is not documented; an operation in the map and not in the
 * spec fails this script, so a route that is removed cannot leave a page
 * behind.
 *
 * Outputs: `api-reference/<family>/<verb>.mdx` for every method, one
 * `api-reference/<family>.mdx` index per family, and the "Reference" tab
 * of `docs.json`. A generated page carries a marker in its frontmatter;
 * prose added under the marker line is kept on regeneration, so a page can
 * grow a hand-written paragraph without being overwritten.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SPEC = JSON.parse(readFileSync(join(ROOT, "api-reference/openapi.json"), "utf8"));
const DOCS_JSON = join(ROOT, "docs.json");
const CHECK = process.argv.includes("--check");

/**
 * family → { title, blurb, methods: [[verb, "METHOD /path", one-line]] }
 *
 * Verb names follow Slack: the object is the family, the verb says what
 * happens, dotted sub-objects where the app has them (contacts.lists).
 */
const FAMILIES = {
  bots: {
    title: "Bots",
    blurb: "A bot is a job the business has hired an agent for: answer the phone, confirm orders, chase payments. Create one from a template or a definition, update its draft, publish, and read its versions.",
    methods: [
      ["createFromTemplate", "POST /api/v1/workflow/create/template", "Describe the bot in plain words and get a working draft."],
      ["create", "POST /api/v1/workflow/create/definition", "Create a bot from a full definition."],
      ["list", "GET /api/v1/workflow/fetch", "Every bot in the account."],
      ["summary", "GET /api/v1/workflow/summary", "Ids and names only, for pickers."],
      ["count", "GET /api/v1/workflow/count", "How many bots the account has."],
      ["get", "GET /api/v1/workflow/fetch/{workflow_id}", "One bot: its draft if it has one, else what is live."],
      ["update", "PUT /api/v1/workflow/{workflow_id}", "Save the draft."],
      ["validate", "POST /api/v1/workflow/{workflow_id}/validate", "Check a definition before publishing."],
      ["publish", "POST /api/v1/workflow/{workflow_id}/publish", "Make the draft the live version."],
      ["createDraft", "POST /api/v1/workflow/{workflow_id}/create-draft", "Start a draft from what is live."],
      ["versions", "GET /api/v1/workflow/{workflow_id}/versions", "Every version, newest first."],
      ["restoreVersion", "POST /api/v1/workflow/{workflow_id}/versions/{version_id}/restore", "Copy an older version into the draft."],
      ["setLive", "PUT /api/v1/workflow/{workflow_id}/live", "Turn a bot on or off for calls."],
      ["setStatus", "PUT /api/v1/workflow/{workflow_id}/status", "Archive or restore a bot."],
      ["duplicate", "POST /api/v1/workflow/{workflow_id}/duplicate", "Copy a bot."],
      ["callOutcomes", "GET /api/v1/workflow/call-outcomes", "Every outcome label the account's calls can carry."],
    ],
  },
  calls: {
    title: "Calls",
    blurb: "A call is one run of a bot on a phone line. Start one from your backend, list what happened, and fetch the transcript, recording and outcome.",
    methods: [
      ["start", "POST /api/v1/public/agent/{uuid}", "Place a call with a bot, by its public id."],
      ["startTest", "POST /api/v1/public/agent/test/{uuid}", "Place a test call that runs the draft."],
      ["startByWorkflow", "POST /api/v1/public/agent/workflow/{workflow_uuid}", "Place a call by workflow id."],
      ["create", "POST /api/v1/workflow/{workflow_id}/runs", "Create a run for a bot (voice, text or web)."],
      ["list", "GET /api/v1/workflow/{workflow_id}/runs", "Runs of one bot, newest first."],
      ["get", "GET /api/v1/workflow/{workflow_id}/runs/{run_id}", "One run: status, outcome, artifacts."],
      ["listAll", "GET /api/v1/organizations/usage/runs", "Every run in the account, across bots, with cost."],
      ["artifact", "GET /api/v1/public/download/workflow/{token}/{artifact_type}", "Download a recording or transcript with a signed token."],
      ["initiate", "POST /api/v1/telephony/initiate-call", "Dial a number with a bot through a telephony configuration."],
    ],
  },
  campaigns: {
    title: "Campaigns",
    blurb: "A campaign dials a list with a bot inside a calling window, with retries, the do-not-call list and consent applied on every number.",
    methods: [
      ["create", "POST /api/v1/campaign/create", "Create a campaign from a contact list and a bot."],
      ["list", "GET /api/v1/campaign/", "Every campaign in the account."],
      ["get", "GET /api/v1/campaign/{campaign_id}", "One campaign."],
      ["update", "PATCH /api/v1/campaign/{campaign_id}", "Change a campaign that has not started."],
      ["start", "POST /api/v1/campaign/{campaign_id}/start", "Start dialling."],
      ["pause", "POST /api/v1/campaign/{campaign_id}/pause", "Pause dialling; calls in progress finish."],
      ["resume", "POST /api/v1/campaign/{campaign_id}/resume", "Resume a paused campaign."],
      ["redial", "POST /api/v1/campaign/{campaign_id}/redial", "Dial the numbers that were not reached again."],
      ["progress", "GET /api/v1/campaign/{campaign_id}/progress", "Dialled, answered, remaining."],
      ["summary", "GET /api/v1/campaign/{campaign_id}/summary", "Outcomes and cost so far."],
      ["runs", "GET /api/v1/campaign/{campaign_id}/runs", "Every call the campaign placed."],
      ["report", "GET /api/v1/campaign/{campaign_id}/report", "Download the report."],
    ],
  },
  contacts: {
    title: "Contacts",
    blurb: "Contact lists feed campaigns. The do-not-call list is checked before every outbound call the account places, whatever started it.",
    methods: [
      ["lists.create", "POST /api/v1/contact-lists", "Create a list."],
      ["lists.list", "GET /api/v1/contact-lists", "Every list."],
      ["lists.update", "PATCH /api/v1/contact-lists/{contact_list_id}", "Rename a list."],
      ["lists.delete", "DELETE /api/v1/contact-lists/{contact_list_id}", "Delete a list."],
      ["uploadUrl", "POST /api/v1/s3/presigned-upload-url", "A signed URL to upload a contacts file to, before importing it."],
      ["import", "POST /api/v1/contact-lists/{contact_list_id}/import", "Import contacts from a file."],
      ["list", "GET /api/v1/contact-lists/{contact_list_id}/contacts", "Contacts in a list."],
      ["delete", "DELETE /api/v1/contact-lists/{contact_list_id}/contacts/{contact_id}", "Remove a contact."],
      ["doNotCall.list", "GET /api/v1/do-not-call", "Numbers the account will never dial."],
      ["doNotCall.add", "POST /api/v1/do-not-call", "Add numbers."],
      ["doNotCall.upload", "POST /api/v1/do-not-call/upload", "Upload a file of numbers."],
      ["doNotCall.remove", "DELETE /api/v1/do-not-call/{phone_number}", "Remove a number."],
    ],
  },
  numbers: {
    title: "Numbers",
    blurb: "Managed numbers are rented from Decibyl and pointed at a bot. Verified numbers are the business's own, proved by a call, for caller id. Missed calls are rings nobody answered.",
    methods: [
      ["search", "POST /api/v1/managed-numbers/search", "Numbers available to rent."],
      ["provision", "POST /api/v1/managed-numbers", "Rent a number."],
      ["release", "POST /api/v1/managed-numbers/{phone_number_id}/release", "Give a number back."],
      ["verified.list", "GET /api/v1/verified-numbers", "The business's own verified numbers."],
      ["verified.options", "GET /api/v1/verified-numbers/options", "How a number can be verified."],
      ["verified.start", "POST /api/v1/verified-numbers/start", "Start verifying a number."],
      ["verified.confirm", "POST /api/v1/verified-numbers/confirm", "Confirm with the code."],
      ["verified.remove", "DELETE /api/v1/verified-numbers/{phone_number}", "Remove a verified number."],
      ["missed.list", "GET /api/v1/missed-calls", "Callers who rang and were not answered."],
    ],
  },
  telephony: {
    title: "Telephony",
    blurb: "Your own carrier accounts and the numbers on them, for accounts that bring a Twilio, Plivo, Vonage, Telnyx or SIP trunk instead of renting managed numbers.",
    methods: [
      ["providers", "GET /api/v1/organizations/telephony-providers/metadata", "Providers and what each needs."],
      ["configs.create", "POST /api/v1/organizations/telephony-configs", "Connect a carrier account."],
      ["configs.list", "GET /api/v1/organizations/telephony-configs", "Every carrier account."],
      ["configs.get", "GET /api/v1/organizations/telephony-configs/{config_id}", "One carrier account."],
      ["configs.update", "PUT /api/v1/organizations/telephony-configs/{config_id}", "Change a carrier account."],
      ["configs.delete", "DELETE /api/v1/organizations/telephony-configs/{config_id}", "Disconnect a carrier account."],
      ["configs.setDefaultOutbound", "POST /api/v1/organizations/telephony-configs/{config_id}/set-default-outbound", "Make this the account outbound calls use."],
      ["phoneNumbers.list", "GET /api/v1/organizations/telephony-configs/{config_id}/phone-numbers", "Numbers on a carrier account."],
      ["phoneNumbers.create", "POST /api/v1/organizations/telephony-configs/{config_id}/phone-numbers", "Add a number and point it at a bot."],
      ["phoneNumbers.get", "GET /api/v1/organizations/telephony-configs/{config_id}/phone-numbers/{phone_number_id}", "One number."],
      ["phoneNumbers.update", "PUT /api/v1/organizations/telephony-configs/{config_id}/phone-numbers/{phone_number_id}", "Repoint or rename a number."],
      ["phoneNumbers.delete", "DELETE /api/v1/organizations/telephony-configs/{config_id}/phone-numbers/{phone_number_id}", "Remove a number."],
      ["phoneNumbers.setDefaultCaller", "POST /api/v1/organizations/telephony-configs/{config_id}/phone-numbers/{phone_number_id}/set-default-caller", "Make this the caller id."],
    ],
  },
  channels: {
    title: "Channels and timeline",
    blurb: "A channel is a team of bots and people. The timeline is everything that happened: messages, calls, outcomes, questions a bot asked, actions it proposed. Post a message to a bot, answer its question, confirm or undo an action.",
    methods: [
      ["list", "GET /api/v1/folder/", "Every channel."],
      ["create", "POST /api/v1/folder/", "Create a channel."],
      ["rename", "PUT /api/v1/folder/{folder_id}", "Rename a channel."],
      ["delete", "DELETE /api/v1/folder/{folder_id}", "Delete a channel."],
      ["timeline", "GET /api/v1/timeline", "What happened, newest first, for a channel, a bot, a call or Decibyl."],
      ["post", "POST /api/v1/timeline/message", "Say something to a bot, a channel or Decibyl."],
      ["decide", "POST /api/v1/timeline/decide", "Answer a question a bot asked."],
      ["settleAction", "POST /api/v1/timeline/actions/settle", "Confirm, decline or undo an action a bot proposed."],
      ["settleEdit", "POST /api/v1/timeline/edits/settle", "Publish or discard a change a bot proposed to itself."],
      ["provideSecret", "POST /api/v1/timeline/secrets/provide", "Give a bot the key it asked for, into the credential store."],
      ["draft", "GET /api/v1/timeline/draft", "The reply forming, while a bot is thinking."],
    ],
  },
  knowledge: {
    title: "Knowledge",
    blurb: "Documents every bot in the account can read from. Upload, process, search, translate.",
    methods: [
      ["uploadUrl", "POST /api/v1/knowledge-base/upload-url", "A signed URL to upload a document to."],
      ["process", "POST /api/v1/knowledge-base/process-document", "Index an uploaded document."],
      ["list", "GET /api/v1/knowledge-base/documents", "Every document."],
      ["get", "GET /api/v1/knowledge-base/documents/{document_uuid}", "One document and its status."],
      ["delete", "DELETE /api/v1/knowledge-base/documents/{document_uuid}", "Remove a document and everything indexed from it."],
      ["translate", "POST /api/v1/knowledge-base/documents/{document_uuid}/translate", "A copy of a document in another language."],
      ["search", "POST /api/v1/knowledge-base/search", "The passages that match a question."],
      ["usage", "GET /api/v1/knowledge-base/usage", "Storage used against the plan."],
    ],
  },
  tools: {
    title: "Tools and connectors",
    blurb: "A tool is something a bot can do in outside software: an HTTP call, an MCP server, a connected app. Connectors are the apps the account has signed in to.",
    methods: [
      ["create", "POST /api/v1/tools/", "Create a tool."],
      ["list", "GET /api/v1/tools/", "Every tool."],
      ["get", "GET /api/v1/tools/{tool_uuid}", "One tool."],
      ["update", "PUT /api/v1/tools/{tool_uuid}", "Change a tool."],
      ["delete", "DELETE /api/v1/tools/{tool_uuid}", "Archive a tool."],
      ["unarchive", "POST /api/v1/tools/{tool_uuid}/unarchive", "Bring a tool back."],
      ["refreshMcp", "POST /api/v1/tools/{tool_uuid}/mcp/refresh", "Re-read an MCP server's tool list."],
      ["connectors.list", "GET /api/v1/connectors", "Apps that can be connected."],
      ["connectors.connect", "POST /api/v1/connectors/{slug}/connect", "Start connecting an app."],
      ["connectors.accounts", "GET /api/v1/connectors/accounts", "Apps the account has connected."],
      ["connectors.activity", "GET /api/v1/connectors/activity", "What bots did in connected apps."],
    ],
  },
  credentials: {
    title: "Credentials",
    blurb: "Keys and passwords a bot's tools use, stored once and referenced by id. A bot never sees the value.",
    methods: [
      ["create", "POST /api/v1/credentials/", "Store a credential."],
      ["list", "GET /api/v1/credentials/", "Every credential, by name and hint."],
      ["get", "GET /api/v1/credentials/{credential_uuid}", "One credential's metadata."],
      ["update", "PUT /api/v1/credentials/{credential_uuid}", "Replace the value."],
      ["delete", "DELETE /api/v1/credentials/{credential_uuid}", "Remove a credential."],
    ],
  },
  keys: {
    title: "API keys",
    blurb: "Keys that call this API. Create one in the dashboard or here, send it in the X-API-Key header.",
    methods: [
      ["create", "POST /api/v1/user/api-keys", "Create a key. The value is shown once."],
      ["list", "GET /api/v1/user/api-keys", "Every key, by prefix."],
      ["archive", "DELETE /api/v1/user/api-keys/{api_key_id}", "Stop a key."],
      ["reactivate", "PUT /api/v1/user/api-keys/{api_key_id}/reactivate", "Start a stopped key again."],
    ],
  },
  team: {
    title: "Team",
    blurb: "What the team did today, and who is on it.",
    methods: [
      ["home", "GET /api/v1/team/home", "The numbers Home shows: calls, answers, outcomes, what needs attention."],
      ["status", "GET /api/v1/team/status", "Each bot's state right now."],
    ],
  },
  billing: {
    title: "Billing",
    blurb: "Balance, plan, payments and tax documents. Read what the app shows; top up and subscribe from here too.",
    methods: [
      ["balance", "GET /api/v1/billing/balance", "Credit balance."],
      ["plans", "GET /api/v1/billing/plans", "The plan ladder, prices, credits and caps. No sign-in needed."],
      ["plan", "GET /api/v1/billing/plan", "The current plan."],
      ["subscribe", "POST /api/v1/billing/plan", "Subscribe to a plan."],
      ["topup", "POST /api/v1/billing/topup", "Buy credit."],
      ["payments", "GET /api/v1/billing/payments", "Every payment."],
      ["autoTopup.get", "GET /api/v1/billing/auto-topup", "Auto top-up settings."],
      ["autoTopup.save", "PUT /api/v1/billing/auto-topup", "Change auto top-up."],
      ["documents.list", "GET /api/v1/billing/documents", "Tax invoices."],
      ["documents.get", "GET /api/v1/billing/documents/{document_id}", "One invoice."],
      ["documents.pdf", "GET /api/v1/billing/documents/{document_id}/pdf", "The invoice as PDF."],
    ],
  },
  marketplace: {
    title: "Marketplace",
    blurb: "Bots by industry and function, and packs of bots for a vertical. Hire one into the account.",
    methods: [
      ["templates.list", "GET /api/v1/agent-templates", "Every bot on the shelf."],
      ["templates.get", "GET /api/v1/agent-templates/{template_id}", "One template."],
      ["templates.hire", "POST /api/v1/agent-templates/{template_id}/create", "Create a bot from a template."],
      ["packs.list", "GET /api/v1/packs", "Packs on the shelf."],
      ["packs.get", "GET /api/v1/packs/{slug}", "One pack."],
    ],
  },
};

const MARK = "{/* generated: prose below this line is kept */}";

function frontmatter(title, description, op) {
  const esc = (s) => String(s).replace(/"/g, '\\"');
  return `---\ntitle: "${esc(title)}"\ndescription: "${esc(description)}"\nopenapi: "${op}"\n---`;
}

/** Prose from the pages that existed before generation, by operation, so a
 *  paragraph written for "agents/create-from-template" moves to
 *  "bots.createFromTemplate" rather than being lost. Read once. */
const legacyProse = new Map();
for (const file of walk(join(ROOT, "api-reference"))) {
  const text = readFileSync(file, "utf8");
  if (text.includes(MARK)) continue;
  const m = /^openapi:\s*"?([A-Z]+\s+\/\S+?)"?\s*$/m.exec(text);
  if (!m) continue;
  const body = text.replace(/^---[\s\S]*?---\s*/, "").trim();
  if (body) legacyProse.set(m[1], body);
}

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.name.endsWith(".mdx")) out.push(full);
  }
  return out;
}

function keptProse(file, op) {
  if (!existsSync(file)) return legacyProse.get(op) ?? "";
  const text = readFileSync(file, "utf8");
  const at = text.indexOf(MARK);
  if (at === -1) {
    // A hand-written page from before generation: keep its body as prose.
    const body = text.replace(/^---[\s\S]*?---\s*/, "").trim();
    return body;
  }
  return text.slice(at + MARK.length).trim();
}

const changes = [];
function emit(file, content) {
  const current = existsSync(file) ? readFileSync(file, "utf8") : null;
  if (current === content) return;
  changes.push(file);
  if (!CHECK) {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, content);
  }
}

const nav = [];
for (const [family, def] of Object.entries(FAMILIES)) {
  const pages = [`api-reference/${family}`];
  const rows = [];
  for (const [verb, op, line] of def.methods) {
    const [method, path] = op.split(/\s+/, 2);
    const operation = SPEC.paths?.[path]?.[method.toLowerCase()];
    if (!operation) {
      console.error(`generate-reference: ${family}.${verb} names ${op}, which is not in the public spec`);
      process.exit(1);
    }
    // Starlight slugs are lowercase; `createFromTemplate` becomes
    // `create-from-template` on disk and the title keeps the camel case.
    const slug = verb.replace(/\./g, "-").replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
    const file = join(ROOT, "api-reference", family, `${slug}.mdx`);
    const prose = keptProse(file, op);
    const title = `${family}.${verb}`;
    const content = `${frontmatter(title, line, op)}\n\n${line}\n\n${MARK}\n${prose ? `\n${prose}\n` : ""}`;
    emit(file, content);
    pages.push(`api-reference/${family}/${slug}`);
    rows.push(`| [\`${title}\`](/api-reference/${family}/${slug}) | \`${method} ${path}\` | ${line} |`);
  }
  const index = `---\ntitle: "${def.title}"\ndescription: "${def.blurb.replace(/"/g, '\\"')}"\n---\n\n${def.blurb}\n\n| Method | Route | What it does |\n|---|---|---|\n${rows.join("\n")}\n`;
  emit(join(ROOT, "api-reference", `${family}.mdx`), index);
  nav.push({ group: def.title, pages });
}

const docsJson = JSON.parse(readFileSync(DOCS_JSON, "utf8"));
const tab = docsJson.navigation.tabs.find((t) => t.tab === "API Reference" || t.tab === "Reference");
tab.tab = "Reference";
tab.groups = [
  {
    group: "Using the API",
    pages: ["api-reference/overview", "api-reference/authentication", "api-reference/errors", "developer/webhooks"],
  },
  { group: "Methods", pages: nav },
];
const next = `${JSON.stringify(docsJson, null, 2)}\n`;
emit(DOCS_JSON, next);

if (CHECK && changes.length) {
  console.error(`generate-reference: ${changes.length} file(s) would change:\n  ${changes.map((f) => f.replace(ROOT + "/", "")).join("\n  ")}`);
  process.exit(1);
}
console.log(`${CHECK ? "checked" : "wrote"} ${Object.values(FAMILIES).reduce((n, f) => n + f.methods.length, 0)} methods in ${Object.keys(FAMILIES).length} families; ${changes.length} file(s) ${CHECK ? "differ" : "written"}`);
