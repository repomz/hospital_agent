import unittest
from pathlib import Path
from unittest.mock import patch

from hospital_agent.polling.protocols import parse_protocol
from hospital_agent.services.operation_reports import (
    shorten_operation_description,
    shorten_operation_name,
    split_protocol_sections,
)


class ProtocolSectionsTests(unittest.TestCase):
    def test_payload_keeps_ivus_in_description_and_new_field_names(self):
        content = (
            "Операция: 125 Операционная №2. Коронарография.\n"
            "Дата и время операции: 22.01.2026 10:30\n"
            "Ф.И.О. больного: Иванов Иван Иванович, возраст 55\n"
            "Описание операции: Выполнено внутрисосудистое исследование. "
            "Заключение: Значимых стенозов не выявлено. Исход: удовлетворительный\n"
            "Опер.:_______Идрисов М.З."
        )
        with patch("hospital_agent.polling.protocols.read_docx_text", return_value=content):
            result = parse_protocol(Path("example.docx"), "1")
        self.assertNotIn("descr_operation", result)
        self.assertEqual(result["room"], 2)
        self.assertEqual(result["options"], "ivus")
        self.assertIn("ВСУЗИ", result["description"])
        self.assertEqual(result["conclusion"], "Значимых стенозов не выявлено.")

    def test_empty_file_warning_has_absolute_path(self):
        with patch("hospital_agent.polling.protocols.read_docx_text", return_value=""):
            with self.assertLogs("hospital_agent.protocols", level="WARNING") as logs:
                self.assertIsNone(parse_protocol(Path("empty.docx"), "1"))
        self.assertIn(str(Path("empty.docx").resolve()), logs.output[0])

    def test_radiation_dose_is_not_clinical_description(self):
        self.assertEqual(
            shorten_operation_description(
                "ЭКС подключен. Время рентгеноскопии 0 минуты, лучевая нагрузка 0 мЗв."
            ),
            "ЭКС подключен.",
        )

    def test_segment_spacing(self):
        self.assertEqual(
            shorten_operation_description("Тромбоз СМА М1 -М2 слева."), "Тромбоз СМА М1-М2 слева."
        )

    def test_plus_and_equals_are_removed_without_words(self):
        self.assertEqual(
            shorten_operation_description("БАП + ВСУЗИ. Остаточный стеноз=0%."),
            "БАП ВСУЗИ. Остаточный стеноз 0%.",
        )

    def test_conclusion_is_explicit_and_short(self):
        description, conclusion = split_protocol_sections(
            "Выполнено внутрисосудистое исследование. Заключение: Правый тип кровоснабжения. "
            "АГ признаков поражения коронарных артерий, гемодинамически значимых стенозов не выявлено."
        )
        self.assertIn("ВСУЗИ", description)
        self.assertEqual(conclusion, "Правый тип. Значимых стенозов не выявлено.")

    def test_no_invented_conclusion(self):
        description, conclusion = split_protocol_sections(
            "ЭКС подключен к электродам. Ритмовождение устойчивое."
        )
        self.assertEqual(conclusion, "")
        self.assertIn("Ритмовождение устойчивое", description)

    def test_requested_clinical_shortening(self):
        cases = {
            "Выполнена контрольная АГ остаточный стеноз 5%.": "Остаточный стеноз 5%.",
            "На контрольной АГ отмечается стаз контраста в дистальных отделах маточных артерий справа и слева.": "Стаз контраста в дистальных отделах маточных артерий справа и слева.",
            "Левый тип кровоснабжения.": "Левый тип.",
            "Тромб не визуализируется.": "Тромб не визуализируется.",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(shorten_operation_description(source), expected)

    def test_title_and_extraction(self):
        self.assertEqual(
            shorten_operation_name("Частичная ЦАГ. ТА из СМА М1-М2 сегментов слева"),
            "ЦАГ. ТА из СМА М1-М2 слева",
        )
        self.assertEqual(shorten_operation_name("ЦАГ. Тромбэкстракция из СМА"), "ЦАГ. ТЭ из СМА")

    def test_dont_drop_complication_or_embolization(self):
        for source in (
            "Катетер удален, кровотечение из места пункции.",
            "Заведен катетер в маточные артерии, выполнена эмболизация эмболами 500-710 мкм.",
        ):
            result = shorten_operation_description(source)
            self.assertTrue(result)
            self.assertIn("кровотечение" if "кровотечение" in source else "500-710", result)
