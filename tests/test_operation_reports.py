import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from hospital_agent.services.operation_reports import (
    _deduplicate_operations,
    _operations_in_period,
    classify_operation,
    iter_operation_files,
    operation_summary,
    parse_birth_date_from_content,
    parse_operation_description,
    parse_operation_datetime,
    parse_operation_from_content,
    parse_recommendation,
    previous_operation_summary,
    read_docx_text,
    shorten_operation_description,
    shorten_operation_name,
)


class OperationReportParsingTests(unittest.TestCase):
    def test_report_deduplicates_same_operation_from_different_files(self):
        operation = {
            "patient": "Иванов И.И.",
            "datetime": datetime(2026, 7, 27, 7, 30),
            "operation": "КАГ",
            "source_file": "first.docx",
        }
        duplicate = {**operation, "source_file": "copy.docx"}

        result = _deduplicate_operations([operation, duplicate])

        self.assertEqual(result, [operation])

    def test_duty_period_does_not_duplicate_operation_at_0800_boundary(self):
        start = datetime(2026, 7, 26, 8, 0)
        end = datetime(2026, 7, 27, 8, 0)
        operations = [
            {"id": "before", "datetime": datetime(2026, 7, 26, 7, 59)},
            {"id": "start", "datetime": start},
            {"id": "inside", "datetime": datetime(2026, 7, 27, 7, 59)},
            {"id": "end", "datetime": end},
        ]

        selected = _operations_in_period(operations, start, end)

        self.assertEqual([operation["id"] for operation in selected], ["start", "inside"])

    def test_operation_datetime_allows_spaces_around_separator(self):
        content = "Дата и время операции: 20 .04.2026 23:30"

        self.assertEqual(
            parse_operation_datetime(content),
            datetime(2026, 4, 20, 23, 30),
        )

    def test_operation_datetime_allows_spaces_inside_digits(self):
        cases = {
            "Дата и время операции: 25.04.2026 14:4 0": datetime(
                2026, 4, 25, 14, 40
            ),
            "Дата и время операции: 1 9 .02.2026 13 : 00": datetime(
                2026, 2, 19, 13, 0
            ),
        }
        for content, expected in cases.items():
            with self.subTest(content=content):
                self.assertEqual(parse_operation_datetime(content), expected)

    def test_operation_is_inferred_when_header_has_no_name(self):
        content = (
            "Операция: 217 Операционная №2.\n"
            "Описание операции: выполнена ангиография артерий нижней конечности, "
            "проведена ангиопластика подколенной артерии и артерий голени. "
            "Исход: удовлетворительный"
        )

        self.assertEqual(parse_operation_from_content(content), "БАП артерий НК")

    def test_empty_and_word_lock_files_are_not_operation_candidates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.docx").touch()
            (root / "~$locked.docx").write_bytes(b"temporary")
            expected = root / "operation.docx"
            expected.write_bytes(b"non-empty")
            (root / "notes.txt").write_text("not a protocol", encoding="utf-8")

            self.assertEqual(iter_operation_files([root]), [expected])

    def test_operation_date_is_not_used_as_birth_date(self):
        content = (
            "Дата и время операции: 21.04.2026 21:45\n"
            "Ф.И.О. больного: Иванов Иван Иванович, возраст 37"
        )

        self.assertEqual(parse_birth_date_from_content(content), "")

    def test_birth_date_is_read_from_patient_line(self):
        content = (
            "Дата и время операции: 21.04.2026 21:45\n"
            "Ф.И.О. больного: Иванов Иван Иванович, "
            "дата рождения 02.03.1989, возраст 37"
        )

        self.assertEqual(parse_birth_date_from_content(content), "02.03.1989")

    def test_full_recommendation_label_is_supported(self):
        content = (
            "Рекомендовано: наблюдение дежурного врача "
            "Расходные материалы Йодсодержащий контраст"
        )

        self.assertEqual(parse_recommendation(content), "наблюдение дежурного врача")

    def test_operation_name_uses_requested_abbreviations(self):
        cases = {
            "Коронарография. В условиях ЭКМО": "КАГ. ЭКМО",
            "Церебральная ангиография бассейна ОСА справа": "ЦАГ ОСА прав.",
            (
                "Коронарография. Локальная эндоваскулярная трансартериальная "
                "тромбоаспирация из I ветки тупого края."
            ): "КАГ. ТА I ВТК.",
            "Попытка тромбоаспирации ВСА слева": "поп. ТА ВСА лев.",
            "Баллонная ангиопластика артерий голени": "БАП голени",
            "Баллонная БАП артерий голени": "БАП голени",
            "Частичная ЦАГ. ТА/ТА из СМА": "Частичная ЦАГ. ТА из СМА",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(shorten_operation_name(source), expected)

    def test_operation_description_compacts_protocol_boilerplate(self):
        description = (
            "Под МИА 0,5% р-ром новокаина 10,0 мл, в положении на спине "
            "выполнена пункция правой лучевой артерии по «Сельдингеру» "
            "с установкой интродьюсера 6F. "
            "Далее выполнена церебральная ангиография в стандартных проекциях. "
            "Интродьюсер удален, гемостаз места пункции, наложена давящая повязка. "
            "В ходе исследования выявлено: окклюзия средней мозговой артерии."
        )

        self.assertEqual(
            shorten_operation_description(description),
            "Доступ: правой лучевой артерии, 6F. окклюзия СМА.",
        )

    def test_access_is_compacted_without_word_performed(self):
        description = (
            "Под МИА новокаином 0,5% 10 мл пункция правой бедренной артерии "
            "по “Сельдингеру” с установкой интродьюсера 6 Fr. "
            "Проведена ангиография артерий нижней конечности."
        )

        self.assertEqual(
            shorten_operation_description(description),
            "Доступ: правой бедренной артерии, 6Fr. "
            "Проведена АГ артерий нижней конечности.",
        )

    def test_description_stops_at_outcome_without_intermediate_space(self):
        content = (
            "Описание операции: Кровоток после вмешательства TIMI 3."
            "Исход: переведен в отделение."
            "Рекомендовано: наблюдение."
        )

        self.assertEqual(
            parse_operation_description(content),
            "Кровоток после вмешательства TIMI 3.",
        )

    def test_invalid_docx_is_skipped_instead_of_raising(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.docx"
            path.write_text("not a zip archive", encoding="utf-8")

            self.assertIsNone(read_docx_text(path))

    def test_report_classification_recognizes_short_thrombaspiration(self):
        self.assertEqual(classify_operation("ЦАГ. ТА СМА"), 2)

    def test_report_uses_correct_time_and_recommendation_keys(self):
        operation = {
            "patient": "Иванов И.И.",
            "age": "50",
            "department": "кардиология",
            "operation": "КАГ",
            "datetime": datetime(2026, 7, 26, 10, 0),
            "time_beginning": "10:00",
            "time_duration": 20,
            "description": "Описание",
            "recommendation": "Наблюдение",
            "surgeon": "идрисов",
        }

        current = operation_summary(operation)
        previous = previous_operation_summary(operation)

        self.assertEqual(current["time_beginning"], "10:00")
        self.assertNotIn("time_beginnig", current)
        self.assertEqual(previous["recommendation"], "Наблюдение")
        self.assertNotIn("recomendation", previous)


if __name__ == "__main__":
    unittest.main()
