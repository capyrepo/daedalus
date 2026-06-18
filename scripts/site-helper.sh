#!/usr/bin/env bash
# Privileged helper called by the webmonitor backend via sudo.
# Installed at /usr/local/bin/webmonitor-site-helper by setup-hosting.sh.
set -euo pipefail

DOMAIN="ent3.tech"
NGINX_AVAILABLE="/etc/nginx/sites-available"
NGINX_ENABLED="/etc/nginx/sites-enabled"
WWW_DIR="/var/www"
APPS_DIR="/home/daedalus/apps"
SSL_OPTIONS="/etc/letsencrypt/options-ssl-nginx.conf"

# Locate wildcard cert (prefer ent3.tech-0001, fall back to ent3.tech)
if [[ -d /etc/letsencrypt/live/ent3.tech-0001 ]]; then
    CERT_DIR="/etc/letsencrypt/live/ent3.tech-0001"
elif [[ -d /etc/letsencrypt/live/ent3.tech ]]; then
    CERT_DIR="/etc/letsencrypt/live/ent3.tech"
else
    echo "Error: No SSL certificate found. Run setup-hosting.sh first." >&2
    exit 1
fi

validate_name() {
    local n="$1"
    if [[ ! "$n" =~ ^[a-zA-Z0-9-]{1,63}$ ]]; then
        echo "Error: Invalid name '${n}'" >&2; exit 1
    fi
}

reload_nginx() {
    nginx -t 2>&1
    systemctl reload nginx
}

CMD="${1:-}"
shift || true

case "$CMD" in

# ── create static|python|node <name> [port] ────────────────────────────────
create)
    TYPE="${1:-}"; NAME="${2:-}"; PORT="${3:-}"
    validate_name "$NAME"
    SUBDOMAIN="${NAME}.${DOMAIN}"
    CONF="${NGINX_AVAILABLE}/${SUBDOMAIN}"

    [[ -f "$CONF" ]] && { echo "Error: ${SUBDOMAIN} already exists" >&2; exit 1; }

    case "$TYPE" in
    static)
        mkdir -p "${WWW_DIR}/${SUBDOMAIN}"
        chown daedalus:www-data "${WWW_DIR}/${SUBDOMAIN}"
        if [[ ! -f "${WWW_DIR}/${SUBDOMAIN}/index.html" ]]; then
            cat > "${WWW_DIR}/${SUBDOMAIN}/index.html" <<PLACEHOLDER
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>${SUBDOMAIN}</title>
<style>body{font-family:sans-serif;padding:2rem;background:#0f172a;color:#e2e8f0}</style>
</head>
<body><h1>${SUBDOMAIN}</h1><p>Deploy files to <code>${WWW_DIR}/${SUBDOMAIN}/</code></p></body>
</html>
PLACEHOLDER
            chown daedalus:www-data "${WWW_DIR}/${SUBDOMAIN}/index.html"
        fi
        cat > "$CONF" <<NGINX
# webmonitor-type: static
server {
    listen 443 ssl;
    server_name ${SUBDOMAIN};
    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    include ${SSL_OPTIONS};
    root ${WWW_DIR}/${SUBDOMAIN};
    index index.html;
    location / { try_files \$uri \$uri/ =404; }
}
server {
    listen 80;
    server_name ${SUBDOMAIN};
    return 301 https://\$host\$request_uri;
}
NGINX
        ;;

    python|node)
        [[ -z "$PORT" ]] && { echo "Error: port required" >&2; exit 1; }
        [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )) && \
            { echo "Error: port must be 1024–65535" >&2; exit 1; }

        mkdir -p "${APPS_DIR}/${NAME}"
        chown daedalus:daedalus "${APPS_DIR}/${NAME}"

        SERVICE="/etc/systemd/system/${NAME}.service"
        if [[ ! -f "$SERVICE" ]]; then
            if [[ "$TYPE" == "python" ]]; then
                EXEC="/home/daedalus/.local/bin/uvicorn main:app --host 127.0.0.1 --port ${PORT}"
                if [[ ! -f "${APPS_DIR}/${NAME}/main.py" ]]; then
                    cat > "${APPS_DIR}/${NAME}/main.py" <<'PY'
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello from FastAPI"}
PY
                    chown daedalus:daedalus "${APPS_DIR}/${NAME}/main.py"
                fi
            else
                NODE_BIN="$(command -v node 2>/dev/null || command -v bun 2>/dev/null || echo /usr/bin/node)"
                EXEC="${NODE_BIN} server.js"
                if [[ ! -f "${APPS_DIR}/${NAME}/server.js" ]]; then
                    cat > "${APPS_DIR}/${NAME}/server.js" <<JS
