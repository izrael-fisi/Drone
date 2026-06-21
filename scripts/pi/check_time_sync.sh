#!/usr/bin/env bash
set -euo pipefail

failures=0

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  failures=$((failures + 1))
}

echo "System time: $(date --iso-8601=seconds 2>/dev/null || date)"

current_year="$(date +%Y)"
if [[ "$current_year" -lt 2024 ]]; then
  fail "System clock appears stale: year=$current_year"
else
  ok "System clock year is plausible: $current_year"
fi

if command -v timedatectl >/dev/null 2>&1; then
  timedatectl status || true
  ntp_enabled="$(timedatectl show -p NTP --value 2>/dev/null || true)"
  synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
  timezone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [[ -n "$timezone" ]] && ok "Timezone: $timezone"
  if [[ "$ntp_enabled" == "yes" || "$ntp_enabled" == "true" ]]; then
    ok "NTP is enabled"
  else
    warn "NTP is not enabled according to timedatectl"
  fi
  if [[ "$synchronized" == "yes" || "$synchronized" == "true" ]]; then
    ok "System clock is synchronized"
  else
    fail "System clock is not synchronized"
  fi
else
  warn "timedatectl is unavailable; checked only the local system date."
fi

if command -v chronyc >/dev/null 2>&1; then
  chronyc tracking || true
elif command -v ntpq >/dev/null 2>&1; then
  ntpq -p || true
else
  warn "No chronyc/ntpq command found for deeper time-source diagnostics."
fi

if ((failures > 0)); then
  echo "Time sync check failed with $failures issue(s)." >&2
  exit 1
fi

echo "Time sync check passed."
