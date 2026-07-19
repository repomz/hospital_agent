import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

LOGGER = logging.getLogger("hospital_agent.services.operation_reports")

# --- НАСТРОЙКИ ПО УМОЛЧАНИЮ ---
DEFAULT_TARGET_DIR_1 = r"C:\Users\Angio_hir1\Desktop\Операции 2026"
DEFAULT_TARGET_DIR_2 = r"C:\Users\Angio_hir1\Desktop\2026 Опер №2"
DEFAULT_PLAN_DIR = r"C:\Users\Angio_hir1\Desktop\План Отчеты"
DEFAULT_REPORT_DIR = r"C:\Users\Angio_hir1\Desktop\План Отчеты\отчеты"
DEFAULT_PERIOD = 1
DEFAULT_TIME = "08:00"


def normalize_spaces(value):
    """Нормализует пробельные символы в строке."""
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def format_patient_short(fio):
    """Сокращает ФИО до формата 'Фамилия И.О.'."""
    parts = normalize_spaces(fio).split()
    if len(parts) >= 2:
        initials = "".join(f"{part[0]}." for part in parts[1:] if part)
        return f"{parts[0]} {initials}"
    return normalize_spaces(fio)


def parse_date_value(value):
    """Преобразует дату формата DD.MM.YYYY/DD.MM.YY в datetime.date."""
    value = str(value or "").strip().replace("/", ".").replace("-", ".")
    for date_format in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def format_date_value(value):
    """Возвращает дату в формате DD.MM.YYYY или пустую строку."""
    parsed = parse_date_value(value)
    return parsed.strftime("%d.%m.%Y") if parsed else ""


def extract_birth_date(value):
    """Извлекает дату рождения из произвольного текста."""
    text = normalize_spaces(value)
    patterns = [
        r"(?:дата\s+рождения|д\.?\s*р\.?|рожд\.?|г\.?\s*р\.?)\s*:?\s*"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        r"\b(\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            birth_date = format_date_value(match.group(1))
            if birth_date:
                return birth_date
    return ""


def strip_birth_date(value):
    """Удаляет дату рождения и служебные маркеры из строки пациента."""
    text = normalize_spaces(value)
    text = re.sub(
        r"(?:дата\s+рождения|д\.?\s*р\.?|рожд\.?|г\.?\s*р\.?)\s*:?\s*"
        r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b", "", text)
    return normalize_spaces(text)


def parse_birth_date_from_content(content):
    """Извлекает дату рождения пациента из протокола операции."""
    match = re.search(
        r"Ф\.И\.О\.\s*больного\s*:\s*([^\n\r]*)",
        content,
        flags=re.IGNORECASE,
    )
    return extract_birth_date(match.group(1)) if match else ""


def scan_and_filter_files(base_paths, start_period, end_period):
    """Сканирует несколько папок с операциями"""
    if isinstance(base_paths, str):
        base_paths = [base_paths]
    
    results = []
    all_files = []
    
    for base_path in base_paths:
        base_path = Path(base_path)
        if not base_path.exists():
            LOGGER.warning("Operations directory does not exist: %s", base_path)
            continue
        
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.lower().endswith('.docx'):
                    all_files.append(Path(root) / file)
    
    if not all_files:
        return []
    
    for i, file_path in enumerate(all_files, 1):
        if i % 50 == 0 or i == len(all_files):
            LOGGER.info("Analyzed operation files: %s/%s", i, len(all_files))
        
        result = analyze_file(file_path, start_period, end_period)
        if result:
            results.append(result)
    
    return results

def parse_time_string(time_str):
    """Нормализует время начала периода к формату HH:MM."""
    time_str = time_str.replace('.', ':')
    if ':' not in time_str:
        time_str = time_str + ':00'
    parts = time_str.split(':')
    if len(parts) >= 2:
        hour = parts[0].zfill(2)
        minute = parts[1].zfill(2)
        return f"{hour}:{minute}"
    return "08:00"

def get_start_datetime(period_days, time_str):
    """Вычисляет дату и время начала отчетного периода."""
    now = datetime.now()
    time_str = parse_time_string(time_str)

    try:
        start_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        LOGGER.warning("Invalid report start time %r; using 08:00", time_str)
        start_time = datetime.strptime(DEFAULT_TIME, "%H:%M").time()

    start_date = now.date() - timedelta(days=period_days)
    return datetime.combine(start_date, start_time)

def read_docx_text(file_path):
    """Извлекает текст из .docx файла"""
    try:
        with ZipFile(file_path, 'r') as docx:
            with docx.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                
                texts = []
                for para in root.findall('.//w:p', ns):
                    para_text = []
                    for child in para:
                        if child.tag == f'{{{ns["w"]}}}r':
                            for text_elem in child.findall('.//w:t', ns):
                                if text_elem.text:
                                    para_text.append(text_elem.text)
                            para_text.append(' ')
                    if para_text:
                        texts.append(''.join(para_text).strip())
                
                full_text = '\n'.join(texts)
                
                # Добавляем пробелы между слитыми полями
                full_text = re.sub(r'(\d{2}\.\d{2}\.\d{4})(\d{2}:\d{2})', r'\1 \2', full_text)
                full_text = re.sub(r'(Дата и время операции:)(\d{2}\.\d{2}\.\d{4})', r'\1 \2', full_text)
                full_text = re.sub(r'(Ф\.И\.О\. больного:)([А-Я])', r'\1 \2', full_text)
                full_text = re.sub(r'(возраст\s*\d+)([А-Я])', r'\1 \2', full_text)
                full_text = re.sub(r'(Карта стационарного больного[^\n]+?)(Дата)', r'\1\n\2', full_text)
                full_text = re.sub(r'(Дата и время операции:[^\n]+?)(Ф\.И\.О\.)', r'\1\n\2', full_text)
                full_text = re.sub(r'(Ф\.И\.О\. больного:[^\n]+?)(Диагноз)', r'\1\n\2', full_text)
                
                return full_text
    except (OSError, KeyError, ET.ParseError) as exc:
        LOGGER.warning("Cannot read DOCX file %s: %s", file_path, exc)
        return None

def parse_operation_datetime(content):
    """Извлекает дату и время операции"""
    pattern = (
        r"Дата\s+и\s+время\s+операции\s*:\s*"
        r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})\s+"
        r"(\d{1,2})\s*:\s*(\d{2})"
    )
    match = re.search(pattern, content)
    if match:
        try:
            day, month, year, hour, minute = (int(value) for value in match.groups())
            return datetime(year, month, day, hour, minute)
        except ValueError:
            return None
    return None

