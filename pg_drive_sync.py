"""
PasarGuard -> Google Drive subscription sync
============================================

Забирает подписки пользователей из панели PasarGuard (локально, по 127.0.0.1 —
поэтому блокировка публичного домена панели не мешает), заливает их содержимое
в файлы на Google Drive и отдаёт постоянные ссылки вида:

    https://www.googleapis.com/drive/v3/files/{FILE_ID}?key={API_KEY}&alt=media

File ID у файла на Drive не меняется при обновлении содержимого, поэтому ссылка
подписки стабильна. Клиент (v2rayNG, sing-box, Clash) скачивает конфиг по
настоящему googleapis.com — он доступен, даже если домен панели заблокирован.

Запись на Drive — через service account (JSON). Чтение клиентом — по API-ключу
(файл публикуется как «доступен всем по ссылке»).

Все настройки берутся из .env (см. .env.example).
"""

import hashlib
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

# httplib2 (внутри google-api-client) не имеет таймаута по умолчанию: одно
# подвисшее соединение к Google вешает весь процесс навсегда. Ставим глобальный
# таймаут сокета, чтобы такой запрос падал с ошибкой, а не висел.
socket.setdefaulttimeout(60)

import requests
from dotenv import load_dotenv

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("pg-drive-sync")

# drive.file — доступ только к файлам, созданным самим приложением. Несенситивный
# scope: не требует тяжёлой верификации приложения при публикации в Production.
DRIVE_SCOPES = [os.getenv("GOOGLE_DRIVE_SCOPE")
                or "https://www.googleapis.com/auth/drive.file"]
API_MEDIA_BASE = "https://www.googleapis.com/drive/v3/files"


