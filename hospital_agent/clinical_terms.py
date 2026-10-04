"""Deterministic clinical spelling/option rules. Never rewrite numbers or names."""

import re


def normalize_terms(text: str) -> str:
    corrections = {
        "балонная": "баллонная",
        "балонной": "баллонной",
        "стентирвоание": "стентирование",
        "тентирование": "стентирование",
        "аретрии": "артерии",
        "анртерии": "артерии",
        "остсточный": "остаточный",
        "осттаточный": "остаточный",
        "предней": "передней",
        "визуализироуются": "визуализируются",
        "балонным": "баллонным",
        "выполненна": "выполнена",
        "удовлтворительные": "удовлетворительные",
        "ренгеноскопии": "рентгеноскопии",
        "артеий": "артерий",
        "диаггостический": "диагностический",
        "ссправа": "справа",
        "осле": "после",
        "сегемнта": "сегмента",
        "диагоноальной": "диагональной",
    }
    text = re.sub(r"[А-Яа-яЁё]+", lambda m: corrections.get(m[0].lower(), m[0]), text)
    text = re.sub(r"\b(?:стентирвоан|стентирвован|стенирован)", "стентирован", text, flags=re.I)
    # Controlled variants only: the distinctive root plus a research method.
    text = re.sub(
        r"\bвнутри[\s-]*сосу[дт]ист\w*\s+(?:(?:ультра[зс]вук\w*|ультро[зс]вук\w*)\s*)?(?:ис[с]?ледова\w*|визуа[л]?иза\w*|ультра[зс]вук\w*)",
        "ВСУЗИ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b(?:в\s*с\s*у\s*з\s*и|ivus)\b", "ВСУЗИ", text, flags=re.I)
    return text


def performed_ivus(name: str, description: str) -> bool:
    """Title or current procedure evidence; exclude recommendations/history/negation."""
    for text in (name, description):
        normalized = normalize_terms(text)
        for sentence in re.split(r"[.!?;\n]+", normalized):
            if not re.search(r"\bВСУЗИ\b", sentence, re.I):
                continue
            if re.search(
                r"\b(?:рекоменд\w*|планируется|планируем\w*|ранее|анамнез\w*)\b|не\s+(?:был\w*\s+)?(?:выполн\w*|провод\w*)|\bбез\s+ВСУЗИ\b|ВСУЗИ\s+не\b",
                sentence,
                re.I,
            ):
                continue
            return True
    return False


def performed_assist_option(name: str, description: str, option: str) -> bool:
    """Detect an actually used circulatory-support method as an option tag."""
    patterns = {
        "vabk": r"\bвабк\b|внутриаортальн\w*\s+баллон\w*|контрпульсац",
        "ekmo": r"\bэкмо\b|экстракорпорал\w*\s+мембран\w*\s+оксигенац\w*",
    }
    pattern = patterns.get(option)
    if not pattern:
        return False
    for text in (name, description):
        normalized = normalize_terms(text)
        for sentence in re.split(r"[.!?;\n]+", normalized):
            if not re.search(pattern, sentence, re.I):
                continue
            if re.search(
                r"\b(?:рекоменд\w*|планируется|планируем\w*|возможн\w*|ранее|анамнез\w*)\b|"
                r"не\s+(?:был\w*\s+)?(?:выполн\w*|провод\w*|использова\w*)|"
                r"\bбез\s+(?:применения\s+)?(?:вабк|экмо)\b",
                sentence,
                re.I,
            ):
                continue
            return True
    return False


def operation_type(name: str, description: str = "") -> str:
    from .operation_types import classify

    return classify(name, description)
