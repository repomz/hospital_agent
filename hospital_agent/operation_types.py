"""Primary procedure classification. Options (IVUS) are counted separately."""

import re

STENT = r"стентир\w*|стенирован\w*|имплант\w*\s+(?:\w+\s+){0,2}стент\w*|установ\w*\s+(?:\w+\s+){0,2}стент\w*"
BAP = r"\bбап\b|анги(?:о|ло)?пласт\w*|(?:пред|пост)дилатац\w*"
FAILURE = r"попыт\w*|безуспеш\w*|не\s+удал\w*|не\s+выполн\w*|невозмож\w*|не\s+проведен\w*"
HISTORY = r"ранее|анамнез|рестеноз|рекоменд\w*|планируется"


def performed(name: str, description: str, kind: str) -> bool:
    """Require an affirmative action, not just the presence of a device."""
    pattern = STENT if kind == "stent" else BAP
    failure_in_description = False
    for source, title in ((description, False), (name, True)):
        if title and failure_in_description:
            continue
        source = re.sub(r"\bпоп\.", "попытка", source)
        for clause in re.split(r"[;!?\n]+|(?<!\d)[.,](?!\d)", source):
            if re.search(FAILURE, clause):
                if not title and re.search(
                    r"стент" if kind == "stent" else BAP + r"|баллон", clause
                ):
                    failure_in_description = True
                continue
            if re.search(HISTORY, clause):
                continue
            if kind == "stent":
                if "ретривер" in clause:
                    continue
                evidence = re.search(pattern, clause) or re.search(
                    r"стент\w*\s+(?:\S+\s+){0,5}(?:имплантирован|установлен)", clause
                )
                if title:
                    evidence = evidence or re.search(r"\bстент\b|\bчкв\b", clause)
                    if re.search(r"из\s+стент|тромбоз\w*\s+стент", clause):
                        evidence = None
            else:
                evidence = re.search(pattern, clause)
            if evidence:
                return True
    return False


def classify(name: str, description: str = "") -> str:
    # Import locally to keep normalization reusable without an import cycle.
    from .clinical_terms import normalize_terms

    value = normalize_terms(name).lower().replace("ё", "е")
    details = normalize_terms(description).lower().replace("ё", "е")
    coronary = bool(
        re.search(r"коронар|каг|пна|пка|\bоа\b|втк|лка|медиан|диагональ|\bда\b|зна|збв", value)
    )
    cerebral = bool(
        re.search(
            r"цаг|церебр|сма|пма|зма|основн|базиляр|\b(?:ба|па|вса|оса)\b|позвоночн|\bм\s*[123](?:\s*-\s*(?:м\s*)?[123])?\b|инсульт",
            value,
        )
    )
    aspiration = bool(re.search(r"тромб(?:о)?(?:аспирац|экстракц|эктом)|\bт[аэ]\b", value))
    if aspiration and cerebral:
        return "инсульт"
    if re.search(r"\bэкс\b|кардиостим|\bвэкс\b", value):
        if re.search(r"временн|\bвэкс\b", value):
            return "ВЭКС"
        if re.search(r"ревизи|гемостаз|репозиц", value):
            return "ЭКС ревизия"
        if re.search(r"двухкамер|dr\b", value):
            return "ЭКС 2к"
        if re.search(r"однокамер|sr\b", value):
            return "ЭКС 1к"
        return "ЭКС"
    if "аневризм" in value and re.search(r"эмболизац|микроспирал|окклюзи", value):
        return "аневризма"
    # Description confirms the outcome of an intervention named in the title;
    # it must not turn diagnostic angiography into treatment of an old stent.
    intent_name = re.sub(r"из\s+стент\w*|ранее\s+установлен\w*\s+стент\w*", "", value)
    stent_intent = bool(re.search(r"стент|стенирован|\bчкв\b", intent_name))
    bap_intent = bool(re.search(BAP, value))
    stent = stent_intent and performed(value, details, "stent")
    bap = (bap_intent or stent_intent) and performed(value, details, "bap")
    coronary_target = bool(
        re.search(r"коронарн|пна|пка|\bоа\b|втк|лка|медиан|диагональ|\bда\b|зна|збв", value)
    )
    peripheral_target = bool(
        re.search(r"\b(?:вса|оса|па|опа|нпа)\b|сонн|позвоноч|подключ|бедрен|нижн|голен", value)
    )
    if coronary and (coronary_target or not peripheral_target):
        if stent:
            return "стент_кор"
        if bap:
            return "бап_кор"
        return "каг"
    carotid = bool(re.search(r"\b(?:вса|оса)\b|сонн|каротид", value))
    vertebral = bool(re.search(r"позвоночн|\bпа\b", value))
    upper = bool(re.search(r"подключ|плечев|\bверхн|\bв/?к\b", value))
    lower = bool(
        re.search(r"нижн|\bн/?к\b|\bопа\b|\bнпа\b|подвздош|бедрен|подкол|голен|берцов", value)
    )
    if stent:
        if carotid:
            return "стент_вса"
        if vertebral:
            return "стент_па"
        if "почеч" in value:
            return "стент_почки"
        if upper:
            return "стент_вк"
        if lower:
            return "стент_нк"
        return "стент_другие"
    if bap:
        if "фистул" in value:
            return "бап_фистулы"
        if re.search(r"голен|берцов|\b(?:пбба|збба|мбба)\b", value):
            return "бап_голень"
        if carotid:
            return "бап_вса"
        if lower or upper:
            return "бап_периферии"
        return "бап_другие"
    if aspiration:
        return "тромбаспирация"
    peripheral = bool(
        re.search(
            r"\b(?:вса|оса|па|опа|нпа|пбба|збба|мбба)\b|сонн|позвоноч|подключ|"
            r"бедрен|подвздош|голен|подколенн|нижн|верхн|перифер|"
            r"артери\w*\s+(?:нижн\w*\s+|верхн\w*\s+)?конечност",
            value,
        )
    )
    if re.search(r"эмболизац", value) and peripheral:
        return "эмболизация периферии"
    if re.search(r"\bэкмо\b|экстракорпорал\w*\s+мембран\w*", value):
        return "ЭКМО"
    if "эма" in value or ("эмболизац" in value and "маточ" in value):
        return "эма"
    if "эмболизац" in value:
        return "эмболизация"
    if "фистулограф" in value:
        return "фистулография"
    if re.search(r"цаг|церебраль|каротидограф", value):
        return "цаг"
    if re.search(r"\bаг\b|ангиограф", value):
        return "ангиография периферии" if peripheral else "ангиография"
    if re.search(r"вабк|контрпульс|контрапульсац", value):
        return "ВАБК"
    return "другие"
