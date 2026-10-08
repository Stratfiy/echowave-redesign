import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

import { IMPERSONATION_MARKER } from '@/app/impersonate/session-cookies';

const OSS_TOKEN_COOKIE = 'decibyl_auth_token';
const OSS_USER_COOKIE = 'decibyl_auth_user';

export async function POST() {
  const cookieStore = await cookies();

  cookieStore.set(OSS_TOKEN_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 0,
    path: '/',
  });

  cookieStore.set(OSS_USER_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 0,
    path: '/',
  });

  // A new (or ended) session of your own is never someone else's: drop the
  // impersonation marker, or the banner would name a customer over a
  // staffer's own account for the rest of the hour (phase 3).
  cookieStore.set(IMPERSONATION_MARKER, '', { path: '/', maxAge: 0 });

  return NextResponse.json({ success: true });
}
