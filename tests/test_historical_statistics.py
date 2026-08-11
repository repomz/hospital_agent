import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "upload_historical_statistics.py"
SPEC = importlib.util.spec_from_file_location("upload_historical_statistics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class HistoricalStatisticsTest(unittest.TestCase):
    def test_historical_operation_classification(self):
        cases = {
            "КАГ": "КАГ",
            "КАГ, стент ПНА": "Стент кор",
            "КАГ, БАП ПКА": "БАП кор",
            "ЦАГ. ТА СМА": "Инсульт",
            "Стентирование ВСА": "Стент ВСА",
            "Стентирование ОПА справа нижней конечности": "Стент н/к",
            "Стентирование подключичной артерии": "Стент в/к",
            "БАП артерий голени": "Голень",
            "ВСУЗИ коронарной артерии": "ВСУЗИ",
            "Неизвестная операция": "",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(MODULE.classify_historical_operation(source), expected)
