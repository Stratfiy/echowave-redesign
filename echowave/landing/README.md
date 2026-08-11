# Apex landing site

`decibyl.ai` is served from this directory. It is empty on purpose: until a
marketing site exists, nginx answers a 404 with a pointer to
`app.decibyl.ai`.

Drop a built static site here — `index.html` and its assets — and it is served
immediately. No nginx change, no restart.

The apex deliberately does not redirect to the app. That would work today and
be in the way the moment there is a landing page to put here.

If the site ends up hosted elsewhere (Vercel, Webflow), point the apex there in
DNS instead and leave this directory empty; the apex server block then never
sees a request.
