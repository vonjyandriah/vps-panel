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
from functools import wraps
from pathlib import Path

import psutil
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

PANEL_USERNAME = os.environ.get("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin")
PANEL_BASE_PATH = os.environ.get("PANEL_BASE_PATH", "")  # ex: "/panel_admin"

# Chemins absolus — nécessaires quand le panel tourne en service systemd
# (le PATH du service ne contient pas /bin ni /usr/bin)
SYSTEMCTL   = shutil.which("systemctl")   or "/bin/systemctl"
JOURNALCTL  = shutil.which("journalctl")  or "/bin/journalctl"
SUDO        = shutil.which("sudo")        or "/usr/bin/sudo"

def _is_root() -> bool:
    return os.geteuid() == 0

def sudo(cmd: list[str]) -> list[str]:
    """Préfixe la commande avec sudo si le panel ne tourne pas en root."""
    return cmd if _is_root() else [SUDO, "-n"] + cmd


# ─────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Non authentifié"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = request.args.get("error")
    return render_template("login.html", error=error, panel_base=PANEL_BASE_PATH)


@app.route("/login", methods=["POST"])
def login_submit():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if username == PANEL_USERNAME and password == PANEL_PASSWORD:
        session["logged_in"] = True
        session["username"] = username
        return redirect(url_for("index"))
    return redirect(url_for("login_page", error="Identifiants incorrects"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

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


def run_cmd(cmd: list[str], check: bool = False, timeout: int = 15) -> tuple[int, str, str]:
    try:
        env = os.environ.copy()
        # Forcer sortie texte brute — pas de couleurs ANSI, pas de pager
        env.update({"SYSTEMD_COLORS": "0", "PAGER": "cat", "TERM": "dumb",
                    "GIT_TERMINAL_PROMPT": "0"})
        # S'assurer que /bin et /usr/bin sont dans le PATH (absent quand on tourne
        # en service systemd avec un PATH restreint)
        paths = env.get("PATH", "").split(":")
        for p in ["/bin", "/usr/bin", "/sbin", "/usr/sbin"]:
            if p not in paths:
                paths.append(p)
        env["PATH"] = ":".join(paths)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
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
@login_required
def index():
    demo_mode = not IS_VPS
    return render_template("index.html", demo_mode=demo_mode, panel_base=PANEL_BASE_PATH)


# ─────────────────────────────────────────────────────────
#  API — MONITORING SYSTÈME
# ─────────────────────────────────────────────────────────

@app.route("/api/system-stats")
@login_required
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
#  API — DEBUG (voir output brut des commandes système)
# ─────────────────────────────────────────────────────────

@app.route("/api/debug")
@login_required
def debug_info():
    """Retourne l'output brut des commandes clés pour diagnostiquer."""
    import shutil

    results = {}

    # systemctl list-units brut
    rc, out, err = run_cmd([SYSTEMCTL, "list-units", "--type=service",
                             "--all", "--no-pager", "--no-legend"])
    results["systemctl_list_units"] = {
        "rc": rc, "stdout": out[:3000], "stderr": err[:500],
        "lines": out.splitlines()[:20]  # 20 premières lignes
    }

    # systemctl version
    rc2, out2, _ = run_cmd([SYSTEMCTL, "--version"])
    results["systemctl_version"] = out2.splitlines()[0] if out2 else "N/A"

    # Exemple d'une ligne brute (repr pour voir les caractères cachés)
    first_lines = out.splitlines()[:5]
    results["first_lines_repr"] = [repr(l) for l in first_lines]

    # Chemin de systemctl
    results["systemctl_path"] = SYSTEMCTL

    # Fichiers .service dans systemd
    svc_files = list(SYSTEMD_DIR.glob("*.service")) if IS_VPS else []
    results["service_files"] = [f.name for f in svc_files[:20]]

    # IS_VPS
    results["is_vps"] = IS_VPS
    results["systemd_dir"] = str(SYSTEMD_DIR)
    results["systemd_dir_exists"] = SYSTEMD_DIR.exists()

    return jsonify(results)


# ─────────────────────────────────────────────────────────
#  API — SERVICES SYSTEMD
# ─────────────────────────────────────────────────────────

@app.route("/api/services")
@login_required
def list_services():
    if not IS_VPS:
        return jsonify({"services": DEMO_SERVICES, "demo": True})

    rc, out, err = run_cmd([
        SYSTEMCTL, "list-units", "--type=service", "--all",
        "--no-pager", "--no-legend"
    ])

    services = []
    if rc == 0:
        for line in out.splitlines():
            # Supprimer tout caractère non-ASCII en début de ligne (●, codes ANSI…)
            line = re.sub(r'^[\s\W]+', '', line)
            parts = line.split(None, 4)
            if not parts:
                continue
            # Si le premier token ne ressemble pas à un nom de service, le sauter
            if ".service" not in parts[0] and "@" not in parts[0]:
                continue
            if len(parts) >= 4:
                name = parts[0].replace(".service", "")
                load, active, sub = parts[1], parts[2], parts[3]
                desc = parts[4] if len(parts) > 4 else ""

                # Récupérer ActiveEnterTimestamp
                _, ts_out, _ = run_cmd([SYSTEMCTL, "show", f"{name}.service",
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
@login_required
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

    def _wait_for_state(target_active: bool, attempts: int = 8, delay: float = 0.8) -> str:
        """Interroge is-active jusqu'à ce que l'état corresponde ou que le délai soit dépassé."""
        import time
        for _ in range(attempts):
            _, s, _ = run_cmd([SYSTEMCTL, "is-active", f"{name}.service"], timeout=4)
            s = s.strip()
            if target_active and s == "active":
                return s
            if not target_active and s in ("inactive", "dead", "failed"):
                return s
            if s not in ("activating", "deactivating", "reloading"):
                # État stable inattendu — on s'arrête
                return s
            time.sleep(delay)
        _, s, _ = run_cmd([SYSTEMCTL, "is-active", f"{name}.service"], timeout=4)
        return s.strip()

    # Cas spécial : le panel se redémarre lui-même → on répond d'abord,
    # puis on lance le redémarrage dans un thread pour ne pas tuer la connexion.
    own_service = Path("/proc/1/comm").read_text(errors="ignore").strip() != "systemd" or \
                  os.environ.get("INVOCATION_ID")  # on est dans un service systemd

    # Détecter si name est le service du panel courant
    try:
        my_unit = Path("/proc/self/cgroup").read_text().splitlines()
        my_unit_name = next(
            (l.split("/")[-1].replace(".service", "") for l in my_unit if ".service" in l), ""
        )
    except Exception:
        my_unit_name = ""

    is_self = bool(my_unit_name) and my_unit_name == name

    if is_self and action in ("restart", "stop"):
        import threading
        def _delayed():
            import time
            time.sleep(1.5)
            run_cmd(sudo([SYSTEMCTL, action, f"{name}.service"]), timeout=30)
        threading.Thread(target=_delayed, daemon=True).start()
        return jsonify({
            "success": True,
            "message": f"Redémarrage du panel en cours… reconnectez-vous dans 3 secondes.",
            "self_restart": True,
        })

    rc, out, err = run_cmd(sudo([SYSTEMCTL, action, f"{name}.service"]), timeout=30)

    if action in ("start", "restart"):
        state = _wait_for_state(target_active=True)
        success = state == "active"
        msg = (out or err or "").strip() or (
            f"Service {name} actif ✓" if success
            else f"Le service ne semble pas actif (état: {state or 'inconnu'})"
        )
    elif action == "stop":
        state = _wait_for_state(target_active=False)
        success = state in ("inactive", "dead", "failed")
        msg = (out or err or "").strip() or (
            f"Service {name} arrêté ✓" if success
            else f"Le service est encore actif (état: {state})"
        )
    else:  # enable / disable
        state = ""
        success = rc == 0
        msg = (out or err or "").strip() or f"Action {action} exécutée"

    return jsonify({
        "success": success,
        "message": msg,
        "returncode": rc,
        "active_state": state,
    })


@app.route("/api/services/<name>/logs")
@login_required
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

    # ── Stratégie multi-fallback ──────────────────────────────────────────
    # journalctl peut être corrompu/bloqué sur certains VPS.
    # On tente plusieurs sources dans l'ordre, avec des timeouts courts.

    def _journalctl_fast(since: str, timeout: int) -> tuple[int, str, str]:
        """journalctl limité à une fenêtre de temps récente."""
        return run_cmd([
            JOURNALCTL, "-u", f"{name}.service",
            "-n", str(lines), "--no-pager", "--no-hostname",
            "--output=short-iso", "--since", since,
        ], timeout=timeout)

    def _syslog_grep() -> str | None:
        """Lit /var/log/syslog ou /var/log/messages et filtre par service."""
        for logfile in ["/var/log/syslog", "/var/log/messages"]:
            p = Path(logfile)
            if not p.exists():
                continue
            try:
                rc2, out2, _ = run_cmd(
                    ["grep", "-i", name, logfile], timeout=4
                )
                if rc2 == 0 and out2.strip():
                    # Garder les N dernières lignes
                    tail = out2.strip().splitlines()
                    return "\n".join(tail[-lines:])
            except Exception:
                pass
        return None

    def _systemctl_status() -> str:
        """systemctl status est toujours rapide, donne les derniers logs."""
        rc2, out2, err2 = run_cmd(
            [SYSTEMCTL, "status", f"{name}.service", "--no-pager", "-l"],
            timeout=5
        )
        return (out2 or err2 or "").strip()

    def _has_entries(text: str) -> bool:
        """Vrai si journalctl a retourné de vraies lignes de log (pas juste '-- No entries --')."""
        t = text.strip()
        return bool(t) and "-- No entries --" not in t

    source = "journalctl"
    result = ""

    # 1. journalctl — dernière heure (le plus rapide même avec journal chargé)
    rc, out, err = _journalctl_fast("1 hour ago", 4)
    if rc == 0 and _has_entries(out):
        result = out.strip()
    else:
        # 2. journalctl — 7 derniers jours
        rc, out, err = _journalctl_fast("7 days ago", 5)
        if rc == 0 and _has_entries(out):
            result = out.strip()
        else:
            # 3. /var/log/syslog ou /var/log/messages
            source = "syslog"
            sl = _syslog_grep()
            if sl:
                result = sl
            else:
                # 4. systemctl status — toujours disponible
                source = "systemctl status"
                result = _systemctl_status()
                if not result:
                    result = "⚠ Impossible de récupérer les logs (journalctl bloqué, syslog vide).\n" \
                             "Sur le VPS : journalctl --rotate && journalctl --vacuum-size=200M && systemctl restart systemd-journald"

    return jsonify({
        "success": True,
        "logs": result,
        "source": source,
        "demo": False,
    })


@app.route("/api/services/<name>/status")
@login_required
def service_status(name: str):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return jsonify({"success": False, "error": "Nom de service invalide"}), 400

    if not IS_VPS:
        svc = next((s for s in DEMO_SERVICES if s["name"] == name), None)
        if svc:
            return jsonify({"success": True, "status": svc["status"],
                            "sub_status": svc["sub_status"], "demo": True})
        return jsonify({"success": False, "error": "Service non trouvé"}), 404

    rc, out, err = run_cmd([SYSTEMCTL, "show", f"{name}.service",
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
@login_required
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
@login_required
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
    rc, out, err = run_cmd(sudo([SYSTEMCTL, "daemon-reload"]))
    steps.append({"step": "systemctl daemon-reload", "ok": rc == 0, "detail": out or err or "OK"})

    # Étape 6 : enable + démarrage
    rc, out, err = run_cmd(sudo([SYSTEMCTL, "enable", "--now", f"{app_name}.service"]), timeout=30)
    steps.append({"step": f"systemctl enable --now {app_name}", "ok": rc == 0,
                   "detail": out or err or "Service activé et démarré"})

    # Étape 7 : Test + reload Nginx
    rc_test, out_test, err_test = run_cmd(sudo(["nginx", "-t"]))
    if rc_test == 0:
        rc_reload, out_reload, err_reload = run_cmd(sudo([SYSTEMCTL, "reload", "nginx"]))
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
#  API — GESTION DOMAINES NGINX
# ─────────────────────────────────────────────────────────

DEMO_DOMAINS = [
    {
        "name": "andriah.run.place",
        "conf_path": "/etc/nginx/sites-available/andriah.run.place",
        "enabled": True,
        "apps": ["money-manager", "gps-fleet-manager"],
        "ssl": True,
    },
    {
        "name": "support.i-tracker.online",
        "conf_path": "/etc/nginx/sites-available/support.i-tracker.online",
        "enabled": True,
        "apps": ["i-tracker-backend"],
        "ssl": True,
    },
    {
        "name": "i-tracker.online",
        "conf_path": "/etc/nginx/sites-available/i-tracker.online",
        "enabled": True,
        "apps": ["odometer"],
        "ssl": True,
    },
]


def get_domain_details(conf_file: Path) -> dict:
    """Parse a nginx conf file and extract useful info."""
    name = conf_file.name
    try:
        content = conf_file.read_text()
    except Exception:
        content = ""

    ssl = "ssl_certificate" in content or "listen 443" in content

    # Extraire le vrai nom de domaine depuis la directive server_name
    # Le fichier peut s'appeler "support_i_tracker" mais contenir "server_name support.i-tracker.online"
    server_name = name  # fallback = nom de fichier
    m = re.search(r"^\s*server_name\s+([^;]+);", content, re.MULTILINE)
    if m:
        # Prendre le premier nom (ignorer les alias www. etc.)
        candidates = m.group(1).strip().split()
        # Préférer le nom le plus long (le plus spécifique) qui n'est pas "_"
        candidates = [c for c in candidates if c != "_"]
        if candidates:
            server_name = max(candidates, key=len)

    # Apps déployées via le panel (include files)
    include_dir = Path(f"/etc/nginx/{name}-apps")
    apps = []
    if include_dir.exists():
        apps = [f.stem for f in include_dir.glob("*.conf")]

    # Résoudre les blocs upstream nommés : upstream backend { server 127.0.0.1:8000; }
    upstreams = {}
    for up_match in re.finditer(r"upstream\s+(\S+)\s*\{([^}]+)\}", content, re.DOTALL):
        up_name = up_match.group(1)
        srv = re.search(r"server\s+[^:]+:(\d+)", up_match.group(2))
        if srv:
            upstreams[up_name] = int(srv.group(1))

    # Routes détectées dans les blocs location (proxy_pass TCP, socket Unix, upstream nommé)
    routes = []
    proxy_tcp_re   = re.compile(r"proxy_pass\s+https?://[^:/\s]+:(\d+)", re.MULTILINE)
    proxy_named_re = re.compile(r"proxy_pass\s+https?://([a-zA-Z0-9_\-]+)\s*;", re.MULTILINE)
    proxy_unix_re  = re.compile(r"proxy_pass\s+https?://unix:([^;]+);", re.MULTILINE)

    for loc_match in re.finditer(r"location\s+([^\s{]+)\s*\{", content, re.MULTILINE):
        path = loc_match.group(1)
        start = loc_match.end()
        depth, pos = 1, start
        while pos < len(content) and depth > 0:
            if content[pos] == '{':
                depth += 1
            elif content[pos] == '}':
                depth -= 1
            pos += 1
        block = content[start:pos - 1]

        # 1. proxy_pass http://127.0.0.1:PORT
        pp = proxy_tcp_re.search(block)
        if pp:
            routes.append({"path": path, "port": int(pp.group(1)), "type": "tcp"})
            continue
        # 2. proxy_pass http://upstream_name  (résolu via bloc upstream)
        pn = proxy_named_re.search(block)
        if pn:
            up = pn.group(1)
            port = upstreams.get(up)
            routes.append({"path": path, "port": port, "type": "upstream",
                           "upstream": up})
            continue
        # 3. proxy_pass http://unix:/path/to/socket
        pu = proxy_unix_re.search(block)
        if pu:
            routes.append({"path": path, "port": None, "type": "socket",
                           "socket": pu.group(1).strip()})

    # Check if enabled (symlink in sites-enabled)
    sites_enabled = Path("/etc/nginx/sites-enabled")
    enabled = (sites_enabled / name).exists() if sites_enabled.exists() else True

    return {
        "name": name,                # nom du fichier — utilisé pour les chemins internes
        "server_name": server_name,  # vrai domaine extrait du server_name nginx
        "conf_path": str(conf_file),
        "enabled": enabled,
        "apps": apps,
        "routes": routes,            # routes proxy_pass détectées dans le fichier nginx
        "ssl": ssl,
    }


@app.route("/api/nginx/domains")
@login_required
def nginx_domains():
    if not IS_VPS:
        return jsonify({"domains": DEMO_DOMAINS, "demo": True})

    domains = []
    if NGINX_SITES_AVAILABLE.exists():
        for f in sorted(NGINX_SITES_AVAILABLE.iterdir()):
            if f.is_file():
                domains.append(get_domain_details(f))

    return jsonify({"domains": domains, "demo": False})


@app.route("/api/nginx/domains/<name>/config")
@login_required
def nginx_domain_config(name: str):
    """Retourne le contenu brut du fichier nginx d'un domaine."""
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", name):
        return jsonify({"success": False, "error": "Nom invalide"}), 400
    conf_file = NGINX_SITES_AVAILABLE / name
    try:
        content = conf_file.read_text()
        return jsonify({"success": True, "content": content, "path": str(conf_file)})
    except FileNotFoundError:
        return jsonify({"success": False, "error": f"Fichier non trouvé : {conf_file}"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/nginx/test", methods=["POST"])
@login_required
def nginx_test():
    if not IS_VPS:
        return jsonify({"success": True, "output": "[DÉMO] nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\nnginx: configuration file /etc/nginx/nginx.conf test is successful", "demo": True})
    rc, out, err = run_cmd(["nginx", "-t"])
    return jsonify({"success": rc == 0, "output": (out or "") + (err or ""), "demo": False})


@app.route("/api/nginx/reload", methods=["POST"])
@login_required
def nginx_reload():
    if not IS_VPS:
        return jsonify({"success": True, "message": "[DÉMO] nginx rechargé avec succès", "demo": True})
    rc_test, _, err_test = run_cmd(["nginx", "-t"])
    if rc_test != 0:
        return jsonify({"success": False, "message": f"Config invalide : {err_test}"})
    rc, out, err = run_cmd(sudo([SYSTEMCTL, "reload", "nginx"]))
    return jsonify({"success": rc == 0, "message": out or err or "nginx rechargé", "demo": False})


# ─────────────────────────────────────────────────────────
#  API — SCAN APPS EXISTANTES (uniformisation)
# ─────────────────────────────────────────────────────────

DEMO_SCAN_APPS = [
    {"name": "gps-fleet-manager", "description": "GPS Fleet Manager - Application Flask",
     "working_dir": "/opt/gps_fleet_manager_prod", "user": "root",
     "port": 8000, "workers": 3, "wsgi_module": "wsgi:app", "is_standard": False},
    {"name": "i-tracker-backend", "description": "i-Tracker Backend API",
     "working_dir": "/opt/i-tracker/backend", "user": "root",
     "port": 8001, "workers": 2, "wsgi_module": "wsgi:app", "is_standard": False},
    {"name": "odometer", "description": "Odometer SaaS",
     "working_dir": "/opt/odometer", "user": "root",
     "port": 8002, "workers": 3, "wsgi_module": "wsgi:app", "is_standard": True},
    {"name": "money-manager", "description": "Flask money-manager",
     "working_dir": "/opt/money_manager", "user": "root",
     "port": 8003, "workers": 2, "wsgi_module": "app:app", "is_standard": False},
]


def get_service_port_from_pid(name: str) -> int | None:
    """Détecte le port d'écoute d'un service via son PID (fallback quand le fichier .service n'indique pas le port)."""
    try:
        rc, out, _ = run_cmd([SYSTEMCTL, "show", f"{name}.service",
                               "--property=MainPID", "--value"])
        pid = int(out.strip())
        if pid <= 0:
            return None
        proc = psutil.Process(pid)
        # Chercher dans le process et ses enfants (workers gunicorn)
        procs = [proc] + proc.children(recursive=True)
        for p in procs:
            try:
                for conn in p.net_connections(kind="inet"):
                    if conn.status == "LISTEN" and conn.laddr.port > 0:
                        return conn.laddr.port
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return None


def parse_service_file(path: Path) -> dict | None:
    """Parse a systemd .service file and return info if gunicorn-based."""
    try:
        content = path.read_text()
    except Exception:
        return None
    if "gunicorn" not in content:
        return None

    def extract(pattern, default=""):
        m = re.search(pattern, content, re.MULTILINE)
        return m.group(1).strip() if m else default

    name = path.stem
    working_dir = extract(r"^WorkingDirectory=(.+)$")
    user = extract(r"^User=(.+)$", "root")
    description = extract(r"^Description=(.+)$", name)
    exec_start = extract(r"^ExecStart=(.+)$")

    port, workers, wsgi_module = None, 3, "wsgi:app"
    if exec_start:
        # Formats : --bind 0.0.0.0:8000  --bind=127.0.0.1:8000  -b :8000
        m = re.search(r"(?:--bind|-b)[=\s]+\S*:(\d+)", exec_start)
        if m:
            port = int(m.group(1))
        m = re.search(r"--workers[=\s]+(\d+)", exec_start)
        if m:
            workers = int(m.group(1))
        parts = exec_start.strip().split()
        if parts and not parts[-1].startswith("-"):
            wsgi_module = parts[-1]

    is_standard = ("Service Automatise" in content or "EnvironmentFile" in content) and \
                  ("gunicorn" in content)

    return {
        "name": name,
        "description": description,
        "working_dir": working_dir,
        "user": user,
        "port": port,
        "workers": workers,
        "wsgi_module": wsgi_module,
        "is_standard": is_standard,
    }


@app.route("/api/scan/apps")
@login_required
def scan_apps():
    if not IS_VPS:
        return jsonify({"apps": DEMO_SCAN_APPS, "demo": True})

    EXCLUDED = {"vps-panel"}
    apps = []
    for svc_file in SYSTEMD_DIR.glob("*.service"):
        if svc_file.stem in EXCLUDED:
            continue
        parsed = parse_service_file(svc_file)
        if parsed:
            # Si le port n'est pas détecté dans le fichier .service,
            # tenter de le lire depuis le PID du process en cours
            if parsed["port"] is None:
                parsed["port"] = get_service_port_from_pid(parsed["name"])
            apps.append(parsed)

    return jsonify({"apps": sorted(apps, key=lambda x: x["name"]), "demo": False})


@app.route("/api/apps/<name>/normalize", methods=["POST"])
@login_required
def normalize_app(name: str):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return jsonify({"success": False, "error": "Nom invalide"}), 400

    data = request.get_json(force=True)
    working_dir = data.get("working_dir", "")
    port = int(data.get("port", 8000))
    workers = int(data.get("workers", 3))
    wsgi_module = data.get("wsgi_module", "wsgi:app")

    service_content = generate_service_file(name, working_dir, port, workers, wsgi_module)

    if not IS_VPS:
        return jsonify({"success": True, "demo": True, "service_file": service_content,
                        "message": f"[DÉMO] Service {name} normalisé"})

    service_path = SYSTEMD_DIR / f"{name}.service"
    try:
        if service_path.exists():
            service_path.with_suffix(".service.bak").write_text(service_path.read_text())
        service_path.write_text(service_content)
        run_cmd(sudo([SYSTEMCTL, "daemon-reload"]))
        rc, out, err = run_cmd(sudo([SYSTEMCTL, "restart", f"{name}.service"]), timeout=30)
        return jsonify({
            "success": rc == 0,
            "demo": False,
            "service_file": service_content,
            "message": out or err or f"Service {name} normalisé et redémarré",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─────────────────────────────────────────────────────────
#  API — GIT (repos scan / clone / pull)
# ─────────────────────────────────────────────────────────

DEMO_GIT_REPOS = [
    {"name": "gps-fleet-manager",  "path": "/opt/gps-fleet-manager",  "branch": "main",    "last_commit": "abc1234 Fix GPS timeout (2 jours)"},
    {"name": "i-tracker-backend",  "path": "/opt/i-tracker/backend",  "branch": "main",    "last_commit": "def5678 Add webhook endpoint (5 jours)"},
    {"name": "odometer",           "path": "/opt/odometer",           "branch": "develop", "last_commit": "aaa9999 Refactor billing (1 heure)"},
    {"name": "money-manager",      "path": "/opt/money-manager",      "branch": "main",    "last_commit": "bbb0001 Update deps (3 semaines)"},
]


@app.route("/api/git/repos")
@login_required
def git_repos():
    """Scan a base directory recursively (max 2 levels) for .git repos."""
    base = request.args.get("base", "/opt").strip().rstrip("/")

    if not IS_VPS:
        return jsonify({"repos": DEMO_GIT_REPOS, "base": "/opt", "demo": True})

    base_path = Path(base)
    if not base_path.exists():
        return jsonify({"repos": [], "base": base, "demo": False,
                        "error": f"Chemin {base} introuvable"})

    repos = []
    # Check depth 1 and 2
    candidates = list(base_path.iterdir())
    for d in list(candidates):
        if d.is_dir():
            candidates += [sub for sub in d.iterdir() if sub.is_dir()]

    for d in candidates:
        if not d.is_dir():
            continue
        if not (d / ".git").exists():
            continue
        path_str = str(d)
        # Get current branch
        _, branch, _ = run_cmd(["git", "-C", path_str, "branch", "--show-current"])
        branch = branch.strip() or "unknown"
        # Get last commit (short)
        _, commit, _ = run_cmd(["git", "-C", path_str, "log", "-1",
                                 "--format=%h %s (%cr)"])
        commit = commit.strip() or "—"
        repos.append({
            "name": d.name,
            "path": path_str,
            "branch": branch,
            "last_commit": commit,
        })

    repos.sort(key=lambda x: x["name"])
    return jsonify({"repos": repos, "base": base, "demo": False})


@app.route("/api/git/clone", methods=["POST"])
@login_required
def git_clone():
    data = request.get_json(force=True)
    url = data.get("url", "").strip()
    target = data.get("target_path", "").strip().rstrip("/")
    branch = data.get("branch", "").strip()

    if not url or not target:
        return jsonify({"success": False, "error": "URL et chemin cible requis"}), 400

    if not IS_VPS:
        folder = url.rstrip("/").split("/")[-1].replace(".git", "")
        return jsonify({
            "success": True, "demo": True,
            "output": (
                f"[DÉMO] git clone {url} {target}/{folder}\n"
                f"Cloning into '{folder}'...\n"
                "remote: Enumerating objects: 120, done.\n"
                "remote: Counting objects: 100% (120/120), done.\n"
                "Receiving objects: 100% (120/120), 248.5 KiB | 2.1 MiB/s, done.\n"
                "Done."
            ),
        })

    try:
        Path(target).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

    cmd = ["git", "clone"]
    if branch:
        cmd += ["-b", branch]
    cmd += [url, target]
    rc, out, err = run_cmd(cmd)
    return jsonify({"success": rc == 0, "output": out or err, "demo": False})


@app.route("/api/git/pull", methods=["POST"])
@login_required
def git_pull():
    data = request.get_json(force=True)
    project_path = data.get("project_path", "").strip()

    if not project_path:
        return jsonify({"success": False, "error": "Chemin du projet requis"}), 400

    if not IS_VPS:
        return jsonify({
            "success": True, "demo": True,
            "output": (
                f"[DÉMO] git -C {project_path} pull\n"
                "From https://github.com/owner/repo\n"
                "   abc1234..def5678  main -> origin/main\n"
                "Updating abc1234..def5678\nFast-forward\n"
                " 3 files changed, 42 insertions(+), 5 deletions(-)"
            ),
        })

    rc, out, err = run_cmd(["git", "-C", project_path, "pull"])
    return jsonify({"success": rc == 0, "output": out or err, "demo": False})


# ─────────────────────────────────────────────────────────
#  API — UPLOAD FICHIERS
# ─────────────────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    target_dir = request.form.get("target_dir", "").strip()
    if not target_dir:
        return jsonify({"success": False, "error": "Répertoire cible requis"}), 400
    if "files" not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier fourni"}), 400

    results = []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", f.filename)
        if not IS_VPS:
            results.append({"name": f.filename, "ok": True,
                             "detail": f"[DÉMO] → {target_dir}/{safe_name}"})
            continue
        try:
            dest = Path(target_dir) / safe_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.save(str(dest))
            results.append({"name": f.filename, "ok": True, "detail": str(dest)})
        except Exception as e:
            results.append({"name": f.filename, "ok": False, "detail": str(e)})

    return jsonify({
        "success": all(r["ok"] for r in results),
        "results": results,
        "demo": not IS_VPS,
    })


# ─────────────────────────────────────────────────────────
#  API — SETUP SUDOERS (auto-configuration)
# ─────────────────────────────────────────────────────────

@app.route("/api/setup/sudoers", methods=["POST"])
@login_required
def setup_sudoers():
    data = request.get_json(force=True)
    run_user = re.sub(r"[^a-zA-Z0-9_\-]", "", data.get("run_user", "www-data"))

    # Couvre /bin/systemctl (symlink) ET /usr/bin/systemctl (chemin réel Debian)
    sc = SYSTEMCTL  # chemin détecté sur ce système
    sudoers_lines = [
        f"# VPS Admin Panel — règles auto-générées le {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} start *",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} stop *",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} restart *",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} enable *",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} disable *",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} daemon-reload",
        f"{run_user} ALL=(ALL) NOPASSWD: {sc} reload nginx",
        f"{run_user} ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t",
        f"{run_user} ALL=(ALL) NOPASSWD: /bin/nginx -t",
        f"{run_user} ALL=(ALL) NOPASSWD: /bin/mkdir -p /etc/nginx/*",
        f"{run_user} ALL=(ALL) NOPASSWD: /bin/mkdir -p /opt/*",
    ]
    sudoers_content = "\n".join(sudoers_lines) + "\n"

    if not IS_VPS:
        return jsonify({"success": True, "demo": True,
                        "message": f"[DÉMO] Règles sudoers pour '{run_user}' générées",
                        "content": sudoers_content})

    sudoers_path = Path("/etc/sudoers.d/vps-panel")
    try:
        sudoers_path.write_text(sudoers_content)
        sudoers_path.chmod(0o440)
        rc, out, err = run_cmd(["visudo", "-c", "-f", str(sudoers_path)])
        if rc != 0:
            sudoers_path.unlink(missing_ok=True)
            return jsonify({"success": False, "error": f"Fichier sudoers invalide: {err}"})
        return jsonify({"success": True, "demo": False,
                        "message": f"Règles appliquées → /etc/sudoers.d/vps-panel",
                        "content": sudoers_content})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─────────────────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
