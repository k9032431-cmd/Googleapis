#!/usr/bin/env bash
# ==========================================================================
#  Googleapis Drive Link Bot — установщик для VPS (Debian/Ubuntu)
#
#  Запуск одной командой (из каталога с проектом):
#      sudo bash install.sh
#
#  Или прямо с GitHub:
#      curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/googleapis/claude/telegram-googleapis-bot-pgv9u9/install.sh | sudo bash
#
#  Что делает скрипт:
#    1. Ставит python3/venv/git (если их нет).
#    2. Клонирует репозиторий в /opt/googleapis-bot (или обновляет).
#    3. Создаёт виртуальное окружение и ставит зависимости.
#    4. Создаёт .env из шаблона (если его ещё нет).
#    5. Регистрирует и запускает systemd-сервис googleapis-bot.
# ==========================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/k9032431-cmd/googleapis.git}"
BRANCH="${BRANCH:-claude/telegram-googleapis-bot-pgv9u9}"
APP_DIR="${APP_DIR:-/opt/googleapis-bot}"
SERVICE_NAME="googleapis-bot"
RUN_USER="${RUN_USER:-googleapis-bot}"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Запусти через sudo/от root: sudo bash install.sh"

# --- 1. Зависимости системы -----------------------------------------------
say "Устанавливаю системные пакеты (python3, venv, git)…"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null
else
    warn "apt-get не найден. Установи вручную: python3, python3-venv, git."
fi

# --- 2. Пользователь для сервиса -------------------------------------------
if ! id "$RUN_USER" >/dev/null 2>&1; then
    say "Создаю системного пользователя $RUN_USER…"
    useradd --system --create-home --shell /usr/sbin/nologin "$RUN_USER"
fi

# --- 3. Код проекта ---------------------------------------------------------
# Если скрипт запущен из каталога с bot.py — используем его.
# Иначе клонируем/обновляем репозиторий в APP_DIR.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bot.py" ]; then
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

# --- 4. Виртуальное окружение + зависимости --------------------------------
say "Создаю виртуальное окружение и ставлю зависимости…"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# --- 5. Файл .env -----------------------------------------------------------
if [ ! -f "$APP_DIR/.env" ]; then
    say "Создаю .env из шаблона…"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    NEED_CONFIG=1
else
    say ".env уже существует — не трогаю."
    NEED_CONFIG=0
fi

chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# --- 6. systemd-сервис ------------------------------------------------------
say "Регистрирую systemd-сервис $SERVICE_NAME…"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Googleapis Drive Link Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/bot.py
Restart=always
RestartSec=5
EnvironmentFile=${APP_DIR}/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true

echo
if [ "$NEED_CONFIG" -eq 1 ]; then
    warn "Почти готово! Осталось заполнить настройки:"
    echo "    sudo nano $APP_DIR/.env      # впиши BOT_TOKEN и GOOGLE_API_KEY"
    echo "    sudo systemctl restart $SERVICE_NAME"
else
    say "Перезапускаю сервис…"
    systemctl restart "$SERVICE_NAME"
fi

echo
say "Готово. Полезные команды:"
echo "    systemctl status  $SERVICE_NAME      # статус"
echo "    journalctl -u $SERVICE_NAME -f        # логи в реальном времени"
echo "    systemctl restart $SERVICE_NAME       # перезапуск"
echo "    systemctl stop    $SERVICE_NAME       # остановить"
