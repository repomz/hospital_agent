import argparse
from datetime import datetime

try:
    from .report_plan import (
        DEFAULT_PERIOD,
        DEFAULT_PLAN_DIR,
        DEFAULT_REPORT_DIR,
        DEFAULT_TARGET_DIR_1,
        DEFAULT_TARGET_DIR_2,
        DEFAULT_TIME,
        generate_report,
        get_plan_data,
        get_start_datetime,
        scan_and_filter_files,
    )
except ImportError:
    from report_plan import (
        DEFAULT_PERIOD,
        DEFAULT_PLAN_DIR,
        DEFAULT_REPORT_DIR,
        DEFAULT_TARGET_DIR_1,
        DEFAULT_TARGET_DIR_2,
        DEFAULT_TIME,
        generate_report,
        get_plan_data,
        get_start_datetime,
        scan_and_filter_files,
    )


def parse_arguments():
    """Разбирает параметры командной строки для генерации отчета."""
    parser = argparse.ArgumentParser(
        description="Формирование отчета по операциям за период",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python report.py                    # отчет с 08:00 ВЧЕРА
  python report.py -p 2               # отчет с 08:00 ПОЗАВЧЕРА
  python report.py -p 3 -t 14.00      # отчет с 14:00 трехдневной давности
        """,
    )
    parser.add_argument(
        "-p",
        "--period",
        type=int,
        default=DEFAULT_PERIOD,
        help="Количество дней отступа (1=вчера, 2=позавчера и т.д.)",
    )
    parser.add_argument(
        "-t",
        "--time",
        type=str,
        default=DEFAULT_TIME,
        help="Время начала отсчета в формате ЧЧ.ММ или ЧЧ:ММ",
    )
    parser.add_argument(
        "-d1",
        "--dir1",
        type=str,
        default=DEFAULT_TARGET_DIR_1,
        help="Путь к первой папке с операциями",
    )
    parser.add_argument(
        "-d2",
        "--dir2",
        type=str,
        default=DEFAULT_TARGET_DIR_2,
        help="Путь ко второй папке с операциями",
    )
    parser.add_argument(
        "-pd",
        "--plandir",
        type=str,
        default=DEFAULT_PLAN_DIR,
        help="Путь к папке с планом операций",
    )
    parser.add_argument(
        "-rd",
        "--reportdir",
        type=str,
        default=DEFAULT_REPORT_DIR,
        help="Путь к папке для сохранения отчетов",
    )
    return parser.parse_args()


def main():
    """Точка входа CLI: собирает параметры, сканирует DOCX и формирует отчет."""
    args = parse_arguments()

    print("=" * 90)
    print("ФОРМИРОВАНИЕ ОТЧЕТА ПО ОПЕРАЦИЯМ")
    print("=" * 90)

    start_period = get_start_datetime(args.period, args.time)
    end_period = datetime.now()

    print(
        f"\nПериод: с {start_period.strftime('%d.%m.%Y %H:%M')} "
        f"по {end_period.strftime('%d.%m.%Y %H:%M')}"
    )
    print("Папки с операциями:")
    print(f"  1. {args.dir1}")
    print(f"  2. {args.dir2}")

    print("\nЗагрузка плана операций...")
    planned_patients, _ = get_plan_data(args.plandir, start_period)
    if planned_patients:
        print(
            f"  Загружено плановых пациентов на "
            f"{start_period.strftime('%d.%m.%Y')}: {len(planned_patients)}"
        )

    _, planned_details_today = get_plan_data(args.plandir, end_period)
    if planned_details_today:
        print(f"  Загружен план операций на сегодня: {len(planned_details_today)} операций")

    operations = scan_and_filter_files([args.dir1, args.dir2], start_period, end_period)

    print(f"\n{'=' * 90}")
    print(f"ИТОГО ОПЕРАЦИЙ ЗА ПЕРИОД: {len(operations)}")
    print("=" * 90)

    output_file = generate_report(
        operations,
        start_period,
        end_period,
        planned_patients,
        planned_details_today,
        args.reportdir,
    )
    print(f"\nОтчет сохранен: {output_file}")
    print("\nГотово!")


if __name__ == "__main__":
    main()
