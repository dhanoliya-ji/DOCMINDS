// A React hook that loads GET /users/me once and hands back the signed-in user -- their...

"use client";

import { useEffect, useState } from "react";

import { CurrentUser, api, getErrorMessage, getToken } from "@/lib/api";

export function useCurrentUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!getToken()) {
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
