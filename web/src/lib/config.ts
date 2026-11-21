// Settings that differ per environment, read at run time from /config.json (written by the
// container's entrypoint from its environment), so one build serves every environment.

export type AuthConfig = {
  clientId: string; // the web app registration (scripts/setup_entra.sh --web)
  tenantId: string;
  apiScope: string; // api://<API app id>/access_as_user
};

export type AppConfig = {
  apiUrl: string;
  auth: AuthConfig | null; // null: sign-in off (local development, end-to-end tests)
};

let loaded: Promise<AppConfig> | null = null;

export function loadConfig(): Promise<AppConfig> {
  loaded ??= fetch("/config.json", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`config.json: HTTP ${response.status}`);
      return response.json() as Promise<AppConfig>;
    })
    .then((config) => ({ ...config, apiUrl: config.apiUrl.replace(/\/+$/, "") }));
  return loaded;
}
