#!/bin/sh
# Write /config.json for the browser from the container's environment (nginx's entrypoint
# runs this before starting). Without WEB_CLIENT_ID the site runs with sign-in off.
set -eu
if [ -n "${WEB_CLIENT_ID}" ]; then
  auth="{\"clientId\":\"${WEB_CLIENT_ID}\",\"tenantId\":\"${WEB_TENANT_ID}\",\"apiScope\":\"${WEB_API_SCOPE}\"}"
else
  auth="null"
fi
printf '{"apiUrl":"%s","auth":%s}\n' "${WEB_API_URL}" "${auth}" > /tmp/config.json
echo "config.json: api ${WEB_API_URL}, sign-in $([ "${auth}" = null ] && echo off || echo on)"
