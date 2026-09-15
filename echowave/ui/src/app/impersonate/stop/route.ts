import { NextRequest, NextResponse } from "next/server";

import { markerHeader, sessionClearingHeaders } from "../session-cookies";

/**
 * The way out of an impersonation (KAN-82).
 *
 * Starting one wiped the staffer's own Stack cookies, so there is no session
 * to "return" to: stopping means ending the borrowed session -- every Stack
 * cookie in every jar, plus the marker -- and sending the staffer to sign in
 * again, pointed back at the console. Honest and short, rather than a stored
 * copy of a superadmin's token waiting in a cookie for an hour.
 *
 * POST, from the banner's form: a GET that ends a session is a link anyone
 * can plant.
 */
export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(
    new URL("/auth/login?next=/superadmin", request.url),
    303,
  );
  for (const header of sessionClearingHeaders(request)) {
    response.headers.append("set-cookie", header);
  }
  response.headers.append("set-cookie", markerHeader(request, null));
  return response;
}
