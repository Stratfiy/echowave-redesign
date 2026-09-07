import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Where to paste the script tag.
 *
 * The configurator above this has always produced a correct snippet and has
 * never said what to do with it. "Add this before `</body>`" is an instruction
 * for somebody who already knows what that means, and the buyer this product
 * is sold to runs a clinic or a dealership and reached their website through
 * an admin panel they log into once a month.
 *
 * So: one set of steps per surface, written as clicks in the panel they
 * actually have. Every guide ends on the same two checks, because those are
 * the two ways this fails in practice — the domain is not on the allow list,
 * or the tag went on one page instead of the site.
 */

type Surface = {
    id: string;
    label: string;
    /** What they are looking for, in the words their admin panel uses. */
    steps: string[];
    /** Set where the snippet differs from the plain script tag. */
    snippet?: (embedScript: string) => string;
    note?: string;
};

const SURFACES: Surface[] = [
    {
        id: "wordpress",
        label: "WordPress",
        steps: [
            "In the WordPress admin, go to Plugins → Add New and install a header-and-footer script plugin. WPCode and Insert Headers and Footers are both free and both do only this.",
            "Open the plugin's settings — usually Code Snippets → Header & Footer, or Settings → Insert Headers and Footers.",
            "Paste the script into the Footer or Scripts in Body box. Not the header box: the widget loads after the page so it never slows the page down.",
            "Save, then open your site in a private window and look for the button.",
        ],
        note:
            "Do not paste this into Appearance → Theme File Editor. It works, and the next theme update overwrites it and the widget silently disappears.",
    },
    {
        id: "shopify",
        label: "Shopify",
        steps: [
            "In Shopify admin, go to Online Store → Themes.",
            "On your current theme, open the ⋯ menu and choose Edit code.",
            "In the Layout folder, open theme.liquid.",
            "Scroll to the bottom and paste the script on the line just above </body>.",
            "Click Save, then visit your storefront to check the button appears.",
        ],
        note:
            "Duplicate the theme before editing if you would rather have a way back. Shopify keeps the copy under Themes → Theme library.",
    },
    {
        id: "wix",
        label: "Wix",
        steps: [
            "In the Wix dashboard, go to Settings → Custom Code (under Advanced).",
            "Click + Add Custom Code.",
            "Paste the script into the code box and give it a name — 'Decibyl voice widget' does.",
            "Under Add Code to Pages choose All pages, and set Place Code in to Body - end.",
            "Click Apply, then publish the site. Custom code does not appear in the editor preview — you have to publish and open the live site.",
        ],
        note:
            "Custom code needs a Wix premium plan. On a free site the panel is there and the code never runs.",
    },
    {
        id: "html",
        label: "Plain HTML",
        steps: [
            "Open the HTML file for each page you want the widget on.",
            "Paste the script on the line just above the closing </body> tag.",
            "Save and upload.",
        ],
        note:
            "If your pages share a footer include, put it there once instead of on every page — otherwise a page added later will be the one page without a widget.",
    },
    {
        id: "nextjs",
        label: "Next.js",
        steps: [
            "Open app/layout.tsx (or pages/_document.tsx on the pages router).",
            "Import Script from next/script and render it inside <body>, after {children}.",
            "Keep strategy=\"afterInteractive\" so the widget never blocks first paint.",
        ],
        snippet: (embedScript) => {
            const src = /src="([^"]+)"/.exec(embedScript)?.[1] ?? "";
            return `import Script from "next/script";

// ...inside <body>, after {children}
<Script
  id="decibyl-widget"
  src="${src}"
  strategy="afterInteractive"
/>`;
        },
        note:
            "next/script rather than a raw <script> tag: React strips script tags it renders, so the plain snippet does nothing here.",
    },
];

export function InstallGuides({
    embedScript,
    domains,
}: {
    embedScript: string;
    domains: string[];
}) {
    const [active, setActive] = useState(SURFACES[0].id);
    const [copied, setCopied] = useState(false);

    const surface = SURFACES.find((s) => s.id === active) ?? SURFACES[0];
    const snippet = surface.snippet ? surface.snippet(embedScript) : embedScript;

    const copy = () => {
        navigator.clipboard.writeText(snippet);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-base">Where to paste it</CardTitle>
                <CardDescription>
                    Pick where your site is built. The steps are the clicks in that
                    panel, not the general idea.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
                <div className="flex flex-wrap gap-2">
                    {SURFACES.map((s) => (
                        <button
                            key={s.id}
                            type="button"
                            onClick={() => setActive(s.id)}
                            aria-pressed={s.id === active}
                            className={cn(
                                "rounded-full border px-3.5 py-1.5 text-sm transition-colors",
                                s.id === active
                                    ? "border-primary bg-primary/5 font-medium text-foreground"
                                    : "border-border text-muted-foreground hover:border-muted-foreground/30 hover:text-foreground"
                            )}
                        >
                            {s.label}
                        </button>
                    ))}
                </div>

                <ol className="space-y-2.5">
                    {surface.steps.map((step, i) => (
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
                        <span className="text-sm font-medium">
                            {surface.id === "nextjs" ? "What to write" : "What to paste"}
                        </span>
                        <Button size="sm" variant="outline" onClick={copy}>
                            {copied ? (
                                <>
                                    <Check className="mr-1 h-4 w-4" />
                                    Copied
                                </>
                            ) : (
                                <>
                                    <Copy className="mr-1 h-4 w-4" />
                                    Copy
                                </>
                            )}
                        </Button>
                    </div>
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-4 text-xs break-all">
                        <code>{snippet}</code>
                    </pre>
                </div>

                {surface.note && (
                    <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-100">
                        {surface.note}
                    </p>
                )}

                {/* The two ways this actually fails, on every surface. */}
                <div className="space-y-2 rounded-lg border bg-muted/30 px-4 py-3">
                    <p className="text-sm font-medium">If the button does not appear</p>
                    <ul className="space-y-1.5 text-xs text-muted-foreground">
                        <li>
                            <span className="font-medium text-foreground">
                                Check the domain is on the allow list above.
                            </span>{" "}
                            {domains.length > 0 ? (
                                <>
                                    Right now this widget will only run on{" "}
                                    {domains.map((d, i) => (
                                        <span key={d}>
                                            {i > 0 && ", "}
                                            <code className="rounded bg-muted px-1">{d}</code>
                                        </span>
                                    ))}
                                    . Anywhere else it loads and refuses.
                                </>
                            ) : (
                                <>
                                    No domains are set, so it will not run anywhere. Add the
                                    site&apos;s domain above and save.
                                </>
                            )}
                        </li>
                        <li>
                            <span className="font-medium text-foreground">
                                Check you published.
                            </span>{" "}
                            Most site builders keep edits in a draft until you press
                            Publish, and the preview does not run custom code.
                        </li>
                    </ul>
                </div>
            </CardContent>
        </Card>
    );
}
