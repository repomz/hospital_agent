"""
DICOM Monitor - Background service for monitoring CT Brain studies
Запускается в фоне и записывает новые исследования в txt файл
"""

import os
import sys
import time
import json
import signal
from datetime import datetime, timedelta
from pathlib import Path

# DICOM imports
from pynetdicom import AE
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind
from pydicom.dataset import Dataset

# ==============================
# НАСТРОЙКИ (ВСЕ ЗДЕСЬ)
# ==============================
PACS_IP = "pacs2022.okb.local"
PACS_PORT = 11112
PACS_AE = "PACSOKB"
LOCAL_AE = "DICOM_MONITOR"

CHECK_INTERVAL = 10
LOOKBACK_MINUTES = 720

# Файлы
OUTPUT_FILE = r"C:\Users\Angio_hir1\Desktop\marat\projects\pacs\monitor\ct_brain_studies.txt"
PROCESSED_FILE = r"C:\Users\Angio_hir1\Desktop\marat\projects\pacs\monitor\processed_studies.json"
LOG_FILE = r"C:\Users\Angio_hir1\Desktop\marat\projects\pacs\monitor\monitor_debug.log"

# ==============================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ==============================
running = True
processed_studies = set()

# ==============================
# ЛОГИРОВАНИЕ
# ==============================
def log_message(msg):
    """Записать сообщение в лог-файл"""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except:
        pass

# ==============================
# ОСТАНОВКА ПРИ ЗАВЕРШЕНИИ
# ==============================
def signal_handler(signum, frame):
    """Обрабатывает сигнал завершения и переводит скрипт в режим мягкой остановки."""
    global running
    log_message("Shutdown signal received")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ==============================
# ЗАГРУЗКА/СОХРАНЕНИЕ ОБРАБОТАННЫХ ИССЛЕДОВАНИЙ
# ==============================
def load_processed():
    """Загружает список уже обработанных исследований из JSON-файла."""
    global processed_studies
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, 'r') as f:
                processed_studies = set(json.load(f))
            log_message(f"Loaded {len(processed_studies)} processed studies")
        except Exception as e:
            log_message(f"Error loading processed: {e}")
            processed_studies = set()

def save_processed():
    """Сохраняет список уже обработанных исследований в JSON-файл."""
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(list(processed_studies), f)
        log_message(f"Saved {len(processed_studies)} processed studies")
    except Exception as e:
        log_message(f"Error saving processed: {e}")

# ==============================
# ПОИСК ИССЛЕДОВАНИЙ CT BRAIN
# ==============================
def find_ct_brain_studies():
    """Найти исследования CT Brain за последние LOOKBACK_MINUTES минут"""
    studies = []
    
    try:
        log_message(f"Connecting to PACS {PACS_IP}:{PACS_PORT} (AE={PACS_AE})")
        ae = AE(ae_title=LOCAL_AE)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        
        assoc = ae.associate(PACS_IP, PACS_PORT, ae_title=PACS_AE)
        if not assoc.is_established:
            log_message("ERROR: Cannot establish association with PACS")
            return studies
        
        log_message("Association established")
        
        # Диапазон дат
        end_date = datetime.now()
        start_date = end_date - timedelta(minutes=LOOKBACK_MINUTES)
        date_range = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
        log_message(f"Searching studies from {start_date} to {end_date}")
        
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = ""
        ds.PatientName = ""
        ds.StudyDate = date_range
        ds.StudyDescription = ""
        ds.ModalitiesInStudy = "CT"
        
        log_message("Sending C-FIND request...")
        responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
        
        total_found = 0
        brain_found = 0
        
        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01) and identifier:
                total_found += 1
                description = str(identifier.get("StudyDescription", ""))
                
                # Только Brain исследования
                if "brain" in description.lower() or "head" in description.lower():
                    brain_found += 1
                    uid = identifier.get("StudyInstanceUID", "")
                    if uid:
                        # Форматируем имя пациента
                        patient_name = str(identifier.get("PatientName", "Unknown"))
                        if "^" in patient_name:
                            parts = patient_name.split("^")
                            patient_name = f"{parts[0]} {parts[1][0] if len(parts)>1 else ''}"
                        
                        studies.append({
                            "uid": uid,
                            "patient": patient_name,
                            "date": str(identifier.get("StudyDate", "")),
                            "description": description,
                            "instances": str(identifier.get("NumberOfStudyRelatedInstances", ""))
                        })
        
        assoc.release()
        log_message(f"Search complete: {total_found} total, {brain_found} brain studies found")
        
    except Exception as e:
        log_message(f"ERROR in search: {str(e)}")
        import traceback
        log_message(traceback.format_exc())
    
    return studies

# ==============================
# ЗАПИСЬ В ФАЙЛ
# ==============================
def write_to_file(studies):
    """Записать новые исследования в файл"""
    if not studies:
        return
    
    try:
        # Создаем папку если нужно
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        
        with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
            for study in studies:
                # Время обнаружения
                check_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Формируем строку
                line = f"{check_time} | {study['patient']:<30} | {study['date']} | {study['instances']:<5} | {study['description']}\n"
                f.write(line)
                f.flush()
                log_message(f"Written: {study['patient']} - {study['description']}")
        
    except Exception as e:
        log_message(f"Error writing to file: {e}")

# ==============================
# ОСНОВНОЙ ЦИКЛ
# ==============================
def main():
    """Точка входа CLI: разбирает аргументы и запускает сценарий скрипта."""
    global running, processed_studies
    
    log_message("=" * 60)
    log_message("DICOM MONITOR STARTED")
    log_message(f"PACS: {PACS_IP}:{PACS_PORT} (AE={PACS_AE})")
    log_message(f"Check interval: {CHECK_INTERVAL}s")
    log_message(f"Lookback: {LOOKBACK_MINUTES} minutes")
    log_message(f"Output file: {OUTPUT_FILE}")
    log_message("=" * 60)
    
    # Загружаем обработанные исследования
    load_processed()
    
    # Создаем файл с заголовком если не существует
    if not os.path.exists(OUTPUT_FILE):
        try:
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                f.write("=" * 100 + "\n")
                f.write("CT BRAIN STUDIES MONITOR\n")
                f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 100 + "\n")
                f.write(f"{'Time':<20} {'Patient':<30} {'Date':<12} {'Images':<6} {'Description'}\n")
                f.write("-" * 100 + "\n")
            log_message("Output file created")
        except Exception as e:
            log_message(f"Error creating output file: {e}")
    
    # Основной цикл
    iteration = 0
    while running:
        try:
            iteration += 1
            log_message(f"--- Check #{iteration} ---")
            
            # Поиск новых исследований
            studies = find_ct_brain_studies()
            
            # Фильтруем новые
            new_studies = [s for s in studies if s['uid'] not in processed_studies]
            log_message(f"New studies: {len(new_studies)}")
            
            if new_studies:
                # Добавляем в обработанные
                for s in new_studies:
                    processed_studies.add(s['uid'])
                
                # Записываем в файл
                write_to_file(new_studies)
                
                # Сохраняем список обработанных
                save_processed()
            
            # Ждем до следующей проверки
            for _ in range(CHECK_INTERVAL):
                if not running:
                    break
                time.sleep(1)
                
        except Exception as e:
            log_message(f"Error in main loop: {e}")
            import traceback
            log_message(traceback.format_exc())
            time.sleep(30)
    
    # Сохраняем перед выходом
    save_processed()
    log_message("DICOM MONITOR STOPPED")

if __name__ == "__main__":
    main()