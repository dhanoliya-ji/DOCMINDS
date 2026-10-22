/**
 * useCurrentUser.ts
 * =================
 * A React hook that loads GET /users/me once and hands back the signed-in
 * user -- their email, role and whether their email is confirmed.
 *
 * WHY A HOOK
 * ----------
 * Three screens need the same answer (the dashboard's verification banner,
 * the project page's "can this person upload?", and the settings area). A hook
 * keeps the fetch, the loading flag and the error in one place.
 *
 * A FAILED LOOKUP IS NOT FATAL
 * ----------------------------
 * If the call fails for any reason other than an expired session (which
 * api.ts already turns into a redirect to /login), `user` stays null. Callers
 * treat null as "role unknown": they hide role-gated actions, but still show
 * everything a viewer could see. Failing closed on the BUTTONS is safe because
 * the backend makes the real decision anyway.
 */

"use client";

import { useEffect, useState } from "react";

import { CurrentUser, api, getErrorMessage, getToken } from "@/lib/api";

export function useCurrentUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    // `cancelled` stops a slow response from setting state on an unmounted
    // component (navigating away mid-request).
    let cancelled = false;
    if (!getToken()) {
      // Signed out: the page's own guard redirects; nothing to load. The
      // promise defers the state update out of the effect body.
      Promise.resolve().then(() => {
        if (!cancelled) setLoading(false);
      });
      return () => {
        cancelled = true;
      };
    }
    api
      .getMe()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err, "Could not load your account."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loading, error, setUser };
}
