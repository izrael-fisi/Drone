exports.onExecutePostLogin = async (event, api) => {
  if (event.client.client_id !== event.secrets.GLOBAL_VIEW_CLIENT_ID) return;
  if (!event.organization?.id) {
    api.access.deny("Choose a Global View organization to sign in.");
    return;
  }
  if (event.connection.strategy === "auth0" && event.user.email_verified !== true) {
    api.access.deny("Verify your email address before signing in to Global View.");
    return;
  }
  if (event.user.enrolledFactors?.some((factor) => factor.type === "otp")) {
    api.authentication.challengeWith({ type: "otp" });
    return;
  }
  api.authentication.enrollWith({ type: "otp" });
};
