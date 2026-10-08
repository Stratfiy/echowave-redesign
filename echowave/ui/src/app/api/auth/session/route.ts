import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

import { IMPERSONATION_MARKER } from '@/app/impersonate/session-cookies';

const OSS_TOKEN_COOKIE = 'decibyl_auth_token';
const OSS_USER_COOKIE = 'decibyl_auth_user';

export async function POST(request: NextRequest) {
  const { token, user } = await request.json();

  if (!token) {
    return NextResponse.json({ error: 'Missing token' }, { status: 400 });
  }

  const cookieStore = await cookies();

  cookieStore.set(OSS_TOKEN_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  cookieStore.set(OSS_USER_COOKIE, JSON.stringify(user), {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  // A new (or ended) session of your own is never someone else's: drop the
  // impersonation marker, or the banner would name a customer over a
  // staffer's own account for the rest of the hour (phase 3).
  cookieStore.set(IMPERSONATION_MARKER, '', { path: '/', maxAge: 0 });

  return NextResponse.json({ success: true });
}
