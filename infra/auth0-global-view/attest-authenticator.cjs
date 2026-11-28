exports.onExecutePostLogin = async (event, api) => {
  if (event.client.client_id !== event.secrets.GLOBAL_VIEW_CLIENT_ID) return;
  // Auth0 resumes at this Action only after the preceding enrollment or challenge.
  const times = (event.authentication?.methods ?? [])
    .filter((method) => method.name === "mfa" && (!method.type || method.type === "otp"))
    .map((method) => Date.parse(method.timestamp))
    .filter(Number.isFinite);
  const verifiedAt = Math.max(...times);
  const now = Date.now();
  if (!event.connection.name || verifiedAt < now - 300_000 || verifiedAt > now + 30_000) {
    api.access.deny("Complete the Global View authenticator challenge before signing in.");
    return;
  }
  api.idToken.setCustomClaim("https://proxigo.ai/claims/mfa_at", Math.floor(verifiedAt / 1000));
  api.idToken.setCustomClaim("https://proxigo.ai/claims/connection", event.connection.name);
};
