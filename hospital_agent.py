import importlib.util
import sys
from pathlib import Path


def _load_hospital_agent_package() -> None:
    """Загружает пакет hospital_agent рядом с этим wrapper-файлом."""
    package_dir = Path(__file__).with_name("hospital_agent")
    spec = importlib.util.spec_from_file_location(
        "hospital_agent",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load hospital_agent package")

    module = importlib.util.module_from_spec(spec)
    sys.modules["hospital_agent"] = module
    spec.loader.exec_module(module)


def main() -> None:
    """Запускает hospital_agent с настройками из agent_config.json."""
    _load_hospital_agent_package()
    from hospital_agent.app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
