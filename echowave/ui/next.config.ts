import { withSentryConfig } from "@sentry/nextjs";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  output: 'standalone',
  experimental: {
    serverSourceMaps: true,
  },
  // A page is never cached by a device. The hashed assets under _next/static
  // are immutable and cached for a year, which is right; the HTML that names
  // them carried no Cache-Control at all, so a browser kept it by heuristic
  // and a tablet opened this morning still showed last night's build (the
  // pale sidebar, seen on 15 September) hours after the deploy that replaced
  // it. no-cache keeps every page revalidated on open.
  async headers() {
    return [
      {
        source: "/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png).*)",
        headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/ingest/static/:path*",
        destination: "https://us-assets.i.posthog.com/static/:path*",
      },
      {
        source: "/ingest/:path*",
        destination: "https://us.i.posthog.com/:path*",
      },
      {
        source: "/ingest/decide",
        destination: "https://us.i.posthog.com/decide",
      },
    ];
  },
  async redirects() {
    return [
      // The page moved to /integrations, folded in with Tools under one
      // "Integrations" label. Not permanent: a 308 would have browsers and
      // CDNs cache the redirect indefinitely, and this route still has old
      // bookmarks and the odd stale deep link pointing at it.
      {
        source: "/provider-keys",
        destination: "/integrations",
        permanent: false,
      },
    ];
  },
  // This is required to support PostHog trailing slash API requests
  skipTrailingSlashRedirect: true,
};

export default withSentryConfig(nextConfig, {
  // For all available options, see:
  // https://www.npmjs.com/package/@sentry/webpack-plugin#options

  // Sentry organisation and project slugs, used at build time to upload source
  // maps. These are live external identifiers rather than brand strings, which
  // is why the rebrand left "echowave" here — renaming it without a matching
  // Sentry org silently breaks stack traces instead of fixing anything.
  //
  // Now configuration: a deployment sets its own, and one that sets neither
  // simply uploads no source maps. Errors are still reported either way; only
  // the readability of the stack trace depends on this.
  org: process.env.SENTRY_ORG || "",
  project: process.env.SENTRY_PROJECT || "",

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // For all available options, see:
  // https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/

  // Upload a larger set of source maps for prettier stack traces (increases build time)
  widenClientFileUpload: true,

  // Route browser requests to Sentry through a Next.js rewrite to circumvent ad-blockers.
  // This can increase your server load as well as your hosting bill.
  // Note: Check that the configured route will not match with your Next.js middleware, otherwise reporting of client-
  // side errors will fail.
  tunnelRoute: "/monitoring",

  webpack: {
    // Automatically tree-shake Sentry logger statements to reduce bundle size
    treeshake: {
      removeDebugLogging: true,
    },

    // Enables automatic instrumentation of Vercel Cron Monitors. (Does not yet work with App Router route handlers.)
    // See the following for more information:
    // https://docs.sentry.io/product/crons/
    // https://vercel.com/docs/cron-jobs
    automaticVercelMonitors: true,
  },
});