def parse_patient_from_content(content):
    """Извлекает ФИО и возраст"""
    pattern = r"Ф\.И\.О\. больного:\s*([^,]+),\s*возраст\s*(\d+)"
    match = re.search(pattern, content)
    if match:
        fio = normalize_spaces(match.group(1).strip())
        age = match.group(2).strip()
        return format_patient_short(fio), age
    return None, None

def shorten_operation_name(operation):
    """Сокращает название операции"""
    operation = re.sub(r"\s+", " ", operation.replace("\xa0", " ")).strip()
    operation = re.sub(r"\s+([.,])", r"\1", operation)
    operation = re.sub(r"([.,])(?=[^\s\d])", r"\1 ", operation)

    operation = re.sub(r'Коронарография', 'КАГ.', operation, flags=re.IGNORECASE)
    operation = re.sub(r'Коронарошунтография', 'КАГ+шунтогр', operation, flags=re.IGNORECASE)
    operation = re.sub(r'Церебральная ангиография', 'ЦАГ.', operation, flags=re.IGNORECASE)
    operation = re.sub(r'Ангиография', 'АГ', operation, flags=re.IGNORECASE)
    operation = re.sub(r'тромбаспирация', 'ТА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'реканализация', 'МР', operation, flags=re.IGNORECASE)
    operation = re.sub(r'реканализации', 'МР', operation, flags=re.IGNORECASE)
    operation = re.sub(r'стентирование', 'стент', operation, flags=re.IGNORECASE)
    operation = re.sub(r'ангиопластика', 'БАП', operation, flags=re.IGNORECASE)
    operation = re.sub(r'ангиопластикой', 'БАП', operation, flags=re.IGNORECASE)
    operation = re.sub(r'бифуркационное', 'биф', operation, flags=re.IGNORECASE)   
    operation = re.sub(r'контрпульсатора', 'ВАБК', operation, flags=re.IGNORECASE) 
    operation = re.sub(r'справа', 'прав', operation, flags=re.IGNORECASE)
    operation = re.sub(r'слева', 'лев', operation, flags=re.IGNORECASE)
    operation = re.sub(r'попытка', 'try', operation, flags=re.IGNORECASE)
    operation = re.sub(r'электрокардиостимулятора', 'ЭКС', operation, flags=re.IGNORECASE)
    operation = re.sub(r'кардиостимулятора', 'ЭКС', operation, flags=re.IGNORECASE)
    
    # Заменяем артерии
    operation = re.sub(r'левой коронарной', 'стЛКА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'передняя нисходящая', 'ПНА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'передней нисходящей', 'ПНА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'огибающая', 'ОА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'огибающую', 'ОА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'огибающей', 'ОА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'ветвь тупого края', 'ВТК', operation, flags=re.IGNORECASE)
    operation = re.sub(r'прав\w*\s+коронарн\w*', 'ПКА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'лева\w*\s+коронарн\w*', 'ЛКА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'правая коронарная', 'ПКА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'правой', 'ПКА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'диагональная', 'ДА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'медианной', 'МА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'задняя нисходящая', 'ЗНА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'заднюю нисходящую', 'ЗНА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'заднебоковой', 'ЗБВ', operation, flags=re.IGNORECASE)
    operation = re.sub(r'базилярная артерия', 'БА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'средняя мозговая артерия', 'СМА', operation, flags=re.IGNORECASE)
    operation = re.sub(r'задняя мозговая артерия', 'ЗМА', operation, flags=re.IGNORECASE)
    
    # Заменяем сегменты
    operation = re.sub(r'проксимальн\w*\s+сегмент\w*', 'пр/3', operation, flags=re.IGNORECASE)
    operation = re.sub(r'средн\w*\s+сегмент\w*', 'ср/3', operation, flags=re.IGNORECASE)
    operation = re.sub(r'дистальн\w*\s+сегмент\w*', 'д/3', operation, flags=re.IGNORECASE)
    
    # Убираем лишнее
    operation = re.sub(r'\,', '', operation)
    operation = re.sub(r'\+', '', operation)
    operation = re.sub(r'тотальная|селективная|транслюминальная|баллонной|баллонная|баллонного|внутриаортального|первичная|механической|механическая|артерии|артерий|артерию|ствол|окклюзия|установка', '', operation, flags=re.IGNORECASE)
    operation = re.sub(r'\.{2,}', '.', operation)
    operation = re.sub(r'\s+\.', '.', operation)
    operation = re.sub(r'\s+', ' ', operation)
    
    if len(operation) > 100:
        operation = operation[:97] + "..."
    
    return operation.strip()

def parse_operation_from_content(content):
    """Извлекает и сокращает название операции"""
    patterns = [
        r"Операционная №\s*\d+\.?\s*(.+?)(?:\n|$)",
        r"Операция:\s*\d+\s*Операционная\s*\d+\.?\s*(.+?)(?:\n|$)",
        r"Операция:\s*\d+\s*(.+?)(?:\n|$)",
    ]
    
    operation = None
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            operation = match.group(1).strip()
            break
    
    if operation:
        operation = operation.split('Карта стационарного больного')[0].strip()
        return shorten_operation_name(operation)
    return None

def classify_operation(operation):
    """Классифицирует операцию по типу вмешательства для статистики отчета."""
    op_lower = operation.lower()
    
    is_cag = 'каг' in op_lower
    is_tsag = 'цаг' in op_lower
    
    has_stenting = 'стент' in op_lower
    has_angioplasty = 'бап' in op_lower or 'баллон' in op_lower
    has_thrombaspiration = 'тромбэкстр' in op_lower or 'тромбаспир' in op_lower
    has_recanalization = 'мр' in op_lower or 'реканализ' in op_lower
    
    if is_tsag:
        if has_thrombaspiration:
            return 2
        elif has_stenting or has_angioplasty or has_recanalization:
            return 3
        else:
            return 1
    elif is_cag:
        if has_stenting:
            return 6
        elif has_angioplasty:
            return 5
        else:
            return 4
    else:
        return 7

def get_plan_data(plan_dir, target_date):
    """Извлекает данные из файла плана для указанной даты"""
    # Находим понедельник и пятницу недели, содержащей target_date
    days_ahead = 0 - target_date.weekday()
    monday = target_date + timedelta(days=days_ahead)
    friday = monday + timedelta(days=4)
    date_pattern = f"{monday.strftime('%d.%m')}-{friday.strftime('%d.%m')}"
    
    plan_files = list(Path(plan_dir).glob(f"{date_pattern}.docx"))
    plan_files.extend(list(Path(plan_dir).glob(f"{date_pattern}.DOCX")))
    
    if not plan_files:
        plan_files = list(Path(plan_dir).glob(f"*{date_pattern}*.docx"))
    
    if not plan_files:
        return set(), []
    
    try:
        with ZipFile(plan_files[0], 'r') as docx:
            with docx.open('word/document.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                
                tables = root.findall('.//w:tbl', ns)
                if not tables:
                    return set(), []
                
                table = tables[0]
                rows = table.findall('.//w:tr', ns)
                
                # Ищем строку с нужной датой
                target_date_str = target_date.strftime("%d.%m")
                planned_patients = set()
                planned_details = []
                
                i = 0
                while i < len(rows):
                    cells = rows[i].findall('.//w:tc', ns)
                    if cells:
                        # Получаем текст первой ячейки (дата)
                        first_cell_text = []
                        for para in cells[0].findall('.//w:p', ns):
                            para_text = []
                            for text_elem in para.findall('.//w:t', ns):
                                if text_elem.text:
                                    para_text.append(text_elem.text)
                            if para_text:
                                first_cell_text.append(''.join(para_text))
                        first_cell_text = ' '.join(first_cell_text).strip()
                        
                        # Если нашли нужную дату
                        if first_cell_text == target_date_str and i + 1 < len(rows):
                            data_row = rows[i + 1]
                            data_cells = data_row.findall('.//w:tc', ns)
                            
                            # Извлекаем пациентов (колонка 1)
                            patient_lines = []
                            if len(data_cells) > 1:
                                # Сначала пробуем получить текст по параграфам
                                for para in data_cells[1].findall('.//w:p', ns):
                                    para_text = []
                                    for text_elem in para.findall('.//w:t', ns):
                                        if text_elem.text:
                                            para_text.append(text_elem.text)
                                    if para_text:
                                        line = ''.join(para_text).strip()
                                        if line:
                                            patient_lines.append(line)
                                # Если ничего не нашли, возможно данные в одной строке через пробелы
                                if not patient_lines:
                                    full_text = []
                                    for para in data_cells[1].findall('.//w:p', ns):
                                        para_text = []
                                        for text_elem in para.findall('.//w:t', ns):
                                            if text_elem.text:
                                                para_text.append(text_elem.text)
                                        if para_text:
                                            full_text.append(''.join(para_text))
                                    if full_text:
                                        # Разбиваем по пробелам, фильтруем пустые строки
                                        patient_lines = [p.strip() for p in ' '.join(full_text).split() if p.strip()]
                            
                            # Извлекаем отделения (колонка 2)
                            department_lines = []
                            if len(data_cells) > 2:
                                for para in data_cells[2].findall('.//w:p', ns):
                                    para_text = []
                                    for text_elem in para.findall('.//w:t', ns):
                                        if text_elem.text:
                                            para_text.append(text_elem.text)
                                    if para_text:
                                        line = ''.join(para_text).strip()
                                        if line:
                                            department_lines.append(line)
                                if not department_lines:
                                    full_text = []
                                    for para in data_cells[2].findall('.//w:p', ns):
                                        para_text = []
                                        for text_elem in para.findall('.//w:t', ns):
                                            if text_elem.text:
                                                para_text.append(text_elem.text)
                                        if para_text:
                                            full_text.append(''.join(para_text))
                                    if full_text:
                                        department_lines = [d.strip() for d in ' '.join(full_text).split() if d.strip()]
                            
                            # Извлекаем операции (колонка 3)
                            operation_lines = []
                            if len(data_cells) > 3:
                                for para in data_cells[3].findall('.//w:p', ns):
                                    para_text = []
                                    for text_elem in para.findall('.//w:t', ns):
                                        if text_elem.text:
                                            para_text.append(text_elem.text)
                                    if para_text:
                                        line = ''.join(para_text).strip()
                                        if line:
                                            operation_lines.append(line)
                                if not operation_lines:
                                    full_text = []
                                    for para in data_cells[3].findall('.//w:p', ns):
                                        para_text = []
                                        for text_elem in para.findall('.//w:t', ns):
                                            if text_elem.text:
                                                para_text.append(text_elem.text)
                                        if para_text:
                                            full_text.append(''.join(para_text))
                                    if full_text:
                                        operation_lines = [op.strip() for op in ' '.join(full_text).split() if op.strip()]
                            
                            # Создаем записи пациент-отделение-операция.
                            max_len = max(len(patient_lines), len(department_lines), len(operation_lines))
                            
                            for idx in range(max_len):
                                patient_raw = patient_lines[idx] if idx < len(patient_lines) else ""
                                department = department_lines[idx] if idx < len(department_lines) else ""
                                operation = operation_lines[idx] if idx < len(operation_lines) else ""
                                
                                if patient_raw:
                                    birth_date = extract_birth_date(patient_raw)
                                    patient = format_patient_short(strip_birth_date(patient_raw))
                                    # Извлекаем фамилию пациента для сопоставления
                                    patient_surname = patient.split()[0] if patient.split() else patient
                                    planned_patients.add(patient_surname)
                                    planned_details.append(
                                        {
                                            "patient": patient,
                                            "birth_date": birth_date,
                                            "department": normalize_spaces(department),
                                            "operation": normalize_spaces(operation),
                                        }
                                    )
                            
                            break  # Нашли нужную дату, выходим из цикла
                    i += 1
                
                return planned_patients, planned_details
                
    except Exception as exc:
        LOGGER.warning("Cannot parse operation plan file: %s", exc)
        return set(), []

def get_plan_for_date_range(plan_dir, target_date):
    """
    Получает план для указанной даты
    Возвращает: (planned_patients_set, planned_details_list)
    """
    return get_plan_data(plan_dir, target_date)

def analyze_file(file_path, start_period, end_period):
    """Извлекает из DOCX операции пациента, дату и тип операции в заданном периоде."""
    content = read_docx_text(file_path)
    
    if not content:
        return None
    
    op_datetime = parse_operation_datetime(content)
    if not op_datetime or not (start_period <= op_datetime <= end_period):
        return None
    
    patient, age = parse_patient_from_content(content)
    operation = parse_operation_from_content(content)
    
    if not patient or not operation:
        return None
    
    return {
        'patient': patient,
        'age': age,
        'operation': operation,
        'datetime': op_datetime
    }

def sort_operations_by_category(operations):
    """Добавляет категорию операции и сортирует список для печати отчета."""
    for op in operations:
        op['category'] = classify_operation(op['operation'])
    operations.sort(key=lambda x: (x['category'], x['datetime']))
    return operations

def split_operations_by_plan(operations, planned_patients):
    """Разделяет операции на плановые и экстренные"""
    planned = []
    emergency = []
    
    for op in operations:
        patient_surname = op['patient'].split()[0] if op['patient'].split() else op['patient']
        
        is_planned = any(
            patient_surname.lower() == planned_patient.split()[0].lower() 
            for planned_patient in planned_patients
        )
        
        if is_planned:
            planned.append(op)
        else:
            emergency.append(op)
    
    return planned, emergency


def parse_medical_record_number(content):
    """Извлекает номер карты стационарного больного из протокола."""
    match = re.search(
        r"Карта\s+стационарного\s+больного\s*([0-9]+\s*[-–]\s*[0-9]+)",
        content,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", "", match.group(1)).replace("–", "-") if match else None


def department_from_record_number(record_number):
    """Определяет отделение по началу номера карты."""
    if not record_number:
        return ""
    if record_number.startswith("44"):
        return "кардиология"
    if record_number.startswith("42"):
        return "рсц"
    if record_number.startswith("26"):
        return "сосудистая хирургия"
    if record_number.startswith("179"):
        return "неврология"
    return ""


def parse_operation_description(content):
    """Извлекает описание операции из протокола."""
    match = re.search(
        r"Описание\s+операции\s*:\s*(.*?)(?=\s+Исход\s*:|\s+Рек-но\s*:|"
        r"\s+Расходные\s+материалы|\s+Опер\.\s*:|$)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalize_spaces(match.group(1)) if match else ""


def parse_recommendation(content):
    """Извлекает рекомендации после операции."""
    match = re.search(
        r"(?:Рек-но|Рекомендовано)\s*:\s*"
        r"(.*?)(?=\s+Расходные\s+материалы|\s+Опер\.\s*:|$)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalize_spaces(match.group(1)) if match else ""


def parse_operation_duration_min(content):
    """Извлекает длительность операции в минутах."""
    match = re.search(
        r"Длительность\s+операции\s*:\s*(?:(\d+)\s*час\w*)?\s*(?:(\d+)\s*мин\w*)?",
        content,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return str(hours * 60 + minutes)


def parse_surgeon(content):
    """Извлекает хирурга после подписи 'Опер.:_______'."""
    match = re.search(r"Опер\.\s*:\s*_*\s*([^\n\r]+)", content, flags=re.IGNORECASE)
    return normalize_spaces(match.group(1)) if match else ""


def truncate_text(value, limit=120):
    """Ограничивает длинное текстовое поле для JSON-отчета."""
    text = normalize_spaces(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def iter_operation_files(base_paths):
    """Ищет DOCX-протоколы операций в одной или нескольких папках."""
    if isinstance(base_paths, str):
        base_paths = [base_paths]

    files = []
    for base_path in base_paths:
        base_path = Path(base_path)
        if not base_path.exists():
            LOGGER.warning("Operations directory does not exist: %s", base_path)
            continue
        for root, _dirs, filenames in os.walk(base_path):
            for filename in filenames:
                if filename.lower().endswith(".docx"):
                    files.append(Path(root) / filename)
    return sorted(files)


def analyze_operation_file(file_path):
    """Извлекает полную структуру операции из DOCX-протокола."""
    content = read_docx_text(file_path)
    if not content:
        return None

    op_datetime = parse_operation_datetime(content)
    patient, age = parse_patient_from_content(content)
    operation = parse_operation_from_content(content)
    if not op_datetime or not patient or not operation:
        return None

    return {
        "patient": patient,
        "surname": patient.split()[0].lower() if patient.split() else "",
        "birth_date": parse_birth_date_from_content(content),
        "age": age or "",
        "department": department_from_record_number(parse_medical_record_number(content)),
        "operation": operation,
        "datetime": op_datetime,
        "time_beginnig": op_datetime.strftime("%H:%M"),
        "time_duration": parse_operation_duration_min(content),
        "description": parse_operation_description(content),
        "recomendation": parse_recommendation(content),
        "surgeon": parse_surgeon(content),
        "source_file": str(file_path),
    }


def operation_summary(operation):
    """Формирует JSON-элемент проведенной операции."""
    return {
        "patient": operation["patient"],
        "age": operation["age"],
        "department": operation["department"],
        "operation": operation["operation"],
        "time_beginnig": operation["time_beginnig"],
        "time_duration": operation["time_duration"],
        "surgeon": operation["surgeon"],
    }


def previous_operation_summary(operation):
    """Формирует краткое описание прошлой операции пациента."""
    return {
        "date": operation["datetime"].strftime("%d.%m.%Y"),
        "operation": operation["operation"],
        "description": truncate_text(operation["description"]),
        "recomendation": truncate_text(operation["recomendation"]),
        "surgeon": operation["surgeon"],
    }


def same_patient(plan_item, operation):
    """Сопоставляет пациента плана с протоколом по фамилии и дате рождения."""
    plan_patient = str(plan_item.get("patient", ""))
    plan_surname = plan_patient.split()[0].lower() if plan_patient.split() else ""
    if not plan_surname or plan_surname != operation.get("surname"):
        return False

    plan_birth_date = plan_item.get("birth_date") or ""
    operation_birth_date = operation.get("birth_date") or ""
    if plan_birth_date and operation_birth_date:
        return plan_birth_date == operation_birth_date
    return not plan_birth_date


def build_operations_report_payload(
    period,
    start_period,
    end_period,
    period_operations,
    all_operations,
    planned_patients_for_period,
    planned_details_today,
):
    """Собирает JSON-отчет по операциям и сегодняшнему плану."""
    planned_ops, emergency_ops = split_operations_by_plan(
        list(period_operations),
        planned_patients_for_period,
    )
    planned_ops = sort_operations_by_category(planned_ops)
    emergency_ops = sort_operations_by_category(emergency_ops)

    today_planned_operations = []
    today_start = datetime.combine(end_period.date(), datetime.min.time())
    for plan_item in planned_details_today:
        if not isinstance(plan_item, dict):
            patient, department, operation = plan_item
            plan_item = {
                "patient": patient,
                "birth_date": "",
                "department": department,
                "operation": operation,
            }

        previous_operations = [
            previous_operation_summary(operation)
            for operation in all_operations
            if operation["datetime"] < today_start and same_patient(plan_item, operation)
        ]
        previous_operations.sort(key=lambda item: parse_date_value(item["date"]), reverse=True)
        today_planned_operations.append(
            {
                "patient": plan_item.get("patient", ""),
                "age": "",
                "department": plan_item.get("department", ""),
                "operation": plan_item.get("operation", ""),
                "previous_operations": previous_operations,
            }
        )

    return {
        "date": end_period.strftime("%d.%m.%Y"),
        "period_days": int(period),
        "period_start": start_period.strftime("%d.%m.%Y %H:%M"),
        "period_end": end_period.strftime("%d.%m.%Y %H:%M"),
        "planned_count": len(planned_ops),
        "emergency_total": len(emergency_ops),
        "today_planned_count": len(today_planned_operations),
        "planned_operations": [operation_summary(operation) for operation in planned_ops],
        "emergency_operations": [operation_summary(operation) for operation in emergency_ops],
        "today_planned_operations": today_planned_operations,
    }

def write_stats(f, operations, title):
    """Записывает статистику и список операций"""
    if not operations:
        f.write(f"{title} не было.\n\n")
        return
    
    stats = {'tsag_only': 0, 'tsag_with_ta': 0, 'tsag_with_other': 0,
             'cag_only': 0, 'cag_with_angioplasty': 0, 'cag_with_stenting': 0, 'other': 0}
    
    for op in operations:
        category = op['category']
        if category == 1:
            stats['tsag_only'] += 1
        elif category == 2:
            stats['tsag_with_ta'] += 1
        elif category == 3:
            stats['tsag_with_other'] += 1
        elif category == 4:
            stats['cag_only'] += 1
        elif category == 5:
            stats['cag_with_angioplasty'] += 1
        elif category == 6:
            stats['cag_with_stenting'] += 1
        else:
            stats['other'] += 1
    
    if stats['tsag_only'] > 0:
        f.write(f"  ЦАГ                         {stats['tsag_only']}\n")
    if stats['tsag_with_ta'] > 0:
        f.write(f"  ЦАГ + тромбаспирация        {stats['tsag_with_ta']}\n")
    if stats['tsag_with_other'] > 0:
        f.write(f"  ЦАГ + другие                {stats['tsag_with_other']}\n")
    if stats['cag_only'] > 0:
        f.write(f"  КАГ                         {stats['cag_only']}\n")
    if stats['cag_with_angioplasty'] > 0:
        f.write(f"  КАГ + ангиопластика         {stats['cag_with_angioplasty']}\n")
    if stats['cag_with_stenting'] > 0:
        f.write(f"  КАГ + стентирование         {stats['cag_with_stenting']}\n")
    if stats['other'] > 0:
        f.write(f"  Прочие операции             {stats['other']}\n")
    
    f.write("-" * 85 + "\n")
    f.write(f"{'№':<4} {'Пациент':<25} {'Операция'}\n")
    f.write("-" * 85 + "\n")
    
    for i, op in enumerate(operations, 1):
        patient_str = f"{op['patient']} ({op['age']} лет)"
        f.write(f"{i:<4} {patient_str:<25} {op['operation']}\n")
    
    f.write("-" * 85 + "\n\n")

def generate_report(operations, start_period, end_period, 
                   planned_patients_for_period, planned_details_today, report_dir):
    """
    Генерирует отчет
    planned_patients_for_period - плановые пациенты на ДАТУ НАЧАЛА ПЕРИОДА
    planned_details_today - детали плана на СЕГОДНЯ
    """
    # Разделяем операции, используя план на дату начала периода
    planned_ops, emergency_ops = split_operations_by_plan(operations, planned_patients_for_period)
    
    # Сортируем
    planned_ops = sort_operations_by_category(planned_ops)
    emergency_ops = sort_operations_by_category(emergency_ops)
    
    # Создаем папку для отчетов
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    
    # Имя файла в формате 17.04.26.txt
    output_filename = f"{datetime.now().strftime('%d.%m.%y')}.txt"
    output_filepath = report_path / output_filename
    
    with open(output_filepath, 'w', encoding='utf-8') as f:
        f.write("=" * 85 + "\n")
        f.write(f"ОТЧЕТ ПО ОПЕРАЦИЯМ\n")
        f.write(f"Период: с {start_period.strftime('%d.%m.%Y %H:%M')} по {end_period.strftime('%d.%m.%Y %H:%M')}\n")
        f.write("-" * 85 + "\n")
        f.write(f"  Плановые:   {len(planned_ops)}\n")
        f.write(f"  Экстренные: {len(emergency_ops)}\n")
        f.write(f"  ВСЕГО:      {len(operations)}\n")
        
        # ПЛАНОВЫЕ ОПЕРАЦИИ (сверялись с планом на дату начала периода)
        f.write("=" * 85 + "\n")
        f.write(" " * 25 + f"ПЛАНОВЫЕ ОПЕРАЦИИ за {start_period.strftime('%d.%m.%Y')}\n")
        f.write("=" * 85 + "\n")
        write_stats(f, planned_ops, "ПЛАНОВЫХ ОПЕРАЦИЙ")
        
        # ЭКСТРЕННЫЕ ОПЕРАЦИИ
        f.write("=" * 85 + "\n")
        f.write(" " * 35 + "ЭКСТРЕННЫЕ ОПЕРАЦИИ\n")
        f.write("=" * 85 + "\n")
        write_stats(f, emergency_ops, "ЭКСТРЕННЫХ ОПЕРАЦИЙ")
    
        # ТЕКУЩИЙ ПЛАН ОПЕРАЦИЙ (на сегодня)
        f.write("=" * 85 + "\n")
        f.write(" " * 25 + f"ТЕКУЩИЙ ПЛАН ОПЕРАЦИЙ на {datetime.now().strftime('%d.%m.%Y')}\n")
        f.write("=" * 85 + "\n")
        f.write("-" * 85 + "\n")

        if planned_details_today:
            f.write(f"{'№':<4} {'Пациент':<25} {'Отд':<8} {'Операция'}\n")
            f.write("-" * 85 + "\n")
            for i, plan_item in enumerate(planned_details_today, 1):
                if isinstance(plan_item, dict):
                    patient = plan_item.get("patient", "")
                    department = plan_item.get("department", "")
                    operation = plan_item.get("operation", "")
                else:
                    patient, department, operation = plan_item
                operation_short = shorten_operation_name(operation)
                # Ограничиваем длину строк для читаемости
                patient_short = patient[:25] if len(patient) > 25 else patient
                department_short = department[:8] if len(department) > 8 else department
                operation_short = operation_short[:50] if len(operation_short) > 50 else operation_short
                f.write(f"{i:<4} {patient_short:<25} {department_short:<8} {operation_short}\n")
        else:
            f.write("План операций на сегодня не найден.\n")

        f.write("-" * 85 + "\n")
        f.write("=" * 85 + "\n")
    
    return output_filepath


def generate_operations_report(
    period: int = DEFAULT_PERIOD,
    time_value: str = DEFAULT_TIME,
    dir1: str = DEFAULT_TARGET_DIR_1,
    dir2: str = DEFAULT_TARGET_DIR_2,
    plan_dir: str = DEFAULT_PLAN_DIR,
    report_dir: str = DEFAULT_REPORT_DIR,
) -> dict:
    """Сканирует DOCX и формирует JSON-отчет по операциям."""
    start_period = get_start_datetime(period, time_value)
    end_period = datetime.now()
    planned_patients, _ = get_plan_data(plan_dir, start_period)
    _, planned_details_today = get_plan_data(plan_dir, end_period)

    all_operations = []
    for file_path in iter_operation_files([dir1, dir2]):
        operation = analyze_operation_file(file_path)
        if operation:
            all_operations.append(operation)

    period_operations = [
        operation
        for operation in all_operations
        if start_period <= operation["datetime"] <= end_period
    ]

    text_report_path = Path(
        generate_report(
            period_operations,
            start_period,
            end_period,
            planned_patients,
            planned_details_today,
            report_dir,
        )
    )

    payload = build_operations_report_payload(
        period,
        start_period,
        end_period,
        period_operations,
        all_operations,
        planned_patients,
        planned_details_today,
    )

    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    json_report_path = report_path / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with json_report_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    return {
        "report": payload,
        "json_report_file": str(json_report_path),
        "text_report_file": str(text_report_path),
    }
