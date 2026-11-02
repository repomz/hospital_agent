"""Anonymized regressions for the November 2026 operation form."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape
from zipfile import ZipFile

from hospital_agent.polling.protocols import parse_protocol
from hospital_agent.services.operation_reports import (
    normalize_protocol_template,
    parse_birth_date_from_content,
    parse_operation_description,
)

FORM = """Фамилия, имя, отчество (при наличии): Иванов Иван Иванович
Дата рождения: «25» ноября 1969 г.
Дата и время начала оперативного вмешательства (операции): 02.11.2026 00:05
Дата и время окончания оперативного вмешательства (операции): «2» ноября 2026 г. 0 час. 40 мин.
Продолжительность оперативного вмешательства (операции): 0 час 35 мин
Наименование оперативного вмешательства (операции): Стентирование коронарной артерии
Описание оперативного вмешательства (операции): Выполнено ВСУЗИ. Имплантирован стент в ПНА.
В ходе исследования выявлено: Остаточный стеноз 0%.
Осложнения, возникшие в ходе оперативного вмешательства (операции): нет
Вид анестезиологического пособия: прочая местная
Оперирующий врач: Идрисов Марат Загидович, врач
Ассистирующий врач:
"""


class NewProtocolTemplateTests(unittest.TestCase):
    def parse(self, content):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.docx"
            paragraphs = "".join(
                f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in content.splitlines()
            )
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    + paragraphs
                    + "</w:body></w:document>",
                )
            return parse_protocol(path, "1")

    def test_new_form_keeps_detailed_title_and_reads_fields(self):
        payload = self.parse(
            "Операция: 123 Операционная № 1. Коронарография. ВСУЗИ. Стентирование ПНА\n" + FORM
        )
        self.assertEqual(payload["study_id"], "123")
        self.assertEqual(payload["patient"], "Иванов Иван Иванович")
        self.assertEqual(payload["birth_date"], "1969-11-25")
        self.assertEqual(payload["age"], 56)
        self.assertEqual(payload["time_beginning"], "2026-11-01T17:05:00+00:00")
        self.assertEqual(payload["time_duration"], 35)
        self.assertEqual(payload["room"], 1)
        self.assertEqual(payload["surgeon"], "идрисов")
        self.assertEqual(payload["department"], "не указано")
        self.assertEqual(payload["study_type"], "стент_кор")
        self.assertEqual(payload["options"], "ivus")
        self.assertIn("ПНА", payload["name_operation"])
        self.assertEqual(payload["conclusion"], "Остаточный стеноз 0%.")
        self.assertNotIn("Осложнения", payload["description"])

    def test_missing_journal_number_is_not_fabricated(self):
        with self.assertLogs("hospital_agent", level="WARNING") as logs:
            self.assertIsNone(self.parse(FORM))
        self.assertIn("missing=study_id", " ".join(logs.output))

    def test_blank_surgeon_does_not_capture_next_label(self):
        content = FORM.replace("Идрисов Марат Загидович, врач", "")
        payload = self.parse("Операция: 123 Операционная № 1. Стентирование ПНА\n" + content)
        self.assertEqual(payload["surgeon"], "не указано")

    def test_birth_date_months_and_invalid_values(self):
        months = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
        for number, month in enumerate(months, 1):
            self.assertEqual(
                parse_birth_date_from_content(f"Дата рождения: «02» {month} 1970 г."),
                f"02.{number:02d}.1970",
            )
        self.assertFalse(parse_birth_date_from_content("Дата рождения: «31» февраля 1970 г."))

    def test_old_labels_unchanged_and_no_empty_heading_capture(self):
        old = "Ф.И.О. больного: Иванов Иван Иванович, возраст 56\nДата и время операции: 02.11.2026 00:05"
        self.assertEqual(normalize_protocol_template(old), old)
        self.assertEqual(
            parse_operation_description(
                normalize_protocol_template(
                    "Описание оперативного вмешательства (операции):\nОсложнения, возникшие в ходе оперативного вмешательства (операции): нет"
                )
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