# --------------------------------------------------------------------------- #
#  Конфигурация
# --------------------------------------------------------------------------- #
def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self) -> None:
        # PasarGuard
        self.panel_base_url = (os.getenv("PANEL_BASE_URL")
                               or "http://127.0.0.1:8000").strip().rstrip("/")
        self.panel_admin_username = (os.getenv("PANEL_ADMIN_USERNAME") or "").strip()
        self.panel_admin_password = os.getenv("PANEL_ADMIN_PASSWORD") or ""
        self.verify_tls = _get_bool("PANEL_VERIFY_TLS", True)

        # какой формат подписки забирать
        self.sub_user_agent = (os.getenv("SUB_USER_AGENT")
                               or "v2rayNG/1.8.5").strip()
        self.sub_client_type = (os.getenv("SUB_CLIENT_TYPE") or "").strip().strip("/")

        # писать googleapis-ссылку в заметку (note) юзера в панели
        self.panel_write_note = _get_bool("PANEL_WRITE_NOTE", False)

        # название подписки (добавляется в ссылку как #title; многие клиенты
        # показывают его как имя подписки). Пусто = не добавлять.
        self.sub_title = (os.getenv("SUB_TITLE") or "").strip()

        # кого синхронизировать (пусто = всех активных)
        raw_users = (os.getenv("TARGET_USERS") or "").strip()
        self.target_users = [u.strip() for u in raw_users.split(",") if u.strip()]
        self.only_active = _get_bool("ONLY_ACTIVE_USERS", True)

        # Google Drive — чтение (клиент по API-ключу)
        self.google_api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
        self.drive_folder_id = (os.getenv("DRIVE_FOLDER_ID") or "").strip()
        self.file_name_template = (os.getenv("FILE_NAME_TEMPLATE")
                                   or "{username}.txt").strip()

        # Google Drive — запись. Два режима:
        #   oauth           — от имени личного аккаунта (работает на бесплатном Gmail)
        #   service_account — через сервис-аккаунт (нужен Shared Drive / Workspace)
        self.sa_file = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
        self.oauth_client_id = (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        self.oauth_client_secret = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        self.oauth_refresh_token = (os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN") or "").strip()

        mode = (os.getenv("DRIVE_AUTH_MODE") or "").strip().lower()
        if not mode:
            mode = "oauth" if self.oauth_refresh_token else "service_account"
        self.auth_mode = mode

        # состояние и вывод
        state_dir = os.getenv("STATE_DIR") or "/var/lib/pasarguard-drive-sync"
        self.state_file = Path(os.getenv("STATE_FILE") or f"{state_dir}/state.json")
        self.links_file = Path(os.getenv("LINKS_FILE") or f"{state_dir}/links.json")

        # периодичность
        self.sync_interval = int(os.getenv("SYNC_INTERVAL") or "0")

    def validate(self) -> list[str]:
        errors = []
        if not self.panel_admin_username:
            errors.append("PANEL_ADMIN_USERNAME не задан.")
        if not self.panel_admin_password:
            errors.append("PANEL_ADMIN_PASSWORD не задан.")
        if self.auth_mode == "oauth":
            if not self.oauth_client_id:
                errors.append("GOOGLE_OAUTH_CLIENT_ID не задан.")
            if not self.oauth_client_secret:
                errors.append("GOOGLE_OAUTH_CLIENT_SECRET не задан.")
            if not self.oauth_refresh_token:
                errors.append("GOOGLE_OAUTH_REFRESH_TOKEN не задан.")
        elif self.auth_mode == "service_account":
            if not self.sa_file:
                errors.append("GOOGLE_SERVICE_ACCOUNT_FILE не задан.")
            elif not Path(self.sa_file).is_file():
                errors.append(f"Файл service account не найден: {self.sa_file}")
        else:
            errors.append(f"Неизвестный DRIVE_AUTH_MODE: {self.auth_mode}")
        if not self.google_api_key:
            errors.append("GOOGLE_API_KEY не задан (нужен для ссылки чтения).")
        if not self.drive_folder_id:
            errors.append("DRIVE_FOLDER_ID не задан (папка для файлов).")
        return errors

    def build_drive_credentials(self):
        if self.auth_mode == "oauth":
            return UserCredentials(
                None,
                refresh_token=self.oauth_refresh_token,
                client_id=self.oauth_client_id,
                client_secret=self.oauth_client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=DRIVE_SCOPES,
            )
        return service_account.Credentials.from_service_account_file(
            self.sa_file, scopes=DRIVE_SCOPES)


# --------------------------------------------------------------------------- #
#  PasarGuard API
# --------------------------------------------------------------------------- #
class PanelClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.verify = cfg.verify_tls
        self.token: str | None = None

    def login(self) -> None:
        url = f"{self.cfg.panel_base_url}/api/admin/token"
        resp = self.session.post(
            url,
            data={
                "username": self.cfg.panel_admin_username,
                "password": self.cfg.panel_admin_password,
                "grant_type": "password",
            },
            timeout=30,
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        logger.info("Авторизовались в панели как %s", self.cfg.panel_admin_username)

    def list_users(self) -> list[dict]:
        """Возвращает список пользователей (с пагинацией)."""
        users: list[dict] = []
        offset, limit = 0, 100
        while True:
            resp = self.session.get(
                f"{self.cfg.panel_base_url}/api/users",
                # load_sub=true заставляет панель заполнить subscription_url.
                params={"offset": offset, "limit": limit, "load_sub": "true"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("users", data if isinstance(data, list) else [])
            users.extend(batch)
            total = data.get("total") if isinstance(data, dict) else None
            offset += limit
            if not batch or (total is not None and offset >= total):
                break
        return users

    def subscription_content(self, user: dict) -> str:
        """Скачивает содержимое подписки пользователя (как это видит клиент)."""
        sub_url = user.get("subscription_url") or ""
        if not sub_url:
            raise ValueError("у пользователя нет subscription_url")
        # Берём только путь (+query) и всегда стучимся к панели по localhost —
        # публичный домен из ссылки может быть заблокирован, а контент одинаков.
        parsed = urlparse(sub_url)
        path = parsed.path if parsed.scheme else sub_url
        if parsed.query:
            path = f"{path}?{parsed.query}"
        full = urljoin(self.cfg.panel_base_url + "/", path.lstrip("/"))
        if self.cfg.sub_client_type:
            full = full.rstrip("/") + "/" + self.cfg.sub_client_type
        resp = self.session.get(
            full,
            headers={"User-Agent": self.cfg.sub_user_agent},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text

    def set_note(self, username: str, note: str) -> None:
        """Обновляет только поле note у пользователя (остальное не трогает)."""
        resp = self.session.put(
            f"{self.cfg.panel_base_url}/api/user/{quote(username)}",
            json={"note": note},
            timeout=30,
        )
        resp.raise_for_status()


# --------------------------------------------------------------------------- #
#  Google Drive
# --------------------------------------------------------------------------- #
class DriveClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        creds = cfg.build_drive_credentials()
        self.service = build("drive", "v3", credentials=creds,
                             cache_discovery=False)

    def _ensure_public(self, file_id: str) -> None:
        try:
            self.service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            # 'anyone' уже выдан — ок; остальное пробрасываем.
            if exc.resp.status not in (400, 409):
                raise

    def upload(self, file_id: str | None, name: str, content: str) -> str:
        media = MediaInMemoryUpload(content.encode("utf-8"),
                                    mimetype="text/plain", resumable=False)
        if file_id:
            # Обновляем существующий файл — ID (и ссылка) сохраняются.
            self.service.files().update(
                fileId=file_id, media_body=media, supportsAllDrives=True,
            ).execute()
            return file_id
        body = {"name": name}
        if self.cfg.drive_folder_id:
            body["parents"] = [self.cfg.drive_folder_id]
        try:
            created = self.service.files().create(
                body=body, media_body=media, fields="id", supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            # В режиме drive.file папка-родитель может быть недоступна приложению
            # (404 на parent) — создаём файл в корне My Drive вместо падения.
            if exc.resp.status == 404 and "parents" in body:
                logger.warning("Папка %s недоступна, создаю файл в корне Drive",
                               self.cfg.drive_folder_id)
                created = self.service.files().create(
                    body={"name": name}, media_body=media, fields="id",
                ).execute()
            else:
                raise
        file_id = created["id"]
        self._ensure_public(file_id)
        return file_id


# --------------------------------------------------------------------------- #
#  Состояние
# --------------------------------------------------------------------------- #
def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def media_link(file_id: str, api_key: str, title: str = "") -> str:
    url = f"{API_MEDIA_BASE}/{quote(file_id)}?key={quote(api_key)}&alt=media"
    if title:
        # Фрагмент после # на сервер не уходит (Google отдаёт файл как есть),
        # но многие клиенты используют его как название подписки.
        url += "#" + quote(title)
    return url


# --------------------------------------------------------------------------- #
#  Один проход синхронизации
# --------------------------------------------------------------------------- #
def sync_once(cfg: Config, drive: DriveClient) -> None:
    panel = PanelClient(cfg)
    panel.login()
    users = panel.list_users()
    logger.info("Пользователей в панели: %d", len(users))

    state = load_state(cfg.state_file)   # username -> {file_id, hash}
    links: dict[str, str] = {}
    changed = created = skipped = failed = noted = 0
    processed = 0
    total = len(users)

    for user in users:
        username = user.get("username")
        if not username:
            continue
        if cfg.target_users and username not in cfg.target_users:
            continue
        if cfg.only_active and user.get("status") not in (None, "active", "on_hold"):
            continue

        processed += 1
        if processed % 20 == 0:
            logger.info("…обработано %d/%d (создано %d, ошибок %d), сохраняю прогресс",
                        processed, total, created, failed)
            save_json(cfg.state_file, state)
            save_json(cfg.links_file, links)

        try:
            content = panel.subscription_content(user)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] не удалось получить подписку: %s", username, exc)
            failed += 1
            continue

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        entry = state.get(username, {})
        file_id = entry.get("file_id")

        try:
            if file_id and entry.get("hash") == digest:
                skipped += 1
            else:
                name = cfg.file_name_template.format(username=username)
                new_id = drive.upload(file_id, name, content)
                if file_id:
                    changed += 1
                else:
                    created += 1
                    file_id = new_id
                state[username] = {"file_id": file_id, "hash": digest}
        except Exception as exc:  # noqa: BLE001  (HttpError, таймаут сокета и пр.)
            logger.warning("[%s] ошибка Drive: %s", username, exc)
            failed += 1
            continue

        link = media_link(state[username]["file_id"], cfg.google_api_key,
                          cfg.sub_title)
        links[username] = link

        # По желанию: пишем ссылку в note юзера (видно в панели). Не затираем
        # заметки, написанные вручную — меняем только пустые или те, где уже
        # стоит наша googleapis-ссылка.
        if cfg.panel_write_note:
            current_note = user.get("note") or ""
            if current_note != link and (
                    current_note == "" or "googleapis.com/drive" in current_note):
                try:
                    panel.set_note(username, link)
                    noted += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] не удалось записать note: %s", username, exc)

    save_json(cfg.state_file, state)
    save_json(cfg.links_file, links)
    logger.info("Готово: создано %d, обновлено %d, без изменений %d, ошибок %d, "
                "note обновлено %d. Ссылки: %s",
                created, changed, skipped, failed, noted, cfg.links_file)


# --------------------------------------------------------------------------- #
#  Точка входа
# --------------------------------------------------------------------------- #
def main() -> None:
    load_dotenv()
    cfg = Config()
    errors = cfg.validate()
    if errors:
        for e in errors:
            logger.error(e)
        logger.error("Исправь .env и перезапусти.")
        sys.exit(1)

    logger.info("Режим авторизации Drive: %s", cfg.auth_mode)
    drive = DriveClient(cfg)

    if cfg.sync_interval > 0:
        logger.info("Режим цикла: синхронизация каждые %d сек.", cfg.sync_interval)
        while True:
            try:
                sync_once(cfg, drive)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка прохода синхронизации: %s", exc)
            time.sleep(cfg.sync_interval)
    else:
        sync_once(cfg, drive)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
