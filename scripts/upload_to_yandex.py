import boto3
import os
import sys
import argparse
from botocore.exceptions import ClientError, NoCredentialsError
import mimetypes
from datetime import datetime
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- НАСТРОЙКИ ---
YANDEX_ACCESS_KEY_ID = os.getenv("YANDEX_ACCESS_KEY_ID")
YANDEX_SECRET_ACCESS_KEY = os.getenv("YANDEX_SECRET_ACCESS_KEY")
YANDEX_ACCESS_KEY_ID = os.getenv("YANDEX_BUCKET")
YANDEX_SECRET_ACCESS_KEY = os.getenv("YANDEX_ENDPOINT")
MAX_WORKERS = 3  # Количество параллельных загрузок
# -----------------

class ProgressTracker:
    """Отслеживание прогресса загрузки"""
    def __init__(self, total_files, total_size):
        """Инициализирует объект и его рабочее состояние."""
        self.total_files = total_files
        self.total_size = total_size
        self.completed_files = 0
        self.completed_size = 0
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.failed_files = []
        
    def update(self, file_size, success=True, file_path=None):
        """Обновляет счетчики прогресса после обработки очередного файла."""
        with self.lock:
            self.completed_files += 1
            if success:
                self.completed_size += file_size
            else:
                self.failed_files.append(file_path)
            self._print_progress()
    
    def _print_progress(self):
        """Печатает текущий прогресс операции в консоль."""
        elapsed = time.time() - self.start_time
        percent = (self.completed_files / self.total_files) * 100
        speed = self.completed_size / elapsed if elapsed > 0 else 0
        speed_mb = speed / (1024 * 1024)
        
        completed_mb = self.completed_size / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024)
        
        bar_length = 40
        filled = int(bar_length * self.completed_files / self.total_files)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        print(f"\r[{bar}] {self.completed_files}/{self.total_files} файлов "
              f"({completed_mb:.1f}/{total_mb:.1f} MB) "
              f"[{speed_mb:.1f} MB/s] [{elapsed:.0f}с]", end='')

def setup_s3_client():
    """Создание и проверка S3 клиента"""
    try:
        session = boto3.session.Session()
        s3_client = session.client(
            service_name='s3',
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
        )
        # Проверка подключения
        s3_client.list_buckets()
        print("✓ Клиент S3 успешно создан и подключен к Yandex Cloud")
        return s3_client
    except NoCredentialsError:
        print("✗ Ошибка: Не удалось найти учетные данные. Проверьте ACCESS_KEY и SECRET_KEY.")
        return None
    except Exception as e:
        print(f"✗ Ошибка при создании клиента S3: {e}")
        return None

def upload_file(s3_client, file_path, object_name, content_type):
    """Загружает один файл"""
    try:
        s3_client.upload_file(
            file_path, 
            BUCKET_NAME, 
            object_name,
            ExtraArgs={'ContentType': content_type}
        )
        return True, None
    except Exception as e:
        return False, str(e)

