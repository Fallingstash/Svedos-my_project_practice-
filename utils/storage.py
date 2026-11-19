import mimetypes
import httpx
import uuid
from pathlib import Path
from urllib.parse import quote

from utils.config import (
    MAX_UPLOAD_SIZE_BYTES,
    get_server_secret,
    get_storage_backend,
)


PRIVATE_BUCKET = "svedos-audio"
def _data_directory():
    from utils import session_store

    return session_store.DATA_DIRECTORY


class AudioStorageError(RuntimeError):
    pass


class LocalAudioStorage:
    def save_upload(self, owner_id, session_id, filename, data, content_type=None):
        extension = Path(filename).suffix.lower()
        data_directory = _data_directory()
        directory = (data_directory / "history" / session_id).resolve()
        root = (data_directory / "history").resolve()
        if root not in directory.parents:
            raise ValueError("Недопустимый идентификатор сессии.")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}{extension}"
        try:
            with path.open("xb") as target:
                target.write(data)
        except OSError as error:
            raise AudioStorageError("Не удалось сохранить аудиофайл локально.") from error
        return str(path)

    def download_bytes(self, owner_id, session_id, object_key):
        path = Path(object_key).resolve()
        allowed_directory = (
            _data_directory() / "history" / session_id
        ).resolve()
        if allowed_directory not in path.parents:
            raise PermissionError("Аудиофайл не принадлежит указанной сессии.")
        try:
            return path.read_bytes()
        except OSError as error:
            raise AudioStorageError("Аудиофайл сессии отсутствует или недоступен.") from error

    def exists(self, owner_id, session_id, object_key):
        path = Path(object_key).resolve()
        allowed_directory = (
            _data_directory() / "history" / session_id
        ).resolve()
        return allowed_directory in path.parents and path.is_file()

    def delete_object(self, owner_id, session_id, object_key):
        path = Path(object_key).resolve()
        allowed_directory = (
            _data_directory() / "history" / session_id
        ).resolve()
        if allowed_directory not in path.parents:
            raise PermissionError("Нельзя удалить файл другой сессии.")
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise AudioStorageError("Не удалось удалить аудиофайл.") from error


class SupabaseAudioStorage:
    def __init__(self, client=None):
        url = get_server_secret("SUPABASE_URL")
        key = get_server_secret("SUPABASESERVICEROLE_KEY")
        if not url or not key:
            raise AudioStorageError("Конфигурация Supabase Storage неполна.")
        if client is None:
            try:
                from supabase import create_client
            except ImportError as error:
                raise AudioStorageError(
                    "Официальный SDK Supabase не установлен."
                ) from error
            try:
                client = create_client(url, key)
            except Exception as error:
                raise AudioStorageError("Не удалось инициализировать Supabase Storage.") from error
        self.client = client
        self.bucket = client.storage.from_(PRIVATE_BUCKET)
        self.url = url.rstrip("/")
        self.service_key = key

    def ensure_private_bucket(self):
        try:
            bucket_info = self.client.storage.get_bucket(PRIVATE_BUCKET)
        except Exception as error:
            raise AudioStorageError(
                "Приватный bucket svedos-audio не найден или недоступен."
            ) from error
        is_public = (
            bucket_info.get("public")
            if isinstance(bucket_info, dict)
            else getattr(bucket_info, "public", None)
        )
        if is_public is not False:
            raise AudioStorageError(
                "Bucket svedos-audio должен быть приватным."
            )

    def save_upload(self, owner_id, session_id, filename, data, content_type=None):
        key = f"{owner_id}/{session_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
        content_type = content_type or mimetypes.guess_type(filename)[0]
        try:
            from tempfile import TemporaryDirectory

            with TemporaryDirectory(prefix="svedos-upload-") as temp_dir:
                local_path = Path(temp_dir) / Path(filename).name
                with local_path.open("xb") as target:
                    target.write(data)
                self.bucket.upload(
                    key,
                    local_path,
                    {
                        "content-type": content_type or "application/octet-stream",
                        "upsert": "false",
                    },
                )
        except Exception as upload_error:
            try:
                self.bucket.remove([key])
            except Exception as cleanup_error:
                raise AudioStorageError(
                    "Загрузка аудио в Supabase не подтверждена, и не удалось "
                    "проверить очистку временного объекта. Проверьте bucket "
                    "перед повторной загрузкой."
                ) from cleanup_error
            raise AudioStorageError(
                "Не удалось загрузить аудио в Supabase Storage."
            ) from upload_error
        return key

    def download_bytes(self, owner_id, session_id, object_key):
        if not object_key.startswith(f"{owner_id}/{session_id}/"):
            raise PermissionError("Аудиофайл не принадлежит указанной сессии.")
        try:
            return bytes(self.bucket.download(object_key))
        except Exception as error:
            raise AudioStorageError(
                "Аудиофайл отсутствует или недоступен в Supabase Storage."
            ) from error

    def download_to_path(self, owner_id, session_id, object_key, destination):
        if not object_key.startswith(f"{owner_id}/{session_id}/"):
            raise PermissionError("Аудиофайл не принадлежит указанной сессии.")
        url = (
            f"{self.url}/storage/v1/object/{PRIVATE_BUCKET}/"
            f"{quote(object_key, safe='/')}"
        )
        destination = Path(destination)
        try:
            with httpx.stream(
                "GET",
                url,
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                },
                timeout=60.0,
            ) as response:
                response.raise_for_status()
                with destination.open("xb") as target:
                    for chunk in response.iter_bytes(64 * 1024):
                        target.write(chunk)
        except (httpx.HTTPError, OSError) as error:
            destination.unlink(missing_ok=True)
            raise AudioStorageError(
                "Аудиофайл отсутствует или недоступен в Supabase Storage."
            ) from error

    def exists(self, owner_id, session_id, object_key):
        if not object_key.startswith(f"{owner_id}/{session_id}/"):
            return False
        try:
            self.bucket.info(object_key)
            return True
        except Exception as error:
            if getattr(error, "status_code", None) == 404:
                return False
            raise AudioStorageError(
                "Не удалось проверить аудиофайл в Supabase Storage."
            ) from error

    def delete_object(self, owner_id, session_id, object_key):
        if not object_key.startswith(f"{owner_id}/{session_id}/"):
            raise PermissionError("Нельзя удалить файл другой сессии.")
        try:
            self.bucket.remove([object_key])
        except Exception as error:
            raise AudioStorageError("Не удалось удалить аудио из Supabase Storage.") from error


