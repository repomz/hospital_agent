import logging
import os
import time
from pathlib import Path
from typing import Any

import boto3


LOGGER = logging.getLogger("hospital_agent.services.yandex")


class YandexStorage:
    """Минимальный S3-клиент для загрузки DICOM-файлов в Yandex Object Storage."""

    def __init__(
        self,
        bucket: str | None = None,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ):
        """Создает клиент из явных параметров или переменных окружения."""
        self.bucket = bucket or os.getenv("YANDEX_BUCKET")
        self.endpoint = endpoint or os.getenv("YANDEX_ENDPOINT")
        self.access_key = access_key or os.getenv("YANDEX_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.getenv("YANDEX_SECRET_ACCESS_KEY")
        self.client = boto3.session.Session().client(
            service_name="s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

    def check_connection(self) -> None:
        """Проверяет доступность Object Storage текущими ключами."""
        self.client.list_buckets()

    def upload_dicom_with_retries(
        self,
        file_path: str | Path,
        object_name: str,
        retry_attempts: int,
        retry_delay: float,
    ) -> bool:
        """Загружает один DICOM-файл с повторами."""
        for attempt in range(retry_attempts):
            try:
                self.client.upload_file(
                    str(file_path),
                    self.bucket,
                    object_name,
                    ExtraArgs={"ContentType": "application/dicom"},
                )
                return True
            except Exception:
                if attempt < retry_attempts - 1:
                    time.sleep(retry_delay)
                else:
                    LOGGER.exception("Yandex upload failed: %s", file_path)
        return False

    def upload_folder(
        self,
        folder_path: str | Path,
        yandex_folder: str,
        retry_attempts: int,
        retry_delay: float,
    ) -> dict[str, Any]:
        """Загружает все файлы из локальной папки исследования в Yandex."""
        source = Path(folder_path)
        uploaded_files = 0
        uploaded_bytes = 0
        failed_files: list[str] = []

        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            file_size = file_path.stat().st_size
            object_name = f"{yandex_folder}/{file_path.name}"
            if self.upload_dicom_with_retries(file_path, object_name, retry_attempts, retry_delay):
                uploaded_files += 1
                uploaded_bytes += file_size
            else:
                failed_files.append(str(file_path))

        return {
            "yandex_folder": yandex_folder,
            "uploaded_files": uploaded_files,
            "uploaded_bytes": uploaded_bytes,
            "failed_files": failed_files,
        }
