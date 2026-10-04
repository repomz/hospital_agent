import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "upload_historical_statistics.py"
SPEC = importlib.util.spec_from_file_location("upload_historical_statistics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class HistoricalStatisticsTest(unittest.TestCase):
    def test_historical_operation_classification(self):
        cases = {
            "КАГ": "КАГ",
            "КАГ, стент ПНА": "СТЕНТ КОР",
            "КАГ, БАП ПКА": "БАП КОР",
            "ЦАГ. ТА СМА": "ИНСУЛЬТ",
            "Стентирование ВСА": "СТЕНТ ВСА",
            "Стентирование ОПА справа нижней конечности": "СТЕНТ Н/К",
            "Стентирование подключичной артерии": "СТЕНТ В/К",
            "БАП артерий голени": "Голень",
            "ВСУЗИ коронарной артерии": "КАГ",
            "Неизвестная операция": "ДРУГИЕ",
            "Временный ЭКС": "ВЭКС",
            "ЭМА": "ЭМА",
            "КАГ. ТА ПКА. Стентирование ДА": "СТЕНТ КОР",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(MODULE.classify_historical_operation(source), expected)

    def test_ivus_is_an_overlay_not_a_second_operation(self):
        operation = {
            "datetime": datetime(2025, 1, 1),
            "patient": "Тест Тест Тест",
            "operation": "КАГ. Стентирование ПНА с ВСУЗИ",
            "description": "Выполнено ВСУЗИ ПНА.",
        }
        with (
            patch.object(
                MODULE, "iter_operation_files", return_value=[Path("a.docx"), Path("b.docx")]
            ),
            patch.object(MODULE, "analyze_operation_file", return_value=operation),
        ):
            payload, parsed, skipped = MODULE.build_statistics(Path("."), 2025)
        self.assertEqual((parsed, skipped), (1, 0))
        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["years"][0]["total"], 1)
        self.assertEqual(payload["years"][0]["counts"]["СТЕНТ КОР"], 1)
        self.assertEqual(payload["years"][0]["counts"]["ВСУЗИ"], 1)