def create_audio_storage():
    if get_storage_backend() == "supabase":
        return SupabaseAudioStorage()
    return LocalAudioStorage()


def validate_backend_ready():
    if get_storage_backend() != "supabase":
        return
    from utils.supabase_store import get_supabase_store

    store = get_supabase_store()
    try:
        with store.connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM sessions LIMIT 0")
    except Exception as error:
        raise AudioStorageError(
            "Supabase подключён, но схема базы недоступна. "
            "Примените SQL-миграции."
        ) from error
    finally:
        store.close()
    create_audio_storage().ensure_private_bucket()


def save_audio_upload(owner_id, session_id, uploaded_file, role):
    storage = create_audio_storage()
    data = uploaded_file.getbuffer()
    if data.nbytes > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError("Максимальный размер аудиофайла — 100 МБ.")
    object_key = storage.save_upload(
        owner_id,
        session_id,
        uploaded_file.name,
        data,
        getattr(uploaded_file, "type", None),
    )
    metadata = {
        "object_key": object_key,
        "role": role,
        "original_name": Path(uploaded_file.name).name,
        "content_type": getattr(uploaded_file, "type", None),
        "byte_size": len(data),
    }
    return object_key, metadata


def _session_contains_file(session, object_key):
    fields = ("track_path", "reference_path", "beat_path", "vocal_path")
    if object_key in (session.get(field) for field in fields):
        return True
    return any(
        item.get("object_key") == object_key
        for item in session.get("file_metadata", [])
    )


def authorize_session_audio(session_id, object_key, user_id=None, share_token=None):
    from utils.session_store import load_session, load_shared_session

    if share_token:
        session = load_shared_session(share_token)
        if session is None or session["id"] != session_id:
            raise PermissionError("Публичная ссылка недействительна или отозвана.")
    elif user_id:
        session = load_session(session_id, user_id)
        if session is None:
            raise PermissionError("Нет доступа к аудиофайлу этой сессии.")
    else:
        raise PermissionError("Для получения аудио требуется проверка доступа.")

    if not _session_contains_file(session, object_key):
        raise PermissionError("Файл не относится к этой сессии.")
    owner_id = session.get("user_id")
    if not owner_id:
        raise PermissionError("Не удалось подтвердить владельца аудиофайла.")
    return session, owner_id


def load_audio_bytes(session_id, object_key, user_id=None, share_token=None):
    _, owner_id = authorize_session_audio(
        session_id,
        object_key,
        user_id=user_id,
        share_token=share_token,
    )
    return create_audio_storage().download_bytes(owner_id, session_id, object_key)


def materialize_audio(
    session_id,
    object_key,
    directory,
    user_id=None,
    share_token=None,
):
    backend = get_storage_backend()
    if backend == "local":
        authorize_session_audio(
            session_id,
            object_key,
            user_id=user_id,
            share_token=share_token,
        )
        path = Path(object_key).resolve()
        if not path.is_file():
            raise AudioStorageError("Аудиофайл сессии отсутствует.")
        return str(path)

    _, owner_id = authorize_session_audio(
        session_id,
        object_key,
        user_id=user_id,
        share_token=share_token,
    )
    suffix = Path(object_key).suffix or ".audio"
    path = Path(directory) / f"{uuid.uuid4().hex}{suffix}"
    try:
        storage = create_audio_storage()
        if isinstance(storage, SupabaseAudioStorage):
            storage.download_to_path(owner_id, session_id, object_key, path)
        else:
            data = storage.download_bytes(owner_id, session_id, object_key)
            with path.open("xb") as audio_file:
                audio_file.write(data)
    except AudioStorageError:
        raise
    except OSError as error:
        raise AudioStorageError("Не удалось подготовить аудио для анализа.") from error
    return str(path)


def delete_session_audio(owner_id, session_id, object_keys):
    storage = create_audio_storage()
    for object_key in dict.fromkeys(object_keys):
        storage.delete_object(owner_id, session_id, object_key)
