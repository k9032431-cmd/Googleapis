# Googleapis Drive Link Bot

Телеграм-бот на Python (aiogram). Присылаешь ему **ссылку на файл Google Drive**
(или его **ID**) — он возвращает прямую ссылку для скачивания через Google Drive API:

```
https://www.googleapis.com/drive/v3/files/{FILE_ID}?key={API_KEY}&alt=media
```

Все настройки — в файле `.env`. Устанавливается на сервер **одной командой**.

---

## Что нужно заранее

1. **Токен Telegram-бота** — создай бота у [@BotFather](https://t.me/BotFather)
   (`/newbot`) и скопируй токен.
2. **Google API-ключ** — в [Google Cloud Console](https://console.cloud.google.com):
   - создай проект (или возьми существующий);
   - **APIs & Services → Library** → включи **Google Drive API**;
   - **APIs & Services → Credentials → Create credentials → API key**;
   - скопируй ключ (вида `AIzaSy...`).

> Файлы на Google Drive должны быть открыты «**Доступ всем, у кого есть ссылка**»,
> иначе API-ключ не сможет их отдать (ключ работает только с публичными файлами).

---

## Установка на VPS в одну команду

На чистом сервере (Debian/Ubuntu), от root или через `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/k9032431-cmd/googleapis/claude/telegram-googleapis-bot-pgv9u9/install.sh | sudo bash
```

Либо, если репозиторий уже склонирован:

```bash
sudo bash install.sh
```

Скрипт сам:
- поставит `python3`, `venv`, `git`;
- разложит проект в `/opt/googleapis-bot`;
- создаст виртуальное окружение и установит зависимости;
- создаст `.env` из шаблона;
- зарегистрирует и включит systemd-сервис `googleapis-bot`.

После установки впиши свои ключи и запусти:

```bash
sudo nano /opt/googleapis-bot/.env     # заполни BOT_TOKEN и GOOGLE_API_KEY
sudo systemctl restart googleapis-bot
```

---

## Настройки `.env`

| Переменная       | Обязательна | Описание                                                        |
|------------------|:-----------:|-----------------------------------------------------------------|
| `BOT_TOKEN`      | да          | Токен бота от @BotFather                                         |
| `GOOGLE_API_KEY` | да          | API-ключ Google (подставляется в `?key=` ссылки)                |
| `ALLOWED_USERS`  | нет         | Telegram user id через запятую — кому разрешён бот. Пусто = всем |
| `VERIFY_LINKS`   | нет         | `true` — проверять файл и показывать имя/размер; `false` — нет   |

> **Совет по безопасности:** заполни `ALLOWED_USERS` своим id (узнать у
> [@userinfobot](https://t.me/userinfobot)). Иначе любой, кто найдёт бота,
> будет получать ссылки с твоим API-ключом.

---

## Как пользоваться

Напиши боту `/start`, затем пришли ссылку или ID. Примеры входа:

- `https://drive.google.com/file/d/1daG95vwd7Sj_-Rn8_IbD9FWFy81TXJxA/view`
- `https://drive.google.com/open?id=1daG95vwd7Sj_-Rn8_IbD9FWFy81TXJxA`
- `1daG95vwd7Sj_-Rn8_IbD9FWFy81TXJxA`

Можно прислать несколько ссылок — по одной в строке.

Бот ответит готовой ссылкой:

```
https://www.googleapis.com/drive/v3/files/1daG95vwd7Sj_-Rn8_IbD9FWFy81TXJxA?key=AIzaSy...&alt=media
```

---

## Управление сервисом

```bash
systemctl status  googleapis-bot     # статус
journalctl -u googleapis-bot -f       # логи в реальном времени
systemctl restart googleapis-bot      # перезапуск
systemctl stop    googleapis-bot      # остановить
```

---

## Локальный запуск (для разработки)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # заполни .env
python bot.py
```
