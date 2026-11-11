#!/usr/bin/env bash
# End-to-end check of the running stack. Non-interactive. Exit 0 = everything works.
#   ./scripts/verify.sh
# Needs only docker (checks run inside the containers).
set -uo pipefail
cd "$(dirname "$0")/.."
DOCKER="docker"
if ! docker info >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo -n docker"; fi
if $DOCKER compose version >/dev/null 2>&1; then DC="$DOCKER compose"; else DC="docker-compose"; fi
fail=0
echo "== containers"
for s in plc-conveyor-01 plc-dewater-01 plc-vent-01 rtu-gas-01 rtu-sub-01 otmon-mine; do
  st=$($DC ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$s" '$1==s{print $2}')
  if [ "$st" = "running" ]; then echo "[PASS] $s running"; else echo "[FAIL] $s state='${st:-missing}'"; fail=1; fi
done
[ $fail -eq 0 ] || { echo "Stack is not fully up. Run: docker compose up -d --build"; exit 1; }

echo "== dashboard reachable from the host (http://localhost:${OTMON_HOST_PORT:-8088})"
if command -v curl >/dev/null 2>&1; then
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${OTMON_HOST_PORT:-8088}/" || true)
  if [ "$code" = "200" ]; then echo "[PASS] GET / -> 200"; else echo "[FAIL] GET / -> $code"; fail=1; fi
else
  echo "[SKIP] curl not installed on host"
fi

echo "== DNP3 outstation on SS-1 (class 0 read with mine-dnp3-poll)"
for i in 1 2 3; do
  out=$($DC exec -T rtu-sub-01 mine-dnp3-poll 127.0.0.1 20000 1024 1 10 2>/dev/null)
  echo "$out" | grep -q '"ok":true' && break
  sleep 3
done
if echo "$out" | grep -q '"ok":true'; then echo "[PASS] DNP3 class 0 read: $out"; else echo "[FAIL] DNP3 read: $out"; fail=1; fi

echo "== monitor end-to-end checks (inside otmon-mine)"
$DC exec -T otmon-mine python -m otmon_mine.verify || fail=1

if [ $fail -eq 0 ]; then echo; echo "VERIFY: ALL CHECKS PASSED"; else echo; echo "VERIFY: FAILED"; fi
exit $fail
