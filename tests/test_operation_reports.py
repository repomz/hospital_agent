import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from hospital_agent.services.operation_reports import (
    classify_operation,
    parse_birth_date_from_content,
    parse_operation_datetime,
    parse_recommendation,
    read_docx_text,
    shorten_operation_description,
    shorten_operation_name,
)


class OperationReportParsingTests(unittest.TestCase):
    def test_operation_datetime_allows_spaces_around_separator(self):
        content = "Дата и время операции: 20 .04.2026 23:30"

        self.assertEqual(
            parse_operation_datetime(content),
            datetime(2026, 4, 20, 23, 30),
        )

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

    def test_invalid_docx_is_skipped_instead_of_raising(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.docx"
            path.write_text("not a zip archive", encoding="utf-8")

            self.assertIsNone(read_docx_text(path))

    def test_report_classification_recognizes_short_thrombaspiration(self):
        self.assertEqual(classify_operation("ЦАГ. ТА СМА"), 2)


if __name__ == "__main__":
    unittest.main()
