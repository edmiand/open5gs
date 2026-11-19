# Shared helpers for demo scripts. Source me.
set -euo pipefail
CAMARA_URL="${CAMARA_URL:-http://localhost:8090}"
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="${CAMARA_CONFIG:-$_HERE/config.yaml}"
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

# client secret for demo-app comes from config.yaml (never hard-coded)
client_secret() {
    "$_HERE/.venv/bin/python" -c "import yaml,sys;c=yaml.safe_load(open(sys.argv[1]));print(next(x['client_secret'] for x in c['auth']['clients'] if x['client_id']=='demo-app'))" "$CONF"
}

# token [scopes...]  -> prints access token
token() {
    local scope="${*:-}"
    curl -fsS -u "demo-app:$(client_secret)" -d grant_type=client_credentials ${scope:+-d "scope=$scope"} \
        "$CAMARA_URL/oauth2/token" | jq -r .access_token
}

# call METHOD PATH [JSON]  (uses $TOKEN; prints status + pretty body)
call() {
    local m=$1 p=$2 body=${3:-}
    echo "▶ $m $p ${body:+$body}" >&2
    curl -sS -X "$m" "$CAMARA_URL$p" -H "Authorization: Bearer ${TOKEN:-}" \
        -H "x-correlator: demo-$(date +%s%N)" ${body:+-H 'content-type: application/json' -d "$body"} \
        -w '\n◀ HTTP %{http_code}\n' | { read -r -d '' out || true; echo "$out" | sed '$d' | jq . 2>/dev/null || echo "$out" | sed '$d'; echo "$out" | tail -1; }
}
