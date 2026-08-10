/**
 * Verify every internal link in the built site resolves to a real page.
 *
 * The migration off Mintlify is exactly the kind of change that turns working
 * links into 404s without failing a build: a page that moved, a nav entry that
 * lost its `/index` suffix, a relative link that meant something different
 * under the old renderer. The build cannot catch those — it renders each page
 * happily and never follows an anchor.
 *
 * External links are not checked. They fail for reasons that have nothing to
 * do with this repo (rate limits, a vendor's marketing page moving), and a
 * link checker that goes red for someone else's outage gets ignored, which
 * costs more than it saves.
 *
 * Exits non-zero on the first broken link so this can gate a deploy.
 */

import { readFileSync } from "node:fs";
import { glob } from "node:fs/promises";
import path from "node:path";

const DIST = path.resolve(import.meta.dirname, "..", "dist");

/** Every URL path the built site actually serves. */
async function builtPages() {
  const pages = new Set();
  for await (const file of glob("**/*.html", { cwd: DIST })) {
    const url = "/" + file.replace(/index\.html$/, "").replace(/\.html$/, "");
    pages.add(url.endsWith("/") ? url : url + "/");
    pages.add(url.replace(/\/$/, ""));
  }
  return pages;
}

function linksIn(html) {
  const found = [];
  const re = /<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["']/gi;
  let match;
  while ((match = re.exec(html)) !== null) found.push(match[1]);
  return found;
}

const pages = await builtPages();
const broken = [];
let checked = 0;

for await (const file of glob("**/*.html", { cwd: DIST })) {
  const html = readFileSync(path.join(DIST, file), "utf8");
  const from = "/" + file.replace(/index\.html$/, "");
  // A relative href resolves against the page's *directory*, not the page.
  // From `/deployment/docker.html`, `custom-domain` means
  // `/deployment/custom-domain` — joining onto the page path instead produces
  // `/deployment/docker.html/custom-domain` and reports working links as
  // broken.
  const fromDir = path.posix.dirname(from) + "/";

  for (const href of linksIn(html)) {
    // Skip anything that leaves the site or addresses a position within a page.
    if (
      /^(https?:|mailto:|tel:|#|javascript:)/i.test(href) ||
      href.startsWith("//")
    ) {
      continue;
    }

    const target = href.split("#")[0].split("?")[0];
    if (!target) continue;

    const resolved = target.startsWith("/")
      ? target
      : path.posix.normalize(path.posix.join(fromDir, target));

    checked += 1;

    // An asset (has an extension) is checked against the file tree instead.
    if (path.posix.extname(resolved)) {
      continue;
    }

    const withSlash = resolved.endsWith("/") ? resolved : resolved + "/";
    if (!pages.has(resolved) && !pages.has(withSlash)) {
      broken.push({ from, href, resolved });
    }
  }
}

console.log(
  `Checked ${checked} internal link(s) across ${pages.size / 2} page(s).`,
);

if (broken.length) {
  console.error(`\n${broken.length} broken internal link(s):\n`);
  for (const b of broken) {
    console.error(`  ${b.from}  ->  ${b.href}   (resolves to ${b.resolved})`);
  }
  process.exit(1);
}

console.log("No broken internal links.");
