# PasarGuard → Google Drive subscription sync

Делает ссылку подписки PasarGuard в виде **настоящего** googleapis-URL:

```
https://www.googleapis.com/drive/v3/files/{FILE_ID}?key={API_KEY}&alt=media
```

Такая ссылка ведёт на реальный `googleapis.com` (Google Drive) и остаётся
доступной, даже если домен твоей панели заблокирован или не резолвится.

---

## Зачем это и как работает

Домен `googleapis.com` принадлежит Google — его нельзя заставить проксировать
на твой сервер, и domain fronting через Google давно закрыт. **Единственный
рабочий способ** получить работающую ссылку подписки на `googleapis.com` —
реально положить файл подписки на Google Drive и раздавать ссылку на него.

Схема:

```
PasarGuard (localhost)  ──►  pg_drive_sync  ──►  Google Drive  ──►  клиент
   /api/users,                 забирает            файл с                скачивает
   /sub/<token>                подписку            подпиской             по googleapis
                               и заливает          (file ID              ?key=...&alt=media
                               на Drive            стабилен)
```

- Сервис стоит на том же сервере, что и панель, и ходит к ней по `127.0.0.1` —
  поэтому блокировка публичного домена панели ему не мешает.
- File ID на Drive не меняется при обновлении содержимого → ссылка стабильна.
- По таймеру сервис перезаливает подписки, если они изменились.

### Что нужно учитывать

- Подписка на Drive — статический файл: клиент **не увидит** заголовки с
  трафиком/сроком (`Subscription-Userinfo`). Сами конфиги и подключение работают.
- Обновление файла на Drive подхватывается с задержкой до нескольких минут
  (кэш Google).
- Это чинит **доставку подписки**. Если у тебя заблокирован не только домен,
  но и IP сервера, то и конфиги внутри должны ходить по рабочему каналу
  (REALITY / чистый IP) — иначе рабочая подписка укажет на мёртвый сервер.

---

## Подготовка Google (один раз)

1. **Проект и Drive API.** [Google Cloud Console](https://console.cloud.google.com)
   → создай проект → **APIs & Services → Library** → включи **Google Drive API**.

2. **API-ключ (чтение).** APIs & Services → **Credentials → Create credentials →
   API key**. Это `GOOGLE_API_KEY` — он подставляется в ссылку для клиента.

3. **Service account (запись).** Credentials → **Create credentials → Service
   account** → у созданного аккаунта вкладка **Keys → Add key → JSON** → скачай
   JSON. Запомни его email вида `xxx@xxx.iam.gserviceaccount.com`.

4. **Папка для файлов.** Сервис-аккаунт не имеет своего места на диске, поэтому:
   - **Рекомендуется:** создай **Shared Drive**, добавь email сервис-аккаунта
     как *Content manager*, возьми его ID.
   - **Или:** создай папку в своём Google Drive, «Поделиться» → добавь email
     сервис-аккаунта как *Редактор*, скопируй ID папки из URL
     (`drive.google.com/drive/folders/`**`ЭТОТ_ID`**). Это `DRIVE_FOLDER_ID`.

---

## Установка на VPS в одну команду

На сервере с панелью (Debian/Ubuntu), от root/sudo:

```bash
curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/googleapis/claude/telegram-googleapis-bot-pgv9u9/install.sh | sudo bash
```

Или, если репозиторий уже склонирован:

```bash
sudo bash install.sh
```

Скрипт поставит зависимости, разложит проект в `/opt/googleapis-bot`, создаст
`.env` и systemd-сервис `pasarguard-drive-sync`. Затем:

```bash
# 1) положи JSON сервис-аккаунта
sudo cp service_account.json /opt/googleapis-bot/service_account.json
sudo chown pgdrive:pgdrive /opt/googleapis-bot/service_account.json

# 2) заполни настройки
sudo nano /opt/googleapis-bot/.env      # PANEL_ADMIN_*, GOOGLE_API_KEY, DRIVE_FOLDER_ID

# 3) запусти
sudo systemctl start pasarguard-drive-sync
```

Готовые ссылки появятся в `/var/lib/pasarguard-drive-sync/links.json`:

```json
{
  "user1": "https://www.googleapis.com/drive/v3/files/1AbC...?key=AIza...&alt=media",
  "user2": "https://www.googleapis.com/drive/v3/files/1XyZ...?key=AIza...&alt=media"
}
```

Эту ссылку и раздавай как подписку.

---

## Основные настройки `.env`

| Переменная                    | Обязательна | Описание                                                        |
|-------------------------------|:-----------:|-----------------------------------------------------------------|
| `PANEL_BASE_URL`              | —           | Адрес панели (по умолчанию `http://127.0.0.1:8000`)             |
| `PANEL_ADMIN_USERNAME`        | да          | Логин админа панели                                             |
| `PANEL_ADMIN_PASSWORD`        | да          | Пароль админа панели                                            |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | да          | Путь к JSON сервис-аккаунта (запись на Drive)                   |
| `GOOGLE_API_KEY`              | да          | API-ключ (чтение; подставляется в ссылку)                       |
| `DRIVE_FOLDER_ID`             | да          | ID папки/Shared Drive для файлов подписок                       |
| `SUB_USER_AGENT`              | —           | UA для выбора формата (`v2rayNG/...` → base64)                  |
| `SUB_CLIENT_TYPE`             | —           | Явный формат в пути (`v2ray`/`clash`/`sing-box`), если нужен    |
| `TARGET_USERS`                | —           | Список username через запятую; пусто = все                      |
| `SYNC_INTERVAL`               | —           | Период синхронизации, сек (0 = один проход)                     |

---

## Управление

```bash
systemctl status  pasarguard-drive-sync      # статус
journalctl -u pasarguard-drive-sync -f        # логи в реальном времени
cat /var/lib/pasarguard-drive-sync/links.json # готовые ссылки
systemctl restart pasarguard-drive-sync       # перезапуск
```

Разовый прогон вручную:

```bash
cd /opt/googleapis-bot
sudo -u pgdrive SYNC_INTERVAL=0 ./venv/bin/python pg_drive_sync.py
```

---

## Необязательно: Телеграм-бот (`bot.py`)

В комплекте есть помощник: бот, которому кидаешь **ID файла Drive или ссылку**,
а он отдаёт готовую `googleapis`-ссылку. Это ручной инструмент, для работы
синхронизации он не нужен. Как пользоваться — см. комментарии в `bot.py` и
переменные `BOT_TOKEN` / `ALLOWED_USERS` в `.env`.
