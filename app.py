"""
VPS Admin Panel — Mini-PaaS / cPanel privé
Flask backend avec monitoring système, gestion systemd et déploiement automatique.
"""

import os
import re
import secrets
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psutil
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET_KEY", secrets.token_hex(32))

# Détection de l'environnement VPS réel
IS_VPS = Path("/etc/systemd/system").exists()
SYSTEMD_DIR = Path("/etc/systemd/system")
NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")


# ─────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────

def bytes_to_human(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} Po"


def get_uptime() -> str:
    try:
        boot = psutil.boot_time()
        delta = datetime.now().timestamp() - boot
        days = int(delta // 86400)
        hours = int((delta % 86400) // 3600)
        mins = int((delta % 3600) // 60)
        if days:
            return f"{days}j {hours}h {mins}m"
        if hours:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except Exception:
        return "N/A"


def run_cmd(cmd: list[str], check: bool = False) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Timeout"
    except FileNotFoundError:
        return 1, "", f"Commande introuvable : {cmd[0]}"


def find_free_port(start: int = 8100, end: int = 9000) -> int:
    used = {c.laddr.port for c in psutil.net_connections() if c.status == "LISTEN"}
    for port in range(start, end):
        if port not in used:
            return port
    return start


# ─────────────────────────────────────────────────────────
#  DÉMO (hors VPS)
# ─────────────────────────────────────────────────────────

DEMO_SERVICES = [
    {"name": "gps-fleet-manager", "description": "GPS Fleet Manager - Application Flask",
     "status": "active", "sub_status": "running", "active_since": "2 jours"},
    {"name": "gps-offline-tracker", "description": "GPS Offline Tracker - Device Inactivity Monitor",
     "status": "active", "sub_status": "running", "active_since": "2 jours"},
    {"name": "odometer", "description": "Odometer SaaS",
     "status": "active", "sub_status": "running", "active_since": "5 heures"},
    {"name": "money-manager", "description": "Flask money-manager",
     "status": "inactive", "sub_status": "dead", "active_since": "-"},
    {"name": "gps-swap-manager", "description": "GPS Swap Manager",
     "status": "active", "sub_status": "running", "active_since": "1 jour"},
    {"name": "i-tracker-backend", "description": "i-Tracker Backend API (Flask + Gunicorn)",
     "status": "failed", "sub_status": "failed", "active_since": "-"},
    {"name": "gps-fleet-backup", "description": "GPS Fleet Manager Database Backup",
     "status": "inactive", "sub_status": "dead", "active_since": "-"},
    {"name": "nginx", "description": "A high performance web server",
     "status": "active", "sub_status": "running", "active_since": "3 jours"},
    {"name": "postgresql", "description": "PostgreSQL RDBMS",
     "status": "active", "sub_status": "running", "active_since": "3 jours"},
    {"name": "ssh", "description": "OpenBSD Secure Shell server",
     "status": "active", "sub_status": "running", "active_since": "3 jours"},
]


# ─────────────────────────────────────────────────────────
#  ROUTES — PAGES
# ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    demo_mode = not IS_VPS
    return render_template("index.html", demo_mode=demo_mode)


# ─────────────────────────────────────────────────────────
#  API — MONITORING SYSTÈME
# ─────────────────────────────────────────────────────────

@app.route("/api/system-stats")
def system_stats():
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()

    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    # Températures (si disponibles)
    temps = {}
    try:
        raw_temps = psutil.sensors_temperatures()
        if raw_temps:
            for name, entries in raw_temps.items():
                if entries:
                    temps[name] = entries[0].current
    except (AttributeError, Exception):
        pass

    # Processus les plus gourmands
    top_procs = []
    try:
        procs = sorted(
            [p.info for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"])
             if p.info["cpu_percent"] is not None],
            key=lambda x: x["cpu_percent"],
            reverse=True,
        )[:5]
        top_procs = [
            {"pid": p["pid"], "name": p["name"],
             "cpu": round(p["cpu_percent"], 1),
             "mem": round(p["memory_percent"], 1)}
            for p in procs
        ]
    except Exception:
        pass

    return jsonify({
        "cpu": {
            "percent": cpu_percent,
            "cores": cpu_count,
            "freq_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
        },
        "ram": {
            "percent": ram.percent,
            "used": ram.used,
            "total": ram.total,
            "available": ram.available,
            "used_human": bytes_to_human(ram.used),
            "total_human": bytes_to_human(ram.total),
        },
        "disk": {
            "percent": disk.percent,
            "used": disk.used,
            "free": disk.free,
            "total": disk.total,
            "used_human": bytes_to_human(disk.used),
            "total_human": bytes_to_human(disk.total),
            "free_human": bytes_to_human(disk.free),
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "sent_human": bytes_to_human(net.bytes_sent),
            "recv_human": bytes_to_human(net.bytes_recv),
        },
        "uptime": get_uptime(),
        "temps": temps,
        "top_procs": top_procs,
        "timestamp": datetime.now().isoformat(),
    })


# ─────────────────────────────────────────────────────────
#  API — SERVICES SYSTEMD
# ─────────────────────────────────────────────────────────

@app.route("/api/services")
def list_services():
    if not IS_VPS:
        return jsonify({"services": DEMO_SERVICES, "demo": True})

    rc, out, err = run_cmd([
        "systemctl", "list-units", "--type=service", "--all",
        "--no-pager", "--no-legend"
    ])

    services = []
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4:
                name = parts[0].replace(".service", "")
                load, active, sub = parts[1], parts[2], parts[3]
                desc = parts[4] if len(parts) > 4 else ""

                # Récupérer ActiveEnterTimestamp
                _, ts_out, _ = run_cmd(["systemctl", "show", f"{name}.service",
                                        "--property=ActiveEnterTimestamp", "--value"])
                services.append({
                    "name": name,
                    "description": desc,
                    "status": active,
                    "sub_status": sub,
                    "load": load,
                    "active_since": ts_out or "-",
                })

    return jsonify({"services": services, "demo": False})


@app.route("/api/services/<name>/<action>", methods=["POST"])
def service_action(name: str, action: str):
    if action not in ("start", "stop", "restart", "enable", "disable"):
        return jsonify({"success": False, "error": "Action non autorisée"}), 400

    # Sécurité : n'autoriser que des noms alphanumériques + tirets
    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return jsonify({"success": False, "error": "Nom de service invalide"}), 400

    if not IS_VPS:
        return jsonify({
            "success": True,
            "message": f"[DÉMO] systemctl {action} {name}.service",
            "demo": True,
        })

    rc, out, err = run_cmd(["systemctl", action, f"{name}.service"])
    return jsonify({
        "success": rc == 0,
        "message": out or err or f"Action {action} exécutée",
        "returncode": rc,
    })


@app.route("/api/services/<name>/logs")
def service_logs(name: str):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return jsonify({"success": False, "error": "Nom de service invalide"}), 400

    lines = request.args.get("lines", "100")
    try:
        lines = min(int(lines), 500)
    except ValueError:
        lines = 100

    if not IS_VPS:
        ts = datetime.now().strftime("%b %d %H:%M:%S")
        demo_logs = "\n".join([
            f"{ts} vps {name}[1234]: [DÉMO] Démarrage du service {name}",
            f"{ts} vps {name}[1234]: [DÉMO] Connexion à la base de données... OK",
            f"{ts} vps {name}[1234]: [DÉMO] Gunicorn arbiter booted",
            f"{ts} vps {name}[1234]: [DÉMO] Listening at: http://127.0.0.1:8000",
            f"{ts} vps {name}[1234]: [DÉMO] Worker booted (pid: 5678)",
            f"{ts} vps {name}[1234]: [DÉMO] GET /api/healthz HTTP/1.1 200 OK",
            f"{ts} vps {name}[1234]: [DÉMO] Logs en temps réel disponibles sur VPS réel",
        ])
        return jsonify({"success": True, "logs": demo_logs, "demo": True})

    rc, out, err = run_cmd([
        "journalctl", "-u", f"{name}.service",
        "-n", str(lines), "--no-pager", "--output=short"
    ])
    return jsonify({
        "success": rc == 0,
        "logs": out if rc == 0 else err,
        "demo": False,
    })


@app.route("/api/services/<name>/status")
def service_status(name: str):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return jsonify({"success": False, "error": "Nom de service invalide"}), 400

    if not IS_VPS:
        svc = next((s for s in DEMO_SERVICES if s["name"] == name), None)
        if svc:
            return jsonify({"success": True, "status": svc["status"],
                            "sub_status": svc["sub_status"], "demo": True})
        return jsonify({"success": False, "error": "Service non trouvé"}), 404

    rc, out, err = run_cmd(["systemctl", "show", f"{name}.service",
                            "--property=ActiveState,SubState", "--value"])
    parts = out.splitlines()
    return jsonify({
        "success": True,
        "status": parts[0] if len(parts) > 0 else "unknown",
        "sub_status": parts[1] if len(parts) > 1 else "unknown",
    })


# ─────────────────────────────────────────────────────────
#  API — PORTS DISPONIBLES
# ─────────────────────────────────────────────────────────

@app.route("/api/ports/scan")
def scan_ports():
    free_port = find_free_port()
    used_ports = sorted(
        {c.laddr.port for c in psutil.net_connections() if c.status == "LISTEN"}
    )
    return jsonify({
        "suggested_port": free_port,
        "listening_ports": used_ports[:50],  # limiter la réponse
    })


# ─────────────────────────────────────────────────────────
#  API — DÉPLOIEMENT
# ─────────────────────────────────────────────────────────

def generate_service_file(
    app_name: str,
    project_path: str,
    port: int,
    workers: int = 3,
    wsgi_module: str = "wsgi:app",
) -> str:
    secret_key = secrets.token_urlsafe(32)
    return f"""[Unit]
Description=Service Automatise - {app_name}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={project_path}
Environment="PATH={project_path}/venv/bin"
Environment="FLASK_SECRET_KEY={secret_key}"
ExecStart={project_path}/venv/bin/gunicorn --workers {workers} --bind 127.0.0.1:{port} --timeout 120 --access-logfile - --error-logfile - {wsgi_module}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier={app_name}

[Install]
WantedBy=multi-user.target
"""


def generate_nginx_location_block(app_name: str, port: int, routing_type: str) -> str:
    if routing_type == "subdomain":
        return f"""# Configuration pour sous-domaine dédié
location / {{
    proxy_pass http://127.0.0.1:{port};
    proxy_redirect off;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}}
"""
    else:
        # sous-dossier
        return f"""location /{app_name} {{
    return 301 /{app_name}/;
}}

location /{app_name}/ {{
    rewrite ^/{app_name}/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:{port};
    proxy_redirect off;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /{app_name};
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}}
"""


@app.route("/api/deploy", methods=["POST"])
def deploy():
    data = request.get_json(force=True)

    # Validation des champs obligatoires
    required = ["parent_path", "app_name", "domain", "port", "routing_type"]
    for field in required:
        if not data.get(field):
            return jsonify({"success": False, "error": f"Champ manquant : {field}"}), 400

    parent_path = data["parent_path"].rstrip("/")
    app_name = data["app_name"]
    domain = data["domain"]
    port = int(data["port"])
    routing_type = data["routing_type"]  # "subfolder" | "subdomain"
    wsgi_module = data.get("wsgi_module", "wsgi:app")
    workers = int(data.get("workers", 3))

    # Sécurité : valider app_name
    if not re.match(r"^[a-zA-Z0-9_\-]+$", app_name):
        return jsonify({"success": False, "error": "Nom d'application invalide (alphanumérique + tirets)"}), 400

    project_path = f"{parent_path}/{app_name}"
    steps = []

    if not IS_VPS:
        # Mode démo : simuler toutes les étapes
        steps = [
            {"step": "Création des répertoires", "ok": True,
             "detail": f"mkdir -p {project_path} [DÉMO]"},
            {"step": "Environnement virtuel", "ok": True,
             "detail": f"python3 -m venv {project_path}/venv [DÉMO]"},
            {"step": "Fichier systemd", "ok": True,
             "detail": f"Écrit dans /etc/systemd/system/{app_name}.service [DÉMO]"},
            {"step": "Config Nginx include", "ok": True,
             "detail": f"Écrit dans /etc/nginx/{domain}-apps/{app_name}.conf [DÉMO]"},
            {"step": "systemctl daemon-reload", "ok": True, "detail": "[DÉMO]"},
            {"step": f"systemctl enable --now {app_name}", "ok": True, "detail": "[DÉMO]"},
            {"step": "nginx -t && systemctl reload nginx", "ok": True, "detail": "[DÉMO]"},
        ]
        service_content = generate_service_file(app_name, project_path, port, workers, wsgi_module)
        nginx_block = generate_nginx_location_block(app_name, port, routing_type)

        return jsonify({
            "success": True,
            "demo": True,
            "steps": steps,
            "service_file": service_content,
            "nginx_block": nginx_block,
            "project_path": project_path,
        })

    # ── Exécution réelle sur VPS ──

    # Étape 1 : Créer les répertoires
    try:
        Path(project_path).mkdir(parents=True, exist_ok=True)
        steps.append({"step": "Création des répertoires", "ok": True,
                       "detail": f"mkdir -p {project_path}"})
    except Exception as e:
        steps.append({"step": "Création des répertoires", "ok": False, "detail": str(e)})
        return jsonify({"success": False, "steps": steps})

    # Étape 2 : Environnement virtuel
    venv_path = Path(project_path) / "venv"
    if not venv_path.exists():
        rc, out, err = run_cmd(["python3", "-m", "venv", str(venv_path)])
        ok = rc == 0
        steps.append({"step": "Environnement virtuel", "ok": ok,
                       "detail": out or err or "venv créé"})
        if not ok:
            return jsonify({"success": False, "steps": steps})
    else:
        steps.append({"step": "Environnement virtuel", "ok": True,
                       "detail": "venv déjà existant — ignoré"})

    # Étape 3 : Fichier systemd
    service_content = generate_service_file(app_name, project_path, port, workers, wsgi_module)
    service_path = SYSTEMD_DIR / f"{app_name}.service"
    try:
        service_path.write_text(service_content)
        steps.append({"step": "Fichier systemd", "ok": True, "detail": str(service_path)})
    except Exception as e:
        steps.append({"step": "Fichier systemd", "ok": False, "detail": str(e)})
        return jsonify({"success": False, "steps": steps})

    # Étape 4 : Config Nginx par include
    nginx_block = generate_nginx_location_block(app_name, port, routing_type)
    nginx_include_dir = Path(f"/etc/nginx/{domain}-apps")
    try:
        nginx_include_dir.mkdir(parents=True, exist_ok=True)
        conf_file = nginx_include_dir / f"{app_name}.conf"
        conf_file.write_text(nginx_block)

        # Insérer include dans le fichier principal du domaine si absent
        domain_conf = NGINX_SITES_AVAILABLE / domain
        if domain_conf.exists():
            content = domain_conf.read_text()
            include_line = f"include /etc/nginx/{domain}-apps/*.conf;"
            if include_line not in content:
                # Insérer juste avant la dernière accolade fermante du bloc server HTTPS
                pattern = r"(server\s*\{[^}]*listen\s+443[^}]*)\}"
                replacement = rf"\1    {include_line}\n}}"
                new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
                if new_content != content:
                    domain_conf.write_text(new_content)
                    steps.append({"step": "Config Nginx include", "ok": True,
                                   "detail": f"include inséré dans {domain_conf}"})
                else:
                    steps.append({"step": "Config Nginx include", "ok": True,
                                   "detail": f"{conf_file} créé (include déjà présent ou non trouvé)"})
            else:
                steps.append({"step": "Config Nginx include", "ok": True,
                               "detail": f"include déjà présent dans {domain_conf}"})
        else:
            steps.append({"step": "Config Nginx include", "ok": True,
                           "detail": f"{conf_file} créé (fichier domaine {domain_conf} non trouvé)"})
    except Exception as e:
        steps.append({"step": "Config Nginx include", "ok": False, "detail": str(e)})
        return jsonify({"success": False, "steps": steps})

    # Étape 5 : daemon-reload
    rc, out, err = run_cmd(["systemctl", "daemon-reload"])
    steps.append({"step": "systemctl daemon-reload", "ok": rc == 0, "detail": out or err or "OK"})

    # Étape 6 : enable + démarrage
    rc, out, err = run_cmd(["systemctl", "enable", "--now", f"{app_name}.service"])
    steps.append({"step": f"systemctl enable --now {app_name}", "ok": rc == 0,
                   "detail": out or err or "Service activé et démarré"})

    # Étape 7 : Test + reload Nginx
    rc_test, out_test, err_test = run_cmd(["nginx", "-t"])
    if rc_test == 0:
        rc_reload, out_reload, err_reload = run_cmd(["systemctl", "reload", "nginx"])
        steps.append({"step": "nginx -t && systemctl reload nginx", "ok": rc_reload == 0,
                       "detail": out_reload or err_reload or "nginx rechargé"})
    else:
        steps.append({"step": "nginx -t", "ok": False,
                       "detail": f"Erreur config nginx: {err_test}"})

    all_ok = all(s["ok"] for s in steps)
    return jsonify({
        "success": all_ok,
        "demo": False,
        "steps": steps,
        "service_file": service_content,
        "nginx_block": nginx_block,
        "project_path": project_path,
    })


# ─────────────────────────────────────────────────────────
#  API — INFOS NGINX EXISTANTES
# ─────────────────────────────────────────────────────────

@app.route("/api/nginx/domains")
def nginx_domains():
    if not IS_VPS:
        return jsonify({
            "domains": ["andriah.run.place", "support.i-tracker.online", "i-tracker.online"],
            "demo": True,
        })
    domains = []
    if NGINX_SITES_AVAILABLE.exists():
        domains = [f.name for f in NGINX_SITES_AVAILABLE.iterdir() if f.is_file()]
    return jsonify({"domains": domains, "demo": False})


# ─────────────────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
