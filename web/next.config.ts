import type { NextConfig } from "next";

// A static site: sign-in (MSAL) and the API calls run in the browser, so no server is needed.
// Settings that differ per environment come from /config.json at run time (see Dockerfile).
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