const http = require('http');
http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ message: 'Hello from Node.js' }));
}).listen(${PORT}, '127.0.0.1');
JS
                    chown daedalus:daedalus "${APPS_DIR}/${NAME}/server.js"
                fi
            fi
            cat > "$SERVICE" <<SVC
[Unit]
Description=${NAME} app
After=network.target

[Service]
User=daedalus
WorkingDirectory=${APPS_DIR}/${NAME}
ExecStart=${EXEC}
Restart=always
RestartSec=5
Environment=PORT=${PORT}

[Install]
WantedBy=multi-user.target
SVC
            systemctl daemon-reload
            systemctl enable "${NAME}.service"
            systemctl start "${NAME}.service"
        fi

        cat > "$CONF" <<NGINX
# webmonitor-type: ${TYPE}
# webmonitor-port: ${PORT}
server {
    listen 443 ssl;
    server_name ${SUBDOMAIN};
    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    include ${SSL_OPTIONS};
    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    location / { proxy_pass http://127.0.0.1:${PORT}; }
}
server {
    listen 80;
    server_name ${SUBDOMAIN};
    return 301 https://\$host\$request_uri;
}
NGINX
        ;;
    *)
        echo "Error: unknown type '${TYPE}'" >&2; exit 1 ;;
    esac

    ln -sf "$CONF" "${NGINX_ENABLED}/${SUBDOMAIN}"
    reload_nginx
    echo "Created ${SUBDOMAIN}"
    ;;

# ── delete <name> ──────────────────────────────────────────────────────────
delete)
    NAME="${1:-}"
    validate_name "$NAME"
    SUBDOMAIN="${NAME}.${DOMAIN}"
    CONF="${NGINX_AVAILABLE}/${SUBDOMAIN}"

    [[ ! -f "$CONF" ]] && { echo "Error: ${SUBDOMAIN} not found" >&2; exit 1; }

    TYPE=$(grep -oP '(?<=#\s*webmonitor-type:\s)\S+' "$CONF" 2>/dev/null || echo "static")
    rm -f "${NGINX_ENABLED}/${SUBDOMAIN}" "$CONF"

    if [[ "$TYPE" == "python" || "$TYPE" == "node" ]]; then
        systemctl stop  "${NAME}.service" 2>/dev/null || true
        systemctl disable "${NAME}.service" 2>/dev/null || true
        rm -f "/etc/systemd/system/${NAME}.service"
        systemctl daemon-reload
    fi

    reload_nginx
    echo "Deleted ${SUBDOMAIN}"
    ;;

# ── enable <name> ──────────────────────────────────────────────────────────
enable)
    NAME="${1:-}"
    validate_name "$NAME"
    SUBDOMAIN="${NAME}.${DOMAIN}"
    CONF="${NGINX_AVAILABLE}/${SUBDOMAIN}"

    [[ ! -f "$CONF" ]] && { echo "Error: ${SUBDOMAIN} not found" >&2; exit 1; }
    ln -sf "$CONF" "${NGINX_ENABLED}/${SUBDOMAIN}"
    reload_nginx
    echo "Enabled ${SUBDOMAIN}"
    ;;

# ── disable <name> ─────────────────────────────────────────────────────────
disable)
    NAME="${1:-}"
    validate_name "$NAME"
    SUBDOMAIN="${NAME}.${DOMAIN}"

    [[ ! -L "${NGINX_ENABLED}/${SUBDOMAIN}" ]] && \
        { echo "Error: ${SUBDOMAIN} is not enabled" >&2; exit 1; }
    rm -f "${NGINX_ENABLED}/${SUBDOMAIN}"
    reload_nginx
    echo "Disabled ${SUBDOMAIN}"
    ;;

*)
    echo "Usage: webmonitor-site-helper create|delete|enable|disable [args…]" >&2
    exit 1 ;;
esac