def upload_folder(s3_client, folder_path, preserve_structure=True):
    """Загружает папку с параллельной загрузкой и прогрессом"""
    if not os.path.isdir(folder_path):
        print(f"✗ Папка не найдена: '{folder_path}'")
        return 0
    
    folder_path = os.path.normpath(folder_path)
    folder_name = os.path.basename(folder_path)
    
    print(f"\n📁 Сканирование папки: {folder_path}")
    
    # Собираем все файлы с информацией
    files_to_upload = []
    total_size = 0
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            file_size = os.path.getsize(file_path)
            
            # Определяем object_name
            if preserve_structure:
                rel_path = os.path.relpath(file_path, folder_path).replace('\\', '/')
                object_name = f"{folder_name}/{rel_path}"
            else:
                object_name = file
            
            # Определяем content_type
            content_type, _ = mimetypes.guess_type(file_path)
            if content_type is None:
                content_type = 'application/dicom' if file.lower().endswith('.dcm') else 'application/octet-stream'
            
            files_to_upload.append({
                'path': file_path,
                'size': file_size,
                'object_name': object_name,
                'content_type': content_type
            })
            total_size += file_size
    
    total_files = len(files_to_upload)
    
    if total_files == 0:
        print("✗ В папке нет файлов для загрузки")
        return 0
    
    total_size_mb = total_size / (1024 * 1024)
    print(f"   Найдено файлов: {total_files}")
    print(f"   Общий размер: {total_size_mb:.2f} MB")
    
    if total_files > 0:
        print(f"\n📂 Пример загрузки:")
        print(f"   Локальный: {files_to_upload[0]['path']}")
        print(f"   В бакете:  {files_to_upload[0]['object_name']}")
    
    # Создаем трекер прогресса
    progress = ProgressTracker(total_files, total_size)
    
    print(f"\n🚀 Начинаем загрузку {total_files} файлов...")
    print("   (Нажмите Ctrl+C для остановки)\n")
    
    # Загружаем файлы параллельно
    success_count = 0
    failed_files = []
    
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Создаем задачи
            future_to_file = {
                executor.submit(
                    upload_file, 
                    s3_client, 
                    f['path'], 
                    f['object_name'], 
                    f['content_type']
                ): f for f in files_to_upload
            }
            
            # Обрабатываем результаты
            for future in as_completed(future_to_file):
                file_info = future_to_file[future]
                success, error = future.result()
                
                if success:
                    success_count += 1
                else:
                    failed_files.append((file_info['path'], error))
                
                progress.update(file_info['size'], success, file_info['path'] if not success else None)
    
    except KeyboardInterrupt:
        print("\n\n⚠️ Загрузка прервана пользователем")
    
    # Итоги
    print(f"\n\n" + "="*60)
    print(f"📊 ИТОГИ ЗАГРУЗКИ:")
    print(f"   Успешно: {success_count}/{total_files} файлов")
    
    completed_mb = progress.completed_size / (1024 * 1024)
    print(f"   Загружено: {completed_mb:.1f}/{total_size_mb:.1f} MB")
    
    if success_count > 0:
        avg_speed = progress.completed_size / (time.time() - progress.start_time)
        avg_speed_mb = avg_speed / (1024 * 1024)
        print(f"   Средняя скорость: {avg_speed_mb:.1f} MB/s")
    
    if preserve_structure and success_count > 0:
        print(f"   Структура папок: СОХРАНЕНА в папке '{folder_name}'")
    
    if failed_files:
        print(f"\n❌ Ошибки ({len(failed_files)} файлов):")
        for f, err in failed_files[:5]:
            print(f"     - {os.path.basename(f)}: {err[:100]}")
    
    return success_count

def main():
    """Точка входа CLI: разбирает аргументы и запускает сценарий скрипта."""
    parser = argparse.ArgumentParser(
        description='Загрузить файл или папку в Yandex Cloud Bucket',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  %(prog)s "C:\\Users\\Angio_hir1\\Desktop\\dicom\\patient"
  %(prog)s "C:\\Users\\Angio_hir1\\Desktop\\file.dcm" --flat
  %(prog)s "C:\\Users\\Angio_hir1\\Desktop\\dicom" --workers 5
        """
    )
    parser.add_argument('path', help='Путь к файлу или папке')
    parser.add_argument('--flat', '-f', action='store_true', 
                       help='Загружать все файлы в корень (без сохранения структуры)')
    parser.add_argument('--workers', '-w', type=int, default=3,
                       help='Количество параллельных загрузок (по умолчанию 3)')
    
    args = parser.parse_args()
    
    # Обновляем количество воркеров
    global MAX_WORKERS
    MAX_WORKERS = args.workers
    
    if not os.path.exists(args.path):
        print(f"✗ Путь не существует: '{args.path}'")
        sys.exit(1)
    
    s3_client = setup_s3_client()
    if not s3_client:
        sys.exit(1)
    
    start_time = datetime.now()
    
    if os.path.isfile(args.path):
        print(f"\n📄 Обнаружен файл: {args.path}")
        file_size = os.path.getsize(args.path)
        
        # Определяем object_name
        if args.flat:
            object_name = os.path.basename(args.path)
        else:
            folder_name = os.path.basename(os.path.dirname(args.path))
            object_name = f"{folder_name}/{os.path.basename(args.path)}"
        
        content_type = 'application/dicom' if args.path.lower().endswith('.dcm') else 'application/octet-stream'
        
        print(f"   Загрузка как: {object_name}")
        print(f"   Размер: {file_size/(1024*1024):.2f} MB")
        
        success, error = upload_file(s3_client, args.path, object_name, content_type)
        
        if success:
            print(f"\n✓ Файл успешно загружен")
        else:
            print(f"\n✗ Ошибка: {error}")
            sys.exit(1)
    else:
        success_count = upload_folder(s3_client, args.path, preserve_structure=not args.flat)
        if success_count == 0:
            sys.exit(1)
    
    duration = (datetime.now() - start_time).total_seconds()
    print(f"\n⏱ Общее время: {duration:.2f} сек")

if __name__ == "__main__":
    main()