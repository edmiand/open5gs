#!/usr/bin/env bash
# Phase 3: Number Verification with network-based authentication.
#
# Step 1 runs ON THE DEVICE (UERANSIM host), over the UE data path, so the gateway sees the UE IP:
#     curl -s -o /dev/null -w '%{redirect_url}\n' --interface uesimtun0 '<authorize URL printed below>'
#   It prints a redirect URL containing ?code=...  (the network identified the device, no user interaction)
# Step 2+ run on the app backend (this VM): exchange the code, call the API (single-use token).
# (PKCE S256 is supported by the gateway; the demo backend is a confidential client and omits it.)
#
#   ./demo/03-number-verification.sh                       # prints the device command, then asks for the redirect URL
#   ./demo/03-number-verification.sh '<redirect URL>'      # continue with a redirect URL from the device
#   UE_SSH='ssh user@ueransim-host' ./demo/03-number-verification.sh   # run the device step automatically
#   PHONE=+99970000001 MODE=verify|share|hashed
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"
MODE="${MODE:-verify}"
GW_UE_URL="${GW_UE_URL:-http://10.45.0.1:8090}"      # gateway as reached from the UE data path
REDIRECT_URI="${REDIRECT_URI:-https://demo-app.example/callback}"
case $MODE in
  share) SCOPE="openid number-verification:device-phone-number:read" ;;
  *)     SCOPE="openid number-verification:verify" ;;
esac
STATE="demo-$RANDOM"
urlenc() { jq -rn --arg v "$1" '$v|@uri'; }
AUTHZ="$GW_UE_URL/oauth2/authorize?response_type=code&client_id=demo-app&redirect_uri=$(urlenc "$REDIRECT_URI")&scope=$(urlenc "$SCOPE")&state=$STATE"

echo "== off-network attempt (from this VM, not the UE path) -> must be denied"
curl -s -o /dev/null -w '  %{http_code} -> %{redirect_url}\n' "${CAMARA_URL}/oauth2/authorize?response_type=code&client_id=demo-app&redirect_uri=$(urlenc "$REDIRECT_URI")&scope=$(urlenc "$SCOPE")&state=$STATE"

DEV_CMD="curl -s -o /dev/null -w '%{redirect_url}\n' --interface uesimtun0 '$AUTHZ'"
if [[ -n ${1:-} ]]; then
  REDIR="$1"
elif [[ -n ${UE_SSH:-} ]]; then
  echo "== device step via $UE_SSH"; REDIR=$($UE_SSH "$DEV_CMD")
else
  echo "== run this ON THE DEVICE (UERANSIM host):"; echo; echo "  $DEV_CMD"; echo
  read -r -p "paste the redirect URL it printed: " REDIR
fi
echo "  redirect: $REDIR"
[[ $REDIR == *"state=$STATE"* || -n ${1:-} ]] || echo "  (warning: state mismatch — expected $STATE)"
CODE=$(sed -n 's/.*[?&]code=\([^&]*\).*/\1/p' <<<"$REDIR")
[[ -n $CODE ]] || { echo "no code in redirect (error: $(sed -n 's/.*[?&]error=\([^&]*\).*/\1/p' <<<"$REDIR"))"; exit 1; }

echo "== backend: exchange code for a single-use token"
tok=$(curl -fsS -u "demo-app:$(client_secret)" "$CAMARA_URL/oauth2/token" -d grant_type=authorization_code \
      --data-urlencode "code=$CODE" --data-urlencode "redirect_uri=$REDIRECT_URI") \
  || { echo "token exchange failed (codes are single-use and expire after 60 s)"; exit 1; }
echo "$tok" | jq '{token_type, expires_in, scope}'
TOKEN=$(jq -r .access_token <<<"$tok")
echo "  access token claims:"; cut -d. -f2 <<<"$TOKEN" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -c '{sub, amr, scope, exp}' || true

case $MODE in
  share)  call GET /number-verification/v2/device-phone-number ;;
  hashed) H=$(printf '%s' "$PHONE" | sha256sum | cut -d' ' -f1); call POST /number-verification/v2/verify "{\"hashedPhoneNumber\":\"$H\"}" ;;
  *)      call POST /number-verification/v2/verify "{\"phoneNumber\":\"$PHONE\"}" ;;
esac
echo "== same token again (single-use) -> 401"
call POST /number-verification/v2/verify "{\"phoneNumber\":\"$PHONE\"}"
