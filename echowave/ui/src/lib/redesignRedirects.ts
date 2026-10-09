/**
 * Old addresses of screens the redesign removed or merged (UI-0, KAN-257
 * parts 2-3), and where their content lives now. Read by next.config.ts.
 *
 * Kept as data, apart from the config, so a test can check that every target
 * is a page that exists and no source is one that still does.
 *
 * Next carries the query string across a redirect, so /reports?date=... lands
 * on /analytics?date=..., which opens that day.
 */

export type RedesignRedirect = { source: string; destination: string };

export const REDESIGN_REDIRECTS: RedesignRedirect[] = [
    // Dead: redirects and a form nothing linked to.
    { source: "/integrations/apps", destination: "/marketplace/integrations" },
    { source: "/integrations/apps/more", destination: "/marketplace/integrations" },
    { source: "/model-configurations", destination: "/workflow" },
    { source: "/workflow/:workflowId(\\d+)/setup", destination: "/workflow/:workflowId" },

    // Merged: the same thing shown twice.
    { source: "/requests", destination: "/tasks" },
    { source: "/overview/about", destination: "/overview?about=1" },
    { source: "/reports", destination: "/analytics" },
    { source: "/workflow/:workflowId(\\d+)/evals", destination: "/workflow/:workflowId/settings?tab=analysis" },
    { source: "/workflow/create", destination: "/start" },
    { source: "/analytics/spend", destination: "/billing/spend" },
    { source: "/verification", destination: "/numbers?verify=1" },
    { source: "/workflow/archived", destination: "/workflow?show=archived" },

    // Settings holds everything set up once and left alone (October 2026):
    // each of these is a section of it now. Detail pages (/tools/:id,
    // /channels/:id) stay where they were.
    { source: "/tools", destination: "/settings/apps" },
    { source: "/channels", destination: "/settings/channels" },
    { source: "/company", destination: "/settings/company" },
    { source: "/api-keys", destination: "/settings/developer" },
    { source: "/privacy", destination: "/settings/compliance" },
    { source: "/integrations", destination: "/settings/models" },
    // Own keys are added where they are used: Settings -> Models.
    { source: "/settings/api-keys", destination: "/settings/models" },
    { source: "/telephony-configurations", destination: "/settings/phone-number" },

    // Files is one page, reached from the rail, not a section of Settings.
    { source: "/settings/knowledge", destination: "/files" },

    // Launch shell (handoff sections 19-20): the logical destinations, Chat,
    // Today and profile Settings, under the names people and the handoff use
    // for them. Each lands on the existing route, and a conversation link
    // keeps its record: /chat/<id> opens that thread, still checked
    // server-side against the person reading it.
    { source: "/chat", destination: "/overview" },
    { source: "/chat/:threadId", destination: "/overview?thread=:threadId" },
    { source: "/home", destination: "/overview" },
    { source: "/assistant", destination: "/overview" },
    { source: "/today", destination: "/tasks" },
    { source: "/today/activity", destination: "/usage" },
    { source: "/profile", destination: "/settings" },
];
