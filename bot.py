"""
Googleapis Drive Link Bot
=========================

Телеграм-бот: присылаешь ему ссылку на файл Google Drive (или просто file ID),
а он отдаёт прямую ссылку для скачивания через Google Drive API:

    https://www.googleapis.com/drive/v3/files/{FILE_ID}?key={API_KEY}&alt=media

Все настройки берутся из .env (см. .env.example).
"""

import asyncio
import logging
import os
import re
from urllib.parse import quote

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("googleapis-bot")

API_BASE = "https://www.googleapis.com/drive/v3/files"

# Google Drive file ID: буквы, цифры, "-" и "_", обычно 25+ символов.
_ID_RE = re.compile(r"[-\w]{20,}")
# Шаблоны популярных ссылок Google Drive.
_LINK_PATTERNS = [
    re.compile(r"/file/d/([-\w]{20,})"),        # /file/d/<id>/view
    re.compile(r"/d/([-\w]{20,})"),              # docs/spreadsheets /d/<id>
    re.compile(r"[?&]id=([-\w]{20,})"),          # open?id=<id>, uc?id=<id>
    re.compile(r"/folders/([-\w]{20,})"),        # папка
]


# --------------------------------------------------------------------------- #
#  Конфигурация
# --------------------------------------------------------------------------- #
class Config:
    def __init__(self) -> None:
        self.bot_token = (os.getenv("BOT_TOKEN") or "").strip()
        self.google_api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
        self.verify_links = (os.getenv("VERIFY_LINKS", "true").strip().lower()
                             in ("1", "true", "yes", "on"))

        raw_users = (os.getenv("ALLOWED_USERS") or "").strip()
        self.allowed_users: set[int] = set()
        for chunk in re.split(r"[,\s]+", raw_users):
            if chunk.isdigit():
                self.allowed_users.add(int(chunk))

    def validate(self) -> list[str]:
        errors = []
        if not self.bot_token:
            errors.append("BOT_TOKEN не задан — получи токен у @BotFather.")
        if not self.google_api_key:
            errors.append("GOOGLE_API_KEY не задан — создай API-ключ в Google Cloud Console.")
        return errors

    def is_allowed(self, user_id: int) -> bool:
        # Пустой список = доступ всем.
        return not self.allowed_users or user_id in self.allowed_users


# --------------------------------------------------------------------------- #
#  Логика
# --------------------------------------------------------------------------- #
def extract_file_id(text: str) -> str | None:
    """Достаёт file ID из ссылки Google Drive или из голого ID."""
    text = text.strip()
    for pattern in _LINK_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    # Голый ID (вся строка целиком — один идентификатор).
    if _ID_RE.fullmatch(text):
        return text
    # Последняя попытка: первый подходящий токен в строке.
    m = _ID_RE.search(text)
    return m.group(0) if m else None


def build_link(file_id: str, api_key: str) -> str:
    return f"{API_BASE}/{quote(file_id)}?key={quote(api_key)}&alt=media"


def _fmt_size(num: int | None) -> str:
    if not num:
        return "—"
    size = float(num)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
        size /= 1024
    return f"{num} Б"


async def fetch_metadata(session: aiohttp.ClientSession, file_id: str,
                         api_key: str) -> tuple[bool, str]:
    """
    Проверяет доступность файла по ключу, запрашивая метаданные.
    Возвращает (успех, человекочитаемое_описание_или_ошибка).
    """
    url = (f"{API_BASE}/{quote(file_id)}"
           f"?key={quote(api_key)}&fields=id,name,size,mimeType")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
            if resp.status == 200 and "id" in data:
                name = data.get("name", "—")
                size = _fmt_size(int(data["size"]) if data.get("size") else None)
                mime = data.get("mimeType", "—")
                return True, f"📄 <b>{name}</b>\nРазмер: {size}\nТип: <code>{mime}</code>"
            # Ошибка от Google.
            err = data.get("error", {})
            reason = err.get("message") or f"HTTP {resp.status}"
            return False, reason
    except asyncio.TimeoutError:
        return False, "таймаут при обращении к Google API"
    except aiohttp.ClientError as exc:
        return False, f"сетевая ошибка: {exc}"


