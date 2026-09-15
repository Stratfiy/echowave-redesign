'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';

import logger from '@/lib/logger';

import { isPublicPath } from '../publicPaths';
import type { AuthUser, LocalUser } from '../types';
import { AuthContext } from './AuthProvider';

/**
 * The first read of the token, shared by every instance of the provider.
 *
 * A screen's first fetches fire before the cookie has been read. A getter
 * that answered "" then sent them out with an empty bearer, and the
 * connectors, credentials and template lists opened on a 401. So the
 * getter waits for the first read. The wait lives at module level, not on
 * the instance: under Suspense and StrictMode the provider mounts more than
 * once, and a promise held per instance was resolved by one mount and
 * waited on by another, forever.
 */
const firstRead: { done: boolean; token: string | null; promise: Promise<void>; resolve: () => void } = (() => {
  let resolve: () => void = () => {};
  const promise = new Promise<void>((r) => { resolve = r; });
  return { done: false, token: null, promise, resolve };
})();

function firstReadDone() {
  firstRead.done = true;
  firstRead.resolve();
}

export function LocalProviderWrapper({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<LocalUser | null>(null);
  const [loading, setLoading] = useState(true);
  const tokenRef = useRef<string | null>(null);
  // The first load of the token, as a promise the getter can wait on. A
  // screen's first fetches fire before the cookie has been read, and a
  // getter that answered "" then sent them out with an empty bearer: the
  // connectors, credentials and template lists all opened on a 401.

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const initializeAuth = async () => {
      try {
        const response = await fetch('/api/auth/oss');
        if (response.ok) {
          const data = await response.json();
          tokenRef.current = data.token;
          // Module level as well: a later mount of this provider has an
          // empty ref and must not answer "" for a token already read.
          firstRead.token = data.token;
          setUser(data.user);
          logger.info('OSS auth initialized', { user: data.user });
        } else if (response.status === 401) {
          // No token - redirect to login, unless this page is one a visitor
          // may open without an account (the share page, the auth pages).
          if (!isPublicPath(window.location.pathname)) {
            window.location.href = '/auth/login';
            return;
          }
        } else {
          logger.error('Failed to initialize OSS auth');
        }
      } catch (error) {
        logger.error('Error initializing OSS auth', error);
      } finally {
        setLoading(false);
        firstReadDone();
      }
    };

    initializeAuth();
  }, []);

  const getAccessToken = React.useCallback(async () => {
    if (typeof window === 'undefined') {
      return 'ssr-placeholder-token';
    }
    // Bounded: a load that never finishes must not hold every request.
    if (!firstRead.done) {
      // Bounded: a read that never finishes must not hold every request.
      await Promise.race([firstRead.promise, new Promise<void>((r) => setTimeout(r, 8000))]);
    }
    if (!tokenRef.current && firstRead.token) {
      tokenRef.current = firstRead.token;
    }
    if (!tokenRef.current) {
      logger.warn('No OSS token available after initialization');
      return '';
    }
    return tokenRef.current;
  }, []);

  const redirectToLogin = React.useCallback(() => {
    window.location.href = '/auth/login';
  }, []);

  const logout = React.useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (error) {
      logger.error('Error during logout', error);
    }
    setUser(null);
    tokenRef.current = null;
    firstRead.token = null;
    window.location.href = '/auth/login';
  }, []);

  const contextValue = useMemo(() => ({
    user: user as AuthUser,
    isAuthenticated: !!user,
    loading,
    getAccessToken,
    redirectToLogin,
    logout,
    provider: 'local' as const,
  }), [user, loading, getAccessToken, redirectToLogin, logout]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}
