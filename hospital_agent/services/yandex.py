import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import boto3
import pydicom
from pydicom.errors import InvalidDicomError


LOGGER = logging.getLogger("hospital_agent.services.yandex")


YANDEX_ENVIRONMENT_KEYS = {
    "bucket": "YANDEX_BUCKET",
    "endpoint": "YANDEX_ENDPOINT",
    "access_key": "YANDEX_ACCESS_KEY_ID",
    "secret_key": "YANDEX_SECRET_ACCESS_KEY",
}


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
        missing = [
            environment_key
            for attribute, environment_key in YANDEX_ENVIRONMENT_KEYS.items()
            if not str(getattr(self, attribute) or "").strip()
        ]
        if missing:
            message = (
                "Yandex Object Storage configuration is incomplete: missing "
                + ", ".join(missing)
                + "; configure .env or the process environment"
            )
            LOGGER.error(message)
            raise RuntimeError(message)
        self.client = boto3.session.Session().client(
            service_name="s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

    def check_connection(self) -> None:
        """Проверяет доступность Object Storage текущими ключами."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as exc:
            LOGGER.error(
                "Yandex Object Storage bucket is unavailable: bucket=%s error=%s",
                self.bucket,
                exc,
            )
            raise RuntimeError(
                f"Yandex Object Storage bucket {self.bucket!r} is unavailable"
            ) from exc

    def upload_dicom_with_retries(
        self,
        file_path: str | Path,
        object_name: str,
        retry_attempts: int,
        retry_delay: float,
    ) -> bool:
        """Загружает один DICOM-файл с повторами."""
        last_error: Exception | None = None
        for attempt in range(max(retry_attempts, 1)):
            try:
                self.client.upload_file(
                    str(file_path),
                    self.bucket,
                    object_name,
                    ExtraArgs={"ContentType": "application/dicom"},
                )
                return True
            except Exception as exc:
                last_error = exc
                if attempt < max(retry_attempts, 1) - 1:
                    time.sleep(retry_delay)
        LOGGER.warning(
            "Yandex upload failed: object=%s attempts=%s error=%s",
            object_name,
            max(retry_attempts, 1),
            last_error,
        )
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
        uploaded_objects: list[dict[str, Any]] = []

        files = [path for path in source.rglob("*") if path.is_file()]
        for file_path in sorted(files, key=_dicom_sort_key):
            file_size = file_path.stat().st_size
            relative_name = file_path.relative_to(source).as_posix()
            object_name = f"{yandex_folder}/{relative_name}"
            if self.upload_dicom_with_retries(file_path, object_name, retry_attempts, retry_delay):
                uploaded_files += 1
                uploaded_bytes += file_size
                uploaded_objects.append(
                    {
                        "name": relative_name,
                        "size": file_size,
                        "url": self.client.generate_presigned_url(
                            "get_object",
                            Params={"Bucket": self.bucket, "Key": object_name},
                            ExpiresIn=int(timedelta(days=3).total_seconds()),
                        ),
                    }
                )
            else:
                failed_files.append(str(file_path))

        return {
            "yandex_folder": yandex_folder,
            "uploaded_files": uploaded_files,
            "uploaded_bytes": uploaded_bytes,
            "failed_files": failed_files,
            "files": uploaded_objects,
            "dicom_link": f"s3://{self.bucket}/{yandex_folder}",
        }

    def delete_folder(self, yandex_folder: str) -> int:
        """Удаляет все объекты исследования из Yandex Object Storage."""
        deleted = 0
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": f"{yandex_folder.rstrip('/')}/",
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**request)
            objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
            if objects:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
                deleted += len(objects)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
        return deleted


def _dicom_sort_key(path: Path) -> tuple[int, int, str]:
    """Сортирует DICOM по серии и InstanceNumber, сохраняя стабильный fallback."""
    try:
        dataset = pydicom.dcmread(
            str(path),
            stop_before_pixels=True,
            specific_tags=["SeriesNumber", "InstanceNumber"],
        )
        series_number = int(getattr(dataset, "SeriesNumber", 0) or 0)
        instance_number = int(getattr(dataset, "InstanceNumber", 0) or 0)
        return series_number, instance_number, path.as_posix()
    except (OSError, ValueError, TypeError, AttributeError, InvalidDicomError):
        return 0, 0, path.as_posix()
