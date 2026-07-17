#!/bin/bash
# ─────────────────────────────────────────────────────────
#  VPS Admin Panel — Script de déploiement automatique
#  Usage : bash deploy.sh
# ─────────────────────────────────────────────────────────

set -e

# ── Couleurs ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $1"; }
info() { echo -e "${BLUE}→${RESET} $1"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $1"; }
fail() { echo -e "${RED}✗ ERREUR :${RESET} $1"; exit 1; }

echo -e "\n${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${BOLD}   VPS Admin Panel — Installation${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}\n"

# ── Vérifications préalables ──────────────────────────────
[[ $EUID -ne 0 ]] && fail "Ce script doit être exécuté en root (sudo bash deploy.sh)"
command -v python3 &>/dev/null || fail "python3 non trouvé — apt install python3"
command -v nginx   &>/dev/null || warn "nginx non trouvé — le bloc Nginx ne sera pas configuré"

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
info "Répertoire d'installation : ${BOLD}$INSTALL_DIR${RESET}"

# ── 1. Virtualenv + dépendances ───────────────────────────
echo -e "\n${BOLD}[1/5] Environnement Python${RESET}"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    info "Création du virtualenv..."
    python3 -m venv "$INSTALL_DIR/venv"
fi
info "Installation des dépendances..."
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
ok "Dépendances installées"

# ── 2. Fichier .env ───────────────────────────────────────
echo -e "\n${BOLD}[2/5] Configuration${RESET}"
if [[ -f "$INSTALL_DIR/.env" ]]; then
    warn ".env déjà existant — conservé sans modification"
else
    info "Génération du fichier .env..."

    read -rp "  Identifiant admin [admin] : " PANEL_USER
    PANEL_USER="${PANEL_USER:-admin}"

    while true; do
        read -rsp "  Mot de passe admin : " PANEL_PASS; echo
        [[ -n "$PANEL_PASS" ]] && break
        warn "Le mot de passe ne peut pas être vide"
    done

    read -rp "  Préfixe URL [/panel_admin] : " BASE_PATH
    BASE_PATH="${BASE_PATH:-/panel_admin}"
    # Forcer le slash initial, supprimer le slash final
    [[ "$BASE_PATH" != /* ]] && BASE_PATH="/$BASE_PATH"
    BASE_PATH="${BASE_PATH%/}"

    read -rp "  Port interne [9999] : " PORT
    PORT="${PORT:-9999}"

    SECRET=$("$INSTALL_DIR/venv/bin/python3" -c "import secrets; print(secrets.token_hex(32))")

    cat > "$INSTALL_DIR/.env" << EOF
PANEL_USERNAME=${PANEL_USER}
PANEL_PASSWORD=${PANEL_PASS}
SESSION_SECRET=${SECRET}
PANEL_BASE_PATH=${BASE_PATH}
PORT=${PORT}
EOF
    chmod 600 "$INSTALL_DIR/.env"
    ok ".env créé (chmod 600)"
fi

# Lire les valeurs pour la suite
source "$INSTALL_DIR/.env"
PORT="${PORT:-9999}"
BASE_PATH="${PANEL_BASE_PATH:-/panel_admin}"

# ── 3. Service systemd ────────────────────────────────────
echo -e "\n${BOLD}[3/5] Service systemd${RESET}"
SERVICE_FILE="/etc/systemd/system/vps-panel.service"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=VPS Admin Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment="PATH=${INSTALL_DIR}/venv/bin"
ExecStart=${INSTALL_DIR}/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:${PORT} --timeout 60 app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vps-panel

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vps-panel
systemctl restart vps-panel
sleep 2

if systemctl is-active --quiet vps-panel; then
    ok "Service vps-panel actif sur le port ${PORT}"
else
    fail "Le service n'a pas démarré — vérifiez : journalctl -u vps-panel -n 30"
fi

# ── 4. Bloc Nginx ─────────────────────────────────────────
echo -e "\n${BOLD}[4/5] Nginx${RESET}"
if command -v nginx &>/dev/null; then
    NGINX_SNIPPET="/etc/nginx/conf.d/vps-panel.conf"
    # Cherche la config du domaine existant
    read -rp "  Nom de domaine Nginx (ex: support.i-tracker.online) : " DOMAIN

    DOMAIN_CONF=""
    for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
        [[ -f "$f" ]] && grep -q "$DOMAIN" "$f" 2>/dev/null && DOMAIN_CONF="$f" && break
    done

    # Générer le bloc location
    LOCATION_BLOCK="
    # VPS Admin Panel
    location = ${BASE_PATH} { return 301 ${BASE_PATH}/; }
    location ${BASE_PATH}/ {
        proxy_pass         http://127.0.0.1:${PORT}/;
        proxy_set_header   Host               \$host;
        proxy_set_header   X-Real-IP          \$remote_addr;
        proxy_set_header   X-Forwarded-For    \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto  \$scheme;
        proxy_set_header   X-Forwarded-Prefix ${BASE_PATH};
    }"

    if [[ -n "$DOMAIN_CONF" ]]; then
        info "Config trouvée : $DOMAIN_CONF"
        # Vérifier si le bloc est déjà présent
        if grep -q "location ${BASE_PATH}" "$DOMAIN_CONF"; then
            warn "Bloc location ${BASE_PATH} déjà présent dans $DOMAIN_CONF — conservé"
        else
            # Injecter avant le dernier } du premier bloc server{}
            cp "$DOMAIN_CONF" "${DOMAIN_CONF}.bak"
            # Insérer le bloc juste avant la dernière accolade fermante
            sed -i "0,/^}/s/^}/${LOCATION_BLOCK}\n}/" "$DOMAIN_CONF"
            ok "Bloc injecté dans $DOMAIN_CONF (backup : ${DOMAIN_CONF}.bak)"
        fi
    else
        warn "Config domaine non trouvée. Snippet créé dans $NGINX_SNIPPET"
        cat > "$NGINX_SNIPPET" << EOF
# VPS Admin Panel — bloc à inclure dans votre server {}
${LOCATION_BLOCK}
EOF
        warn "Incluez-le dans votre server{} avec : include $NGINX_SNIPPET;"
    fi

    echo -e "\n${YELLOW}  Bloc Nginx :${RESET}"
    echo "$LOCATION_BLOCK"
    echo ""

    if nginx -t 2>/dev/null; then
        systemctl reload nginx
        ok "Nginx rechargé"
    else
        warn "nginx -t a échoué — vérifiez $DOMAIN_CONF puis : nginx -t && systemctl reload nginx"
        [[ -f "${DOMAIN_CONF}.bak" ]] && warn "Restauration possible : cp ${DOMAIN_CONF}.bak $DOMAIN_CONF"
    fi
else
    warn "Nginx non installé — bloc Nginx ignoré"
fi

# ── 5. Sudoers ────────────────────────────────────────────
echo -e "\n${BOLD}[5/5] Sudoers${RESET}"
SUDOERS_FILE="/etc/sudoers.d/vps-panel"
cat > "$SUDOERS_FILE" << 'EOF'
# VPS Admin Panel — commandes autorisées sans mot de passe
www-data ALL=(ALL) NOPASSWD: /bin/systemctl
www-data ALL=(ALL) NOPASSWD: /usr/sbin/nginx
root ALL=(ALL) NOPASSWD: /bin/systemctl
root ALL=(ALL) NOPASSWD: /usr/sbin/nginx
EOF
chmod 440 "$SUDOERS_FILE"
ok "Sudoers configuré ($SUDOERS_FILE)"

# ── Résumé ────────────────────────────────────────────────
echo -e "\n${BOLD}═══════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Installation terminée !${RESET}"
echo -e "${BOLD}═══════════════════════════════════════${RESET}"
echo -e "  Service  : ${BOLD}systemctl status vps-panel${RESET}"
echo -e "  Logs     : ${BOLD}journalctl -u vps-panel -f${RESET}"
echo -e "  URL      : ${BOLD}https://${DOMAIN:-VOTRE_DOMAINE}${BASE_PATH}${RESET}"
echo -e "  Mise à jour : ${BOLD}git pull origin main && systemctl restart vps-panel${RESET}"
echo ""
