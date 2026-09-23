import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * How to make something else start a call.
 *
 * Every capability on this page already worked before it existed. The trigger
 * endpoint, the retrying outbound webhook, custom tools and the MCP server are
 * all shipped and none of them are mentioned anywhere a customer looks — so
 * "can it talk to my CRM / my forms / my ads" gets answered as no, by silence.
 *
 * These are the four recipes an Indian SMB actually asks for, written as the
 * clicks in the tool they already use rather than as an API reference. The
 * endpoint and the payload are the same in all of them; what differs is where
 * the trigger comes from, which is the only part they need help with.
 */

type Recipe = {
    id: string;
    label: string;
    /** What this connects, in the words of somebody describing their problem. */
    blurb: string;
    steps: string[];
    note?: string;
};

const RECIPES: Recipe[] = [
    {
        id: "meta",
        label: "Meta lead ads",
        blurb:
            "Somebody fills your Facebook or Instagram lead form and the agent rings them while they still remember doing it.",
        steps: [
            "In n8n (or Make), add a Facebook Lead Ads trigger and connect the page and form.",
            "Add an HTTP Request node after it.",
            "Method POST, URL as below, and add the header X-API-Key with a key from Developers.",
            "In the body, map phone_number to the phone field from the lead form.",
            "Put anything else you want the agent to know — name, the ad they came from — under initial_context.",
            "Turn the workflow on and submit a test lead.",
        ],
        note:
            "Speed is the entire value. A lead called within a minute converts several times better than one called the next morning, and this is the difference between the two.",
    },
    {
        id: "sheet",
        label: "Google Sheet",
        blurb: "A list somebody maintains by hand becomes a calling list.",
        steps: [
            "In n8n or Zapier, add a Google Sheets trigger — New or Updated Row.",
            "Add an HTTP Request / Webhooks by Zapier action.",
            "POST to the URL below with the X-API-Key header.",
            "Map phone_number to the phone column.",
            "Test with one row before switching it on.",
        ],
        note:
            "For a list that already exists and is not changing, use Campaigns instead — it handles pacing, retries and do-not-call scrubbing, which a spreadsheet trigger does not.",
    },
    {
        id: "crm",
        label: "CRM or webform",
        blurb:
            "A new enquiry in Zoho, HubSpot or your own website form gets qualified before anyone picks up the phone.",
        steps: [
            "Use your CRM's own webhook or automation to fire on a new lead — Zoho calls it a Workflow Rule, HubSpot a Workflow.",
            "Point it at n8n, Make or Zapier, or straight at the URL below if it can send custom headers.",
            "POST with X-API-Key and map phone_number.",
            "To get the outcome back into the CRM, add a Webhook step at the end of the agent on the canvas — it posts the transcript and extracted fields to any URL, with retries.",
        ],
    },
    {
        id: "raw",
        label: "Anything else",
        blurb: "Any system that can send an HTTP request can start a call.",
        steps: [
            "Create a key under Developers.",
            "POST to the URL below with the X-API-Key header and a JSON body.",
            "phone_number is the only required field.",
            "initial_context is a free-form object; whatever you put there is available to the agent as variables during the call.",
        ],
    },
];

function endpointFor(uuid: string | null) {
    const id = uuid || "YOUR_AGENT_ID";
    return `https://api.decibyl.ai/api/v1/public/agent/${id}`;
}

function bodyFor(uuid: string | null) {
    return `curl -X POST ${endpointFor(uuid)} \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "phone_number": "+919876543210",
    "initial_context": {
      "name": "Priya",
      "source": "Instagram lead form"
    }
  }'`;
}

export function ConnectRecipes({ agentUuid }: { agentUuid: string | null }) {
    const [active, setActive] = useState(RECIPES[0].id);
    const [copied, setCopied] = useState(false);

    const recipe = RECIPES.find((r) => r.id === active) ?? RECIPES[0];
    const snippet = bodyFor(agentUuid);

    const copy = () => {
        navigator.clipboard.writeText(snippet);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-base">Start a call from somewhere else</CardTitle>
                <CardDescription>
                    Anything that can send an HTTP request can make this agent ring
                    somebody. Pick where the trigger comes from.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
                <div className="flex flex-wrap gap-2">
                    {RECIPES.map((r) => (
                        <button
                            key={r.id}
                            type="button"
                            onClick={() => setActive(r.id)}
                            aria-pressed={r.id === active}
                            className={cn(
                                "rounded-full border px-3.5 py-1.5 text-sm transition-colors",
                                r.id === active
                                    ? "border-primary bg-primary/5 font-medium text-foreground"
                                    : "border-border text-muted-foreground hover:border-muted-foreground/30 hover:text-foreground",
                            )}
                        >
                            {r.label}
                        </button>
                    ))}
                </div>

                <p className="text-sm text-muted-foreground">{recipe.blurb}</p>

                <ol className="space-y-2.5">
                    {recipe.steps.map((step, i) => (
                        <li key={i} className="flex gap-3 text-sm">
                            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
                                {i + 1}
                            </span>
                            <span className="text-muted-foreground">{step}</span>
                        </li>
                    ))}
                </ol>

                <div className="space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">The request</span>
                        <Button size="sm" variant="outline" onClick={copy}>
                            {copied ? (
                                <>
                                    <Check className="mr-1 h-4 w-4" /> Copied
                                </>
                            ) : (
                                <>
                                    <Copy className="mr-1 h-4 w-4" /> Copy
                                </>
                            )}
                        </Button>
                    </div>
                    <pre className="overflow-x-auto rounded-lg bg-muted/50 p-4 text-xs">
                        <code>{snippet}</code>
                    </pre>
                    {!agentUuid && (
                        <p className="text-xs text-amber-700 dark:text-amber-500">
                            Publish this agent to get its id. Until then the URL above
                            is a placeholder.
                        </p>
                    )}
                </div>

                {recipe.note && (
                    <p className="rounded-lg border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
                        {recipe.note}
                    </p>
                )}

                <div className="space-y-2 border-t pt-4">
                    <p className="text-sm font-medium">Getting data back out</p>
                    <ul className="space-y-1.5 text-xs text-muted-foreground">
                        <li>
                            <span className="font-medium text-foreground">
                                After the call:
                            </span>{" "}
                            add a Webhook step at the end of the agent on the canvas. It
                            posts the transcript and any extracted fields to a URL you
                            choose, and retries with backoff rather than dropping it if
                            your server is briefly down.
                        </li>
                        <li>
                            <span className="font-medium text-foreground">
                                During the call:
                            </span>{" "}
                            a Tool lets the agent call your own API mid-conversation — to
                            check an order, read a balance, or book a slot — and use the
                            answer in what it says next.
                        </li>
                    </ul>
                    <Button variant="outline" size="sm" asChild className="mt-1">
                        <a
                            href="https://docs.decibyl.ai/api-reference"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            API reference
                            <ExternalLink className="ml-2 h-3.5 w-3.5" />
                        </a>
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
