import type { Profile } from "../lib/types";

export function profileWithDefaults(profile: Profile | null): Profile {
  return {
    accent_color: "#FF6600",
    email: "",
    name: "Operator",
    onboarding_complete: true,
    org: "Drone Vision Nav",
    ...profile,
  };
}
