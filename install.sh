#!/usr/bin/env bash
# ==========================================================================
#  PasarGuard -> Google Drive subscription sync — установщик для VPS
#  (Debian/Ubuntu)
#
#  Запуск одной командой из каталога с проектом:
#      sudo bash install.sh
#
#  Или прямо с GitHub:
#      curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/googleapis/claude/telegram-googleapis-bot-pgv9u9/install.sh | sudo bash
#
#  Что делает скрипт:
#    1. Ставит python3/venv/git (если их нет).
#    2. Разворачивает проект в /opt/googleapis-bot.
#    3. Создаёт venv и ставит зависимости.
#    4. Создаёт .env и каталог состояния.
#    5. Регистрирует systemd-сервис pasarguard-drive-sync (цикл синхронизации).
# ==========================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/k9032431-cmd/googleapis.git}"
BRANCH="${BRANCH:-claude/telegram-googleapis-bot-pgv9u9}"
APP_DIR="${APP_DIR:-/opt/googleapis-bot}"
STATE_DIR="${STATE_DIR:-/var/lib/pasarguard-drive-sync}"
SERVICE_NAME="pasarguard-drive-sync"
RUN_USER="${RUN_USER:-pgdrive}"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Запусти через sudo/от root: sudo bash install.sh"

# --- 1. Системные пакеты ----------------------------------------------------
say "Устанавливаю системные пакеты (python3, venv, git)…"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null
else
    warn "apt-get не найден. Установи вручную: python3, python3-venv, git."
fi

# --- 2. Пользователь сервиса ------------------------------------------------
if ! id "$RUN_USER" >/dev/null 2>&1; then
    say "Создаю системного пользователя $RUN_USER…"
    useradd --system --create-home --shell /usr/sbin/nologin "$RUN_USER"
fi

# --- 3. Код проекта ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pg_drive_sync.py" ]; then
    if [ "$SCRIPT_DIR" != "$APP_DIR" ]; then
        say "Копирую проект в $APP_DIR…"
        mkdir -p "$APP_DIR"
        cp -a "$SCRIPT_DIR/." "$APP_DIR/"
    fi
elif [ -d "$APP_DIR/.git" ]; then
    say "Обновляю репозиторий в $APP_DIR…"
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" checkout -B "$BRANCH" "origin/$BRANCH"
else
    say "Клонирую репозиторий в $APP_DIR…"
    rm -rf "$APP_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# --- 4. venv + зависимости --------------------------------------------------
say "Создаю виртуальное окружение и ставлю зависимости…"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# --- 5. .env и каталог состояния --------------------------------------------
if [ ! -f "$APP_DIR/.env" ]; then
    say "Создаю .env из шаблона…"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    NEED_CONFIG=1
else
    say ".env уже существует — не трогаю."
    NEED_CONFIG=0
fi

mkdir -p "$STATE_DIR"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR" "$STATE_DIR"

# --- 6. systemd-сервис ------------------------------------------------------
say "Регистрирую systemd-сервис $SERVICE_NAME…"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=PasarGuard -> Google Drive subscription sync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/pg_drive_sync.py
Restart=always
RestartSec=10
EnvironmentFile=${APP_DIR}/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true

echo
if [ "$NEED_CONFIG" -eq 1 ]; then
    warn "Почти готово! Заполни настройки:"
    echo "    1) Положи JSON сервис-аккаунта в $APP_DIR/service_account.json"
    echo "    2) sudo nano $APP_DIR/.env   # PANEL_ADMIN_*, GOOGLE_API_KEY, DRIVE_FOLDER_ID"
    echo "    3) sudo chown $RUN_USER:$RUN_USER $APP_DIR/service_account.json"
    echo "    4) sudo systemctl start $SERVICE_NAME"
else
    say "Перезапускаю сервис…"
    systemctl restart "$SERVICE_NAME"
fi

echo
say "Готово. Полезные команды:"
echo "    systemctl status  $SERVICE_NAME       # статус"
echo "    journalctl -u $SERVICE_NAME -f         # логи в реальном времени"
echo "    cat $STATE_DIR/links.json              # готовые googleapis-ссылки по юзерам"
echo "    systemctl restart $SERVICE_NAME        # перезапуск"
