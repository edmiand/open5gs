#!/usr/bin/env bash
# Story 1 — Identity: "the cloned agent can't act".
# A field unit is registered with MSISDN + IMEI. Before a high-risk action, ask the network which SIM and which
# device the request really comes from (flow B: 1 redirect through the device).
#
#   ./demo/story1-identity.sh                         # step-by-step (Enter between steps)
#   UE_SSH='ssh user@192.168.64.3' ./demo/story1-identity.sh   # device step over SSH, else paste the redirect URL
#   REG_IMEI=490154203237518 ./demo/story1-identity.sh          # registered IMEI differs -> match=false (cloned unit)
#   PAUSE=0 ...                                       # no pauses (scripted)
. "$(dirname "$0")/lib.sh"
PHONE="${PHONE:-+99970000001}"                  # registered MSISDN of the field unit
REG_IMEI="${REG_IMEI:-437081612581614}"          # registered IMEI (UERANSIM default IMEISV 4370816125816151)
GW_UE_URL="${GW_UE_URL:-http://10.45.0.1:8090}"
REDIRECT_URI="https://demo-app.example/callback"
urlenc() { jq -rn --arg v "$1" '$v|@uri'; }

step "0 · Registered field unit"
fact "MSISDN $PHONE  ·  IMEI $REG_IMEI"

step "1a · A cloned agent off the mobile network (Wi-Fi/VPN) tries to get a network identity"
STATE="s1-$RANDOM"
AUTHZ_PATH="/oauth2/authorize?response_type=code&client_id=demo-app&redirect_uri=$(urlenc "$REDIRECT_URI")&scope=$(urlenc 'openid number-verification:verify')&state=$STATE"
echo "▶ GET $AUTHZ_PATH   (from this VM = not the UE data path)"
loc=$(curl -s -o /dev/null -w '%{redirect_url}' "$CAMARA_URL$AUTHZ_PATH"); echo "◀ 302 → $loc"
fact "network can't identify it → $(sed -n 's/.*error=\([^&]*\).*/\1/p' <<<"$loc")  (no code, no token)"

step "1b · The real unit: its HTTP client follows the redirect over mobile data (uesimtun0)"
DEV_CMD="curl -s -o /dev/null -w '%{redirect_url}\n' --interface uesimtun0 '$GW_UE_URL$AUTHZ_PATH'"
if [[ -n ${UE_SSH:-} ]]; then echo "▶ (on the device) $DEV_CMD"; REDIR=$($UE_SSH "$DEV_CMD")
else echo "Run ON THE DEVICE (UERANSIM host):"; echo; echo "  $DEV_CMD"; echo; read -r -p "paste the redirect URL: " REDIR </dev/tty; fi
echo "◀ 302 → $REDIR"
CODE=$(sed -n 's/.*[?&]code=\([^&]*\).*/\1/p' <<<"$REDIR")
[[ -n $CODE ]] || { echo "no code: $(sed -n 's/.*error=\([^&]*\).*/\1/p' <<<"$REDIR")"; exit 1; }
fact "network identified the SIM from the data session → one-time code"

step "1c · Backend exchanges the code for a single-use token (sub = that SIM, amr = network)"
TOKEN=$(curl -fsS -u "demo-app:$(client_secret)" "$CAMARA_URL/oauth2/token" -d grant_type=authorization_code \
  --data-urlencode "code=$CODE" --data-urlencode "redirect_uri=$REDIRECT_URI" | tee /dev/stderr | jq -r .access_token); echo
cut -d. -f2 <<<"$TOKEN" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -c '{sub, amr, scope, exp}' || true

step "1d · Number Verification: is the request's SIM the registered MSISDN?"
post_json /number-verification/v2/verify "{\"phoneNumber\":\"$PHONE\"}"
NV=$(jq -r '.devicePhoneNumberVerified // "error"' <<<"$LAST"); fact "SIM verified: $NV"

TOKEN=$(token "device-identifier:match-identifier device-identifier:retrieve-identifier device-swap:check")
step "2 · Device Identifier: is the device that SIM is in right now the registered IMEI? (2-legged, by verified MSISDN)"
post_json /device-identifier/v0.4/match-identifier "{\"device\":{\"phoneNumber\":\"$PHONE\"},\"imei\":\"$REG_IMEI\"}"
DI=$(jq -r '.match // "error"' <<<"$LAST"); fact "device matches registered IMEI: $DI"
note "(what the network sees — retrieve-identifier)"
post_json /device-identifier/v0.4/retrieve-identifier "{\"device\":{\"phoneNumber\":\"$PHONE\"}}" >/dev/null
jq -c '{imei, imeisv, tac, manufacturer, model, lastChecked}' <<<"$LAST"

step "3 · Device Swap: did the device for this number change in the last 24 h?"
post_json /device-swap/v1/check "{\"phoneNumber\":\"$PHONE\",\"maxAge\":24}"
DS=$(jq -r 'if has("swapped") then .swapped else "error" end' <<<"$LAST"); fact "device swapped < 24 h: $DS"

step "Network facts for the gateway's decision"
fact "SIM verified over mobile data ........ $NV"
fact "device = registered IMEI ............. $DI"
fact "device swapped in last 24 h .......... $DS"
note "(the allow/block decision belongs to the EQTY gateway; this build provides the facts)"
