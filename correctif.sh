#!/bin/bash
# ─────────────────────────────────────────────────────────
#  VPS Admin Panel — Script de correctif
#  Corrige l'installation existante (préfixe Nginx, .env)
#  Usage : bash correctif.sh
# ─────────────────────────────────────────────────────────

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $1"; }
info() { echo -e "${BLUE}→${RESET} $1"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $1"; }
fail() { echo -e "${RED}✗ ERREUR :${RESET} $1"; exit 1; }

echo -e "\n${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${BOLD}   VPS Admin Panel — Correctif${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}\n"

[[ $EUID -ne 0 ]] && fail "Ce script doit être exécuté en root"

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$INSTALL_DIR/.env"

# ── 1. Corriger le .env ───────────────────────────────────
echo -e "${BOLD}[1/4] Correction du .env${RESET}"
[[ ! -f "$ENV_FILE" ]] && fail ".env introuvable dans $INSTALL_DIR"

# Lire la valeur actuelle
CURRENT_BASE=$(grep "^PANEL_BASE_PATH=" "$ENV_FILE" | cut -d= -f2)
info "Valeur actuelle : PANEL_BASE_PATH=${CURRENT_BASE}"

# Ajouter le / si manquant
if [[ -n "$CURRENT_BASE" && "$CURRENT_BASE" != /* ]]; then
    sed -i "s|^PANEL_BASE_PATH=.*|PANEL_BASE_PATH=/${CURRENT_BASE}|" "$ENV_FILE"
    CURRENT_BASE="/${CURRENT_BASE}"
    ok "Préfixe corrigé → PANEL_BASE_PATH=${CURRENT_BASE}"
else
    ok "Préfixe déjà correct (${CURRENT_BASE})"
fi

source "$ENV_FILE"
BASE_PATH="${PANEL_BASE_PATH:-/panel_admin}"
PORT="${PORT:-9999}"

# ── 2. Générer le snippet Nginx corrigé ───────────────────
echo -e "\n${BOLD}[2/4] Snippet Nginx${RESET}"
mkdir -p /etc/nginx/snippets
NGINX_SNIPPET="/etc/nginx/snippets/vps-panel.conf"

cat > "$NGINX_SNIPPET" << EOF
# VPS Admin Panel
location = ${BASE_PATH} {
    return 301 ${BASE_PATH}/;
}
location ${BASE_PATH}/ {
    proxy_pass         http://127.0.0.1:${PORT}/;
    proxy_set_header   Host               \$host;
    proxy_set_header   X-Real-IP          \$remote_addr;
    proxy_set_header   X-Forwarded-For    \$proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto  \$scheme;
    proxy_set_header   X-Forwarded-Prefix ${BASE_PATH};
}
EOF
ok "Snippet écrit dans $NGINX_SNIPPET"

# ── 3. Injecter l'include dans la config du domaine ───────
echo -e "\n${BOLD}[3/4] Config Nginx du domaine${RESET}"

# Trouver la config du domaine automatiquement
DOMAIN_CONF=""
for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
    if [[ -f "$f" ]] && grep -q "i-tracker\|support\." "$f" 2>/dev/null; then
        DOMAIN_CONF="$f"
        break
    fi
done

# Si pas trouvé, lister et demander
if [[ -z "$DOMAIN_CONF" ]]; then
    echo -e "  Configs disponibles :"
    ls /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^/    /'
    read -rp "  Nom du fichier de config : /etc/nginx/sites-enabled/" CONF_NAME
    DOMAIN_CONF="/etc/nginx/sites-enabled/${CONF_NAME}"
fi

[[ ! -f "$DOMAIN_CONF" ]] && fail "Fichier introuvable : $DOMAIN_CONF"
info "Config utilisée : $DOMAIN_CONF"

# Backup
cp "$DOMAIN_CONF" "${DOMAIN_CONF}.bak.$(date +%Y%m%d%H%M%S)"
ok "Backup créé"

# Vérifier si include déjà présent
if grep -q "vps-panel.conf\|location ${BASE_PATH}" "$DOMAIN_CONF"; then
    warn "Bloc déjà présent — mise à jour du snippet uniquement"
else
    # Insérer l'include avant le dernier } du premier server{}
    python3 - "$DOMAIN_CONF" "$NGINX_SNIPPET" << 'PYEOF'
import sys, re

conf_path = sys.argv[1]
snippet_path = sys.argv[2]

with open(conf_path, 'r') as f:
    content = f.read()

include_line = f'\n    include {snippet_path};\n'

# Trouver la position du dernier } dans le premier bloc server{}
depth = 0
in_server = False
insert_pos = -1

for i, ch in enumerate(content):
    if not in_server and content[i:i+6] == 'server':
        in_server = True
    if in_server:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                insert_pos = i
                break

if insert_pos == -1:
    print("ERREUR: bloc server{} introuvable")
    sys.exit(1)

new_content = content[:insert_pos] + include_line + content[insert_pos:]
with open(conf_path, 'w') as f:
    f.write(new_content)

print("OK")
PYEOF
    ok "include injecté dans $DOMAIN_CONF"
fi

# ── 4. Tester et recharger ────────────────────────────────
echo -e "\n${BOLD}[4/4] Test et rechargement${RESET}"

if nginx -t 2>&1; then
    systemctl reload nginx
    ok "Nginx rechargé"
    systemctl restart vps-panel
    ok "Service vps-panel redémarré"
else
    fail "nginx -t a échoué — restaurez avec : cp ${DOMAIN_CONF}.bak.* $DOMAIN_CONF"
fi

# ── Résumé ────────────────────────────────────────────────
DOMAIN=$(grep -oP 'server_name\s+\K\S+' "$DOMAIN_CONF" 2>/dev/null | head -1 || echo "VOTRE_DOMAINE")
echo -e "\n${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Correctif appliqué !${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "  URL    : ${BOLD}https://${DOMAIN}${BASE_PATH}${RESET}"
echo -e "  Logs   : ${BOLD}journalctl -u vps-panel -f${RESET}"
echo -e "  Status : ${BOLD}systemctl status vps-panel${RESET}"
echo ""
