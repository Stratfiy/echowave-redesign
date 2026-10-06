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
    { source: "/files", destination: "/settings/knowledge" },
    { source: "/channels", destination: "/settings/channels" },
    { source: "/company", destination: "/settings/company" },
    { source: "/api-keys", destination: "/settings/developer" },
    { source: "/privacy", destination: "/settings/compliance" },
    { source: "/integrations", destination: "/settings/models" },
    // Own keys are added where they are used: Settings -> Models.
    { source: "/settings/api-keys", destination: "/settings/models" },
    { source: "/telephony-configurations", destination: "/settings/phone-number" },
];
