"use client";

import { useEffect, useState } from "react";

import { getMe, type Me } from "./api";
import { useAuth } from "./auth";

/** Who the API says the user is (roles, matters); null until known or when not signed in. */
export function useMe(): { me: Me | null; error: string | null } {
  const { config, account, ready, getToken } = useAuth();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready || !config || (config.auth && !account)) return;
    let cancelled = false;
    getToken()
      .then((token) => getMe(config.apiUrl, token))
      .then((result) => !cancelled && setMe(result))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [ready, config, account, getToken]);

  return { me, error };
}