# --------------------------------------------------------------------------- #
#  Обработчики
# --------------------------------------------------------------------------- #
def build_dispatcher(cfg: Config) -> Dispatcher:
    dp = Dispatcher()

    def guard(message: Message) -> bool:
        return bool(message.from_user and cfg.is_allowed(message.from_user.id))

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if not guard(message):
            await message.answer("⛔ Нет доступа. Обратись к владельцу бота.")
            return
        await message.answer(
            "👋 Привет!\n\n"
            "Пришли мне <b>ссылку на файл Google Drive</b> или его <b>ID</b>, "
            "а я верну прямую ссылку на скачивание через googleapis:\n"
            "<code>https://www.googleapis.com/drive/v3/files/ID?key=КЛЮЧ&alt=media</code>\n\n"
            "Поддерживаю ссылки вида:\n"
            "• <code>drive.google.com/file/d/ID/view</code>\n"
            "• <code>drive.google.com/open?id=ID</code>\n"
            "• <code>drive.google.com/uc?id=ID</code>\n"
            "• просто <code>ID</code>\n\n"
            "Можно прислать несколько ссылок — по одной в строке.\n"
            "/help — помощь."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if not guard(message):
            await message.answer("⛔ Нет доступа.")
            return
        await message.answer(
            "ℹ️ <b>Как пользоваться</b>\n\n"
            "1. Файл на Google Drive должен быть открыт по ссылке "
            "(«Доступ всем, у кого есть ссылка») — иначе API-ключ его не отдаст.\n"
            "2. Пришли ссылку или ID.\n"
            "3. Получишь готовую ссылку googleapis для прямого скачивания.\n\n"
            "Если включена проверка (VERIFY_LINKS=true), бот заодно покажет "
            "имя, размер и тип файла."
        )

    @dp.message(F.text)
    async def handle_text(message: Message) -> None:
        if not guard(message):
            await message.answer("⛔ Нет доступа. Обратись к владельцу бота.")
            return

        lines = [ln for ln in (message.text or "").splitlines() if ln.strip()]
        if not lines:
            return

        session: aiohttp.ClientSession | None = None
        if cfg.verify_links:
            session = aiohttp.ClientSession()

        try:
            replies: list[str] = []
            for line in lines:
                file_id = extract_file_id(line)
                if not file_id:
                    replies.append(f"❓ Не нашёл file ID в: <code>{line[:80]}</code>")
                    continue

                link = build_link(file_id, cfg.google_api_key)

                if session is not None:
                    ok, info = await fetch_metadata(session, file_id, cfg.google_api_key)
                    if ok:
                        replies.append(f"{info}\n\n🔗 <code>{link}</code>")
                    else:
                        replies.append(
                            f"⚠️ Ссылка сформирована, но файл проверить не удалось "
                            f"({info}):\n🔗 <code>{link}</code>"
                        )
                else:
                    replies.append(f"🔗 <code>{link}</code>")

            await message.answer("\n\n➖➖➖\n\n".join(replies),
                                 disable_web_page_preview=True)
        finally:
            if session is not None:
                await session.close()

    return dp


# --------------------------------------------------------------------------- #
#  Запуск
# --------------------------------------------------------------------------- #
async def main() -> None:
    load_dotenv()
    cfg = Config()

    errors = cfg.validate()
    if errors:
        for e in errors:
            logger.error(e)
        logger.error("Исправь .env и перезапусти бота.")
        raise SystemExit(1)

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(cfg)

    me = await bot.get_me()
    logger.info("Бот @%s запущен. Разрешённых пользователей: %s",
                me.username,
                len(cfg.allowed_users) or "все")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
