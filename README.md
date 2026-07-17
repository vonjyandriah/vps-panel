# VPS Admin Panel

Mini-PaaS / cPanel privé en Flask pour gérer un VPS Debian sans jamais faire de SSH manuel.

## Fonctionnalités

- **Dashboard** — CPU, RAM, disque, réseau, uptime en temps réel
- **Services** — liste, start/stop/restart/enable/disable, logs journalctl
- **Déployer** — génère le `.service` systemd + conf Nginx automatiquement
- **Domaines** — liste les domaines Nginx, SSL, apps déployées, reload
- **Importer** — détecte les apps gunicorn existantes et les normalise au standard
- **Outils** — git clone/pull, upload de fichiers, configuration sudoers

## Prérequis

- Debian/Ubuntu avec `python3`, `pip`, `git`, `nginx`, `systemd`
- Accès root (ou sudoers configuré)

```bash
apt update && apt install -y python3 python3-pip python3-venv git nginx
```

## Installation

### 1. Cloner le repo

```bash
git clone https://github.com/vonjyandriah/vps-panel.git /opt/panel
cd /opt/panel
```

### 2. Créer le virtualenv et installer les dépendances

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Créer le fichier `.env`

```bash
cat > /opt/panel/.env << EOF
PANEL_USERNAME=admin
PANEL_PASSWORD=MotDePasseSolide!
SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
PANEL_BASE_PATH=/panel_admin
PORT=9999
EOF
chmod 600 /opt/panel/.env
```

### 4. Créer le service systemd

```bash
cat > /etc/systemd/system/vps-panel.service << 'EOF'
[Unit]
Description=VPS Admin Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/panel
EnvironmentFile=/opt/panel/.env
Environment="PATH=/opt/panel/venv/bin"
ExecStart=/opt/panel/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:9999 --timeout 60 app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vps-panel
systemctl status vps-panel
```

### 5. Configurer Nginx

Ajoutez ce bloc dans la config de votre domaine (ex: `/etc/nginx/sites-available/support.i-tracker.online`) :

```nginx
location /panel_admin {
    proxy_pass         http://127.0.0.1:9999;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_set_header   X-Forwarded-Prefix /panel_admin;
}
```

```bash
nginx -t && systemctl reload nginx
```

### 6. Premier démarrage

Ouvrez `https://support.i-tracker.online/panel_admin` et connectez-vous avec les identifiants du `.env`.

Dans la sidebar, cliquez **Outils → Config Sudoers auto** pour éviter tout `sudo` manuel ensuite.

## Mise à jour

```bash
cd /opt/panel
git pull origin main
systemctl restart vps-panel
```

## Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `PANEL_USERNAME` | Identifiant de connexion | `admin` |
| `PANEL_PASSWORD` | Mot de passe | `admin` |
| `SESSION_SECRET` | Clé secrète Flask (à générer) | aléatoire |
| `PANEL_BASE_PATH` | Préfixe URL (ex: `/panel_admin`) | vide |
| `PORT` | Port d'écoute gunicorn | `5000` |

## Sécurité

- Ne pas exposer le port 9999 directement — passer toujours par Nginx
- Restreindre l'accès par IP dans Nginx : `allow VOTRE_IP; deny all;`
- Changer les identifiants par défaut `admin`/`admin`
- Le panel tourne en root pour écrire dans `/etc/systemd/system/` et `/etc/nginx/`
