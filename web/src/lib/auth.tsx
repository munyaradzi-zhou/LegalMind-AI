"use client";

// Sign-in with Microsoft Entra ID (MSAL, authorization code flow with PKCE in the browser).
// The API checks every token itself: this only gets one and sends it.
// With sign-in off (config.auth null) there is no account and requests go without a token.

import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { loadConfig, type AppConfig } from "./config";

type Auth = {
  config: AppConfig | null;
  ready: boolean;
  account: AccountInfo | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  /** A token for the API, or null when sign-in is off. */
  getToken: () => Promise<string | null>;
  error: string | null;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [msal, setMsal] = useState<PublicClientApplication | null>(null);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const loaded = await loadConfig();
        setConfig(loaded);
        if (loaded.auth) {
          const client = new PublicClientApplication({
            auth: {
              clientId: loaded.auth.clientId,
              authority: `https://login.microsoftonline.com/${loaded.auth.tenantId}`,
              redirectUri: window.location.origin,
            },
            cache: { cacheLocation: "sessionStorage" }, // gone when the tab closes
          });
          await client.initialize();
          const result = await client.handleRedirectPromise(); // back from the sign-in page
          const signedIn = result?.account ?? client.getAllAccounts()[0] ?? null;
          client.setActiveAccount(signedIn);
          setAccount(signedIn);
          setMsal(client);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const scopes = useMemo(() => (config?.auth ? [config.auth.apiScope] : []), [config]);

  const signIn = useCallback(async () => {
    await msal?.loginRedirect({ scopes });
  }, [msal, scopes]);

  const signOut = useCallback(async () => {
    await msal?.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
  }, [msal]);

  const getToken = useCallback(async () => {
    if (!config?.auth) return null;
    if (!msal || !account) throw new Error("Not signed in");
    try {
      return (await msal.acquireTokenSilent({ scopes, account })).accessToken;
    } catch (e) {
      if (e instanceof InteractionRequiredAuthError) {
        await msal.acquireTokenRedirect({ scopes, account }); // leaves the page
      }
      throw e;
    }
  }, [config, msal, account, scopes]);

  const value = useMemo(
    () => ({ config, ready, account, signIn, signOut, getToken, error }),
    [config, ready, account, signIn, signOut, getToken, error],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}
