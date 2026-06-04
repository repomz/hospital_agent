import os
import shutil
from datetime import datetime
import logging

# НАСТРОЙКИ
LOG_DIR = r"C:\Users\Angio_hir1\Desktop\marat\projects\pacs\report"
LOG_FILE = "log1.txt"
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)

def setup_logging():
    """Настраивает систему логирования"""
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(LOG_PATH, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

def get_month_folder():
    """Возвращает название папки для текущего месяца на русском"""
    months = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }
    current_month = datetime.now().month
    return months[current_month]

def need_to_copy(source_path, target_path):
    """
    Определяет, нужно ли копировать файл
    """
    # Если файла нет в целевой папке - копируем
    if not os.path.exists(target_path):
        return True
    
    # Сравниваем размеры
    source_size = os.path.getsize(source_path)
    target_size = os.path.getsize(target_path)
    
    # Если размер отличается - копируем
    if source_size != target_size:
        return True
    
    # Сравниваем дату изменения
    source_mtime = os.path.getmtime(source_path)
    target_mtime = os.path.getmtime(target_path)
    
    # Если исходный файл новее - копируем
    if source_mtime > target_mtime:
        return True
    
    # Во всех остальных случаях пропускаем
    return False

def get_relative_path(source_root, full_path):
    """Возвращает относительный путь файла относительно корневой папки"""
    return os.path.relpath(full_path, source_root)

def format_size(size_bytes):
    """Форматирует размер в человеко-читаемый формат"""
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} ТБ"

def sync_folders(source_folder, target_folder, logger):
    """
    Синхронизирует папки: копирует только новые или измененные файлы
    """
    # Проверяем существование исходной папки
    if not os.path.exists(source_folder):
        logger.error(f"Исходная папка не существует: {source_folder}")
        return False
    
    # Создаем целевую папку, если её нет
    if not os.path.exists(target_folder):
        try:
            os.makedirs(target_folder)
            logger.info(f"Создана целевая папка: {target_folder}")
        except Exception as e:
            logger.error(f"Не удалось создать папку {target_folder}: {e}")
            return False
    
    copied_count = 0
    skipped_count = 0
    
    # Обходим все файлы в исходной папке
    for root, dirs, files in os.walk(source_folder):
        # Создаем аналогичную структуру папок в целевой директории
        rel_path = get_relative_path(source_folder, root)
        
        if rel_path == '.':
            target_root = target_folder
        else:
            target_root = os.path.join(target_folder, rel_path)
            if not os.path.exists(target_root):
                os.makedirs(target_root)
        
        # Копируем файлы
        for file in files:
            source_file = os.path.join(root, file)
            target_file = os.path.join(target_root, file)
            
            if need_to_copy(source_file, target_file):
                try:
                    shutil.copy2(source_file, target_file)
                    copied_count += 1
                    file_size = os.path.getsize(source_file)
                    logger.info(f"КОПИРОВАН: {os.path.join(rel_path, file)} ({format_size(file_size)})")
                except Exception as e:
                    logger.error(f"Ошибка при копировании {file}: {e}")
            else:
                skipped_count += 1
    
    # Итоговый отчет
    logger.info(f"Скопировано файлов: {copied_count}")
    logger.info(f"Пропущено файлов: {skipped_count}")
    
    return True

def main():
    """Точка входа CLI: разбирает аргументы и запускает сценарий скрипта."""
    logger = setup_logging()
    
    try:
        current_month = get_month_folder()
        current_year = datetime.now().year
        
        logger.info(f"Запуск синхронизации: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        
        # Формируем пути
        source = rf"T:\Ангиоблок\Идрисов\{current_year}\{current_month}"
        target = rf"C:\Users\Angio_hir1\Desktop\{current_year} Опер №2\{current_month}"
        
        logger.info(f"Источник: {source}")
        logger.info(f"Приемник: {target}")
        
        # Проверяем существование исходной папки
        if not os.path.exists(source):
            logger.error(f"Папка не найдена: {source}")
            return
        
        # Выполняем синхронизацию
        success = sync_folders(source, target, logger)
        
        if success:
            logger.info("СИНХРОНИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА")
        else:
            logger.error("СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА С ОШИБКАМИ")
            
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")

if __name__ == "__main__":
    main()