"""Deterministic, text-only role-fit rules for Job Intel."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Iterable, Literal


Verdict = Literal["accept", "reject", "blocked"]

DEFAULT_OWNER_LANGUAGES = ("en", "ru")
DEFAULT_SHORT_CONTRACT_MONTHS = 18

_LANGUAGES = {
    "arabic": "ar",
    "chinese": "zh",
    "dutch": "nl",
    "nederlands": "nl",
    "english": "en",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
    "german": "de",
    "deutsch": "de",
    "italian": "it",
    "japanese": "ja",
    "kazakh": "kk",
    "korean": "ko",
    # Job texts name the spoken variety, not the family: run 511 issued okx
    # roles reading "Fluent in English and Mandarin" because only "chinese"
    # was known.
    "mandarin": "zh",
    "cantonese": "zh",
    "putonghua": "zh",
    "polish": "pl",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "español": "es",
    "espanol": "es",
    "turkish": "tr",
    "ukrainian": "uk",
}

_LANGUAGE_CODES = {code: code for code in _LANGUAGES.values()}
KNOWN_LANGUAGE_CODES = tuple(sorted(set(_LANGUAGE_CODES.values())))
_LANGUAGE_NAMES = tuple(sorted(_LANGUAGES, key=len, reverse=True))
_LANGUAGE_SERVICE_WORDS = {
    "en": frozenset(
        "a about above after again against an and another any are around as at back be been before being below between both but by can came come company could description did does down during each even every experience few first for from further get give good had has have he her here himself his how however if in into is it its itself join just keep kind know language last later lead leadership less like looking made make manage many may me might more most much must my need never new next no nor not nothing now of off often once only on or our other over own part people perhaps product put really required role right same say see several she should since software so some team than that the their them then these they think this those though through to too under until up use used using very want was way we were what when where which while who will with without work working would year years you your".split()
    ),
    "ru": frozenset(
        "а быть в для и из ищем компания на о по продукт программная роль с команда это мы".split()
    ),
    "fr": frozenset(
        "à avec au dans de des description directeur disponible du en et expérience française français francais french la le les non nous offre poste pour produit rechercher recherchons seulement une un vous".split()
    ),
    "nl": frozenset(
        "aan als bedrijf de dutch een ervaring het in klanten met naar nederland nederlands om ons product vacature van voor wij werken".split()
    ),
    "de": frozenset(
        "als an auf aus das der die ein eine für im in mit produkt rolle und von wir zu".split()
    ),
    "es": frozenset(
        "a al con de del el en empresa equipo experiencia la los para por producto que se un una y".split()
    ),
    "it": frozenset(
        "a al con da del di è e il in la le per prodotto ruolo una un".split()
    ),
    "pt": frozenset(
        "a ao com da de do e em empresa experiência para por produto que uma um".split()
    ),
    "pl": frozenset(
        "a do dla i jest na nie o oraz produkt rola się teamu w z za".split()
    ),
    "uk": frozenset(
        "а в для і з компанія ми на не продукт роль та це".split()
    ),
    "kk": frozenset(
        "біз және үшін компания өнім рөлін бұл мен".split()
    ),
    "tr": frozenset(
        "bir bu için ile şirket deneyim ve ürün rol".split()
    ),
    "ar": frozenset(),
    "zh": frozenset(),
    "ja": frozenset(),
    "ko": frozenset(),
}
_LANGUAGE_WORD_COUNTS: dict[str, int] = {}
for _service_words in _LANGUAGE_SERVICE_WORDS.values():
    for _word in _service_words:
        _LANGUAGE_WORD_COUNTS[_word] = _LANGUAGE_WORD_COUNTS.get(_word, 0) + 1
_AMBIGUOUS_LANGUAGE_SERVICE_WORDS = frozenset(
    word for word, count in _LANGUAGE_WORD_COUNTS.items() if count > 1
)
_LANGUAGE_DISTINCTIVE_LETTERS = {
    "fr": frozenset("àâçéèêëîïôûùüÿœ"),
    "de": frozenset("äöüß"),
    "es": frozenset("áéíóúñü"),
    "pt": frozenset("ãõáâçéêíóôú"),
    "pl": frozenset("ąćęłńóśźż"),
    "ru": frozenset("ёыэъ"),
    "uk": frozenset("іїєґ"),
    "kk": frozenset("әғқңөұүһі"),
}
_LANGUAGE_TITLE_MARKERS = {
    "fr": frozenset("directeur directrice français francais".split()),
    "nl": frozenset("dutch nederlands".split()),
    "de": frozenset("deutsch german".split()),
    "es": frozenset("español espanol spanish".split()),
}
_LANGUAGE_DISTINCTIVE_SERVICE_WORDS = {
    "fr": frozenset("directeur directrice français francais french française".split()),
    "nl": frozenset("dutch nederlands nederland werken wij klanten vacature".split()),
    "de": frozenset("deutsch german deutschkenntnisse".split()),
    "es": frozenset("español espanol spanish dominio".split()),
    "it": frozenset("italiano italian".split()),
    "pt": frozenset("português portugues portuguese".split()),
    "pl": frozenset("polski polish".split()),
    "uk": frozenset("український українець".split()),
    "kk": frozenset("қазақ қазақша".split()),
}
_ENGLISH_LOW_SIGNAL_WORDS = frozenset(
    "a an and are as at be been by but for from had has have he her in is it its me of on or that the their them these they this those to was we were what when which who will with you".split()
)
_LANGUAGE_SCORE_WORDS = {
    code: {
        "ambiguous": tuple(
            word
            for word in service_words
            if word in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
        ),
        "exclusive": tuple(
            word
            for word in service_words
            if word not in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
        ),
    }
    for code, service_words in _LANGUAGE_SERVICE_WORDS.items()
}
_LANGUAGE_SCORE_WORDS["en"]["high_signal"] = tuple(
    word
    for word in _LANGUAGE_SERVICE_WORDS["en"]
    if word not in _ENGLISH_LOW_SIGNAL_WORDS
    and word not in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
)
_LANGUAGE_SCORE_WORDS["en"]["low_signal"] = tuple(_ENGLISH_LOW_SIGNAL_WORDS)
_LANGUAGE_SCORE_KIND_INDEX = {"exclusive": 0, "high_signal": 0, "ambiguous": 1, "low_signal": 2}
_LANGUAGE_TOKEN_SCORE_KINDS: dict[str, tuple[tuple[str, tuple[int, ...]], ...]] = {}
_token_score_kinds: dict[str, dict[str, list[int]]] = {}
for _code, _score_words in _LANGUAGE_SCORE_WORDS.items():
    for _kind, _words in _score_words.items():
        if _code == "en" and _kind == "exclusive":
            continue
        for _word in _words:
            _token_score_kinds.setdefault(_word, {}).setdefault(_code, []).append(
                _LANGUAGE_SCORE_KIND_INDEX[_kind]
            )
for _word, _code_kinds in _token_score_kinds.items():
    _LANGUAGE_TOKEN_SCORE_KINDS[_word] = tuple(
        (_code, tuple(kinds)) for _code, kinds in _code_kinds.items()
    )
_LANGUAGE_LETTER_RANGES = {
    "cyrillic": ((0x0400, 0x04FF),),
    "latin": ((0x0041, 0x005A), (0x0061, 0x007A)),
    "arabic": ((0x0600, 0x06FF),),
    "cjk": ((0x3400, 0x9FFF),),
    "hangul": ((0xAC00, 0xD7AF),),
}
_LANGUAGE_SCRIPT_BY_CODE = {
    "ru": "cyrillic",
    "uk": "cyrillic",
    "kk": "cyrillic",
    "ar": "arabic",
    "zh": "cjk",
    "ja": "cjk",
    "ko": "hangul",
}
_MIN_LANGUAGE_TEXT_TOKENS = 2
_MIN_DESCRIPTION_CHARS_FOR_LANGUAGE_DETECTION = 64
_MIN_LANGUAGE_SERVICE_RATIO_PERCENT = 15
_MIN_SUPPORTED_LANGUAGE_FALLBACK_SCORE = 8
_LANGUAGE_DOMINANCE_MARGIN = 0
_MIN_SUPPORTED_LANGUAGE_DENSITY_PERCENT = 18
_MIN_DENSITY_TEXT_TOKENS = 20
_MIN_LANGUAGE_SERVICE_WORDS = 2
_MIN_SUPPORTED_SENTENCE_COVERAGE_PERCENT = 35
_MIN_SUPPORTED_SENTENCE_TOKENS = 8

# "AVP/VP, Product Owner" is a corporate grade in front of a backlog role, not
# "VP Product": the seniority patterns stop where "product" runs on into "owner".
_PRODUCT_LEADERSHIP = (
    r"\b(?:chief|head|director|vp|vice president|group)\s+(?:of\s+)?product\b(?!\s+owner\b)",
    r"\b(?:chief|head|director|vp|vice president|group)\s+(?:of\s+)?product\s+(?:management|function|area)\b",
    r"\b(?:chief|head|director|vp|vice president|group)\s*[, :/\-&—–]+\s*(?:of\s+)?product(?!\s+owner\b)(?:\s+(?:management|function|area))?\b",
    r"\bproduct(?:\s+[a-z&/-]+){0,3}\s+(?:chief|head|director|vp|vice president|lead)\b",
    r"\bproduct\s+general\s+manager\b",
    r"\bproduct\s+lead\b",
    r"\bchief\s+product\s+officer\b",
    r"\bcpo\b",
)
_SOFTWARE_SIGNALS = (
    r"\bsaas\b",
    r"\bsoftware\b",
    r"\bsoftware[- ]as[- ]a[- ]service\b",
    r"\b(?:b2b|enterprise|cloud|developer)\s+(?:software|platform)\b",
    r"\btechnology\s+platform\b",
    r"\bplatform\b",
    r"\btechnology\s+company\b",
    r"\b(?:digital|technology|tech)\s+(?:product|business|company|platform)\b",
    r"\bproduct\s+company\b",
    r"\b(?:online|web|mobile)\s+(?:platform|product|application)\b",
    r"\b(?:ai|artificial intelligence|machine learning|ml)\b",
)
_SCOPE_SIGNALS = (
    r"\bco[- ]founder\b",
    r"\bone\s+product\b",
    r"\bsingle\s+(?:product|vertical)\b",
    r"\b(?:product|business)\s+vertical\b",
    r"\bproduct\s+portfolio\b",
)
_ROLE_SIGNAL_TERMS = (
    "chief",
    "head",
    "director",
    "vp",
    "vice president",
    "group",
    "product",
    "cpo",
    "saas",
    "software",
    "b2b",
    "enterprise",
    "cloud",
    "developer",
    "platform",
    "technology",
    "digital",
    "tech",
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "online",
    "web",
    "mobile",
    "co-founder",
    "vertical",
    "portfolio",
    "single",
)
_ADJACENT_PRODUCT_FUNCTIONS = (
    "design",
    "marketing",
    "localisation",
    "localization",
    "partnerships",
    "communications",
    "research",
)
# "Product Marketing Lead" is a marketing mandate that happens to name product;
# "Product Director, Localisation" is a product mandate that happens to name a
# domain. The difference is word order, so the pattern anchors on the function
# sitting immediately after "product" and before the seniority word.
_ADJACENT_FUNCTION_PATTERNS = tuple(
    rf"\bproduct\s+{function}\b" for function in _ADJACENT_PRODUCT_FUNCTIONS
) + tuple(
    rf"\b{function}\s+(?:lead|director|head)\b" for function in _ADJACENT_PRODUCT_FUNCTIONS
)
_US_LOCATION_PATTERNS = (
    r"\bunited\s+states\b",
    r"\busa\b",
    r"\bu\.s\.a?\b",
    # Safe as a whole word here because only the location field is matched;
    # in a description "us" appears in every "join us".
    r"\bus\b",
    r"\bsan\s+francisco\b",
    r"\bnew\s+york\b",
    r"\bseattle\b",
    r"\bchicago\b",
    r"\bboston\b",
    r"\baustin\b",
    r"\bdenver\b",
    r"\blos\s+angeles\b",
    r"\bcalifornia\b",
    r"\bwashington\b",
    r"\bnew\s+jersey\b",
    r",\s*(?:ca|ny|wa|tx|ma|il|co|nj)\b",
)
_REMOTE_LOCATION_PATTERN = r"\bremote\b"
# The owner does not take roles located in Russia (ruling on release
# shortlist-20260922T084423Z). Only the location field is matched: a
# description listing "РФ, СНГ, GCC" as markets says nothing about where the
# role sits.
_RUSSIA_LOCATION_PATTERNS = (
    r"\brussia\b",
    r"\brussian\s+federation\b",
    r"\bmoscow\b",
    r"\b(?:saint|st\.?)\s*petersburg\b",
    r"росси",
    r"\bрф\b",
    r"москв",
    r"санкт-петербург",
    r"новосибирск",
    r"екатеринбург",
    r"\bказань\b",
    r"нижний\s+новгород",
)
# A required master's or doctorate is a hard gate for the owner. Anything that
# admits an alternative (a bachelor's, "or equivalent") or softens it
# ("preferred", "a plus") keeps the role open.
_ADVANCED_DEGREE_PATTERN = (
    r"\bmaster'?s?\s+(?:degree|of)\b|\bmsc\b|\bm\.sc\b|\bmba\b|\bph\.?\s?d\b|\bdoctorate\b|магистр"
)
_DEGREE_REQUIRED_CUE = (
    r"\b(?:required|requires?|mandatory|must|essential|obligatory|minimum)\b|обязательн"
)
_DEGREE_ALTERNATIVE_CUE = (
    r"\b(?:bachelor'?s?|bsc|b\.sc|undergraduate|equivalent|preferred|preferably|plus|advantage|"
    r"desirable|ideally|nice\s+to\s+have|bonus|beneficial|or\s+similar)\b|бакалавр|желательн|плюсом"
)
# A sentence that denies sponsorship contains the word "sponsorship", so the
# cue alone opens the very gate it should keep shut. Run 510 accepted two US
# roles whose text read "visa sponsorship is not available" and "without the
# need for new visa sponsorship".
_SPONSORSHIP_DENIAL_PATTERN = (
    r"\b(?:not|no|never|without|cannot|can\s*not|unable|ineligible|not\s+eligible)\b"
)
_SPONSORSHIP_CUES = (
    r"\bsponsor(?:s|ship|ing)?\b",
    r"\bh-?1b\b",
    r"\bvisa\s+(?:support|sponsorship)\b",
    r"\brelocation\s+(?:support|package|assistance)\b",
    r"\bwork\s+authorisation\s+support\b",
    r"\bwork\s+authorization\s+support\b",
)


_LOCALIZED_LANGUAGE_REQUIREMENTS = {
    "fr": (
        r"\b(?:excellente|bonne)\s+communication\s+en\s+fran(?:ç|c)ais\b",
        r"\bfran(?:ç|c)ais\b[^.!?\n]{0,80}\b(?:oral|écrit|ecrit)\b",
    ),
    "nl": (
        r"\b(?:goede|vloeiende?)\s+beheersing\s+van\s+(?:het\s+)?nederlands\b",
        r"\bvloeiend\s+nederlands\b",
        r"\bnederlands\b[^.!?\n]{0,80}\b(?:mondeling|schriftelijk)\b",
    ),
    "de": (
        r"\b(?:sehr\s+gute|gute)\s+deutschkenntnisse\b",
        r"\bdeutschkenntnisse\b[^.!?\n]{0,80}\b(?:wort|schrift)\b",
        r"\bdeutsch\b[^.!?\n]{0,80}\b(?:wort\s+und\s+schrift|schriftlich)\b",
    ),
    "es": (
        r"\b(?:dominio|fluidez)\s+(?:del\s+)?espa(?:ñ|n)ol\b",
        r"\bespa(?:ñ|n)ol\b[^.!?\n]{0,80}\b(?:oral|escrito)\b",
        r"\bse\s+requiere\b[^.!?\n]{0,80}\bespa(?:ñ|n)ol\b",
    ),
}
_LANGUAGE_CODE_REQUIREMENTS = {
    "fr": (
        r"\b(?:bilingual|bilingue|communication|language)\b[^.!?\n]{0,40}\bfr(?:[-_/]fr)?\b",
        r"\bfr(?:[-_/]fr)?\s*[/&+,]\s*(?:en|english)\b|\b(?:en|english)\s*[/&+,]\s*fr(?:[-_/]fr)?\b",
    ),
    "nl": (
        r"\b(?:bilingual|bilingue|communication|language)\b[^.!?\n]{0,40}\bnl(?:[-_/]nl)?\b",
        r"\bnl(?:[-_/]nl)?\s*[/&+,]\s*(?:en|english)\b|\b(?:en|english)\s*[/&+,]\s*nl(?:[-_/]nl)?\b",
    ),
    "de": (
        r"\b(?:bilingual|bilingue|communication|language)\b[^.!?\n]{0,40}\bde(?:[-_/]de)?\b",
        r"\bde(?:[-_/]de)?\s*[/&+,]\s*(?:en|english)\b|\b(?:en|english)\s*[/&+,]\s*de(?:[-_/]de)?\b",
    ),
    "es": (
        r"\b(?:bilingual|bilingue|communication|language)\b[^.!?\n]{0,40}\bes(?:[-_/]es)?\b",
        r"\bes(?:[-_/]es)?\s*[/&+,]\s*(?:en|english)\b|\b(?:en|english)\s*[/&+,]\s*es(?:[-_/]es)?\b",
    ),
}


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    fragments: tuple[str, ...]
    explanation: str
    details: dict[str, Any]


@dataclass(frozen=True)
class RoleFitDecision:
    verdict: Verdict
    matches: tuple[RuleMatch, ...]
    normalized_text: str
    explanation: str

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(match.rule_id for match in self.matches)

    def match_for(self, rule_id: str) -> RuleMatch:
        for match in self.matches:
            if match.rule_id == rule_id:
                return match
        raise KeyError(rule_id)


def title_product_leadership_fragments(title: str) -> tuple[str, ...]:
    """Return the product-leadership evidence ``evaluate_role_fit`` reads from a title.

    Sourcing uses the same predicate to decide which titles are worth fetching
    full text for, so the two cannot disagree about what counts as leadership.
    """
    if not any(term in title.lower() for term in _ROLE_SIGNAL_TERMS):
        return ()
    return _find_patterns(title, _PRODUCT_LEADERSHIP)


def evaluate_role_fit(
    title: str,
    company: str,
    location: str,
    description: str,
    *,
    owner_languages: Iterable[str] = DEFAULT_OWNER_LANGUAGES,
    supported_languages: Iterable[str] = DEFAULT_OWNER_LANGUAGES,
    short_contract_months: int = DEFAULT_SHORT_CONTRACT_MONTHS,
) -> RoleFitDecision:
    """Evaluate one role without network, database, clock, or model calls.

    Text fragments in every returned rule match are copied from the supplied
    fields, so callers can show the evidence that activated a rule.
    """
    if short_contract_months <= 0:
        raise ValueError("short_contract_months must be positive")

    text = " ".join(part.strip() for part in (title, company, location, description) if part).strip()
    language_text = description.strip()
    supported_language_values = tuple(supported_languages)
    supported_codes = {_normalise_language(language) for language in supported_language_values}
    if (
        len(language_text) < _MIN_DESCRIPTION_CHARS_FOR_LANGUAGE_DETECTION
        and _has_unsupported_language_title_marker(title, supported_codes)
    ):
        language_text = " ".join(part.strip() for part in (title, description) if part).strip()
    language_match = _unsupported_description_language_match(
        language_text,
        supported_language_values,
        dominance_margin=0 if len(description.strip()) < _MIN_DESCRIPTION_CHARS_FOR_LANGUAGE_DETECTION else _LANGUAGE_DOMINANCE_MARGIN,
    )
    if language_match is not None:
        return RoleFitDecision("reject", (language_match,), text, language_match.explanation)

    sentences = _sentences(text)
    matches: list[RuleMatch] = []

    lower_text = text.lower()
    has_role_signal = any(term in lower_text for term in _ROLE_SIGNAL_TERMS)
    software_fragments = (
        _find_patterns(text, (*_PRODUCT_LEADERSHIP, *_SOFTWARE_SIGNALS, *_SCOPE_SIGNALS))
        if has_role_signal
        else ()
    )
    leadership_fragments = title_product_leadership_fragments(title)
    software_signal_fragments = (
        _find_patterns(text, _SOFTWARE_SIGNALS) if has_role_signal else ()
    )
    if leadership_fragments and software_signal_fragments:
        matches.append(
            RuleMatch(
                "software_product_leadership",
                software_fragments,
                "Product leadership is evidenced in a software/SaaS organization; mandate breadth does not gate acceptance.",
                {"leadership_fragments": leadership_fragments, "software_fragments": software_signal_fragments},
            )
        )

    matches.extend(_adjacent_function_matches(title))
    matches.extend(_work_authorisation_matches(location, text))
    matches.extend(_industry_matches(sentences, title))
    matches.extend(_staffing_agency_matches(company, text))
    matches.extend(_russia_location_matches(location))
    matches.extend(_content_platform_matches(title, text))
    matches.extend(_advanced_degree_matches(sentences))

    required_language_match = _language_match(sentences, owner_languages)
    if required_language_match is not None:
        matches.append(required_language_match)

    matches.extend(_urgency_matches(sentences, short_contract_months))

    hard_reject = any(match.rule_id in _HARD_REJECT_RULES for match in matches)
    if hard_reject:
        verdict: Verdict = "reject"
        explanation = "A hard exclusion rule was triggered."
    elif required_language_match is not None:
        verdict = "blocked"
        explanation = "The role otherwise remains eligible, but a required working language is unavailable."
    elif any(match.rule_id in _BLOCKING_RULES for match in matches):
        verdict = "blocked"
        explanation = "The role otherwise remains eligible, but an eligibility question is unresolved."
    elif any(match.rule_id == "software_product_leadership" for match in matches):
        verdict = "accept"
        explanation = "The role matches the software/SaaS product-leadership rule."
    else:
        verdict = "reject"
        explanation = "No qualifying software/SaaS product-leadership evidence was found."

    return RoleFitDecision(verdict, tuple(matches), text, explanation)


_BLOCKING_RULES = frozenset({"us_remote_eligibility_unknown"})

_HARD_REJECT_RULES = frozenset(
    {
        "adjacent_product_function",
        "us_onsite_without_sponsorship",
        "domain_expertise_required",
        "ecommerce_commercial_leadership",
        "interim_or_cover",
        "russia_location",
        "advanced_degree_required",
        "short_contract",
        "staffing_agency_or_aggregator",
    }
)


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(
        fragment.strip()
        for fragment in re.split(r"(?<=[.!?])\s+|\n+", text)
        if fragment.strip()
    )


@lru_cache(maxsize=512)
def _compiled_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_LANGUAGE_REQUIRED_CUES = _compiled_pattern(
    r"\b(?:required|mandatory|must\s+(?:speak|be)|fluent|native|professional(?:ly)?\s+proficien(?:cy|t)|proficiency|business[- ]level|working\s+language|language\s+of\s+work|language\s+skills|excellent\s+command|excellent\s+communication|communication|oral|written|spoken|written\s+and\s+spoken|spoken\s+and\s+written|speaker|speaking)\b"
)
_OPTIONAL_LANGUAGE_CUES = _compiled_pattern(
    r"\b(?:preferred|nice\s+to\s+have|bonus|plus|advantage|desirable|optional)\b|\bnot\s+required\b"
)
_LOCALIZED_LANGUAGE_REQUIREMENT_PATTERNS = {
    code: tuple(_compiled_pattern(pattern) for pattern in patterns)
    for code, patterns in _LOCALIZED_LANGUAGE_REQUIREMENTS.items()
}
_LANGUAGE_CODE_REQUIREMENT_PATTERNS = {
    code: tuple(_compiled_pattern(pattern) for pattern in patterns)
    for code, patterns in _LANGUAGE_CODE_REQUIREMENTS.items()
}
_AD_NEGATION_PATTERN = _compiled_pattern(
    r"\b(?:not|no)\s+(?:an?\s+)?(?:advertising|adtech|ad\s*tech)\s+(?:platform|company|business)\b"
)
_AD_PLATFORM_PATTERN = _compiled_pattern(
    r"\b(?:adtech|ad\s*tech|advertising\s+technology\s+(?:platform|company|business)|programmatic\s+advertising\s+(?:platform|business)|advertising\s+platform|demand[- ]side\s+platform)\b"
)
_AD_SUBJECT_PATTERN = _compiled_pattern(
    r"\b(?:ad[- ]?serv(?:ing|er)|dsp|ssp|ad\s+ops?|ad\s+operations|ad\s+exchange|real[- ]time\s+bidding|rtb|ad(?:vertising)?\s+auction|demand[- ]side\s+platform|advertising\s+inventory|campaign\s+delivery|ecpm|monetization)\b"
)
_NEGATED_SUBJECT_PATTERN = _compiled_pattern(r"\b(?:not|without|never|no)\s*$")
_EXPERIENCE_PREFIX_PATTERN = _compiled_pattern(
    r"\b(?:experience|background|familiarity)\s+with\s+(?:an?\s+)?$"
)
_ECOMMERCE_DUTY_PATTERN = _compiled_pattern(
    r"\b(?:assortment|procurement|purchasing|sourcing|margin|merchandising|category\s+management|range\s+planning|supplier\s+(?:management|relationships?))\b"
)


@lru_cache(maxsize=128)
def _combined_pattern(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.IGNORECASE)


_LANGUAGE_NAME_COMBINED = _combined_pattern(tuple(rf"\b{re.escape(name)}\b" for name in _LANGUAGE_NAMES))
_LANGUAGE_ANY_REQUIREMENT_PATTERN = _combined_pattern(
    tuple(
        pattern
        for patterns in (*_LOCALIZED_LANGUAGE_REQUIREMENTS.values(), *_LANGUAGE_CODE_REQUIREMENTS.values())
        for pattern in patterns
    )
    + tuple(rf"\b{re.escape(name)}\b" for name in _LANGUAGE_NAMES)
)


def _find_patterns(text: str, patterns: Iterable[str]) -> tuple[str, ...]:
    pattern_tuple = tuple(patterns)
    if not pattern_tuple:
        return ()
    found = [
        (match.start(), text[match.start() : match.end()].strip())
        for match in _combined_pattern(pattern_tuple).finditer(text)
        if match.group()
    ]
    unique: list[str] = []
    for _, fragment in sorted(found):
        if fragment not in unique:
            unique.append(fragment)
    return tuple(unique)


def _unsupported_description_language_match(
    description: str,
    supported_languages: Iterable[str],
    *,
    dominance_margin: int = _LANGUAGE_DOMINANCE_MARGIN,
) -> RuleMatch | None:
    description = description.strip()
    lower_description = description.lower()
    tokens = tuple(re.findall(r"[^\W\d_]+", lower_description, flags=re.UNICODE))
    if len(tokens) < _MIN_LANGUAGE_TEXT_TOKENS:
        return None
    sentences = _sentences(description)

    allowed = {_normalise_language(language) for language in supported_languages}
    if set(KNOWN_LANGUAGE_CODES).issubset(allowed):
        return None
    cyrillic_letters = _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES["cyrillic"])
    latin_letters = _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES["latin"])
    script_letter_counts = {
        "cyrillic": cyrillic_letters,
        "latin": latin_letters,
        **{
            script: _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES[script])
            for script in {"arabic", "cjk", "hangul"}
        },
    }
    token_counts = Counter(tokens)
    language_marker_counts = Counter(
        _LANGUAGES[token] for token in tokens if token in _LANGUAGES
    )
    description_letter_counts = Counter(lower_description)
    distinctive_letter_counts = {
        code: sum(description_letter_counts.get(letter, 0) for letter in letters)
        for code, letters in _LANGUAGE_DISTINCTIVE_LETTERS.items()
    }
    language_service_scores = _language_service_scores(tokens)
    scores: dict[str, int] = {}
    for code, service_words in _LANGUAGE_SERVICE_WORDS.items():
        service_score = language_service_scores[code]
        marker_score = language_marker_counts.get(code, 0)
        distinctive_score = distinctive_letter_counts.get(code, 0)
        script_name = _LANGUAGE_SCRIPT_BY_CODE.get(code)
        script_letters = script_letter_counts.get(script_name, 0)
        script_score = min(script_letters // 8, 3)
        if code == "en" and latin_letters >= 8:
            script_score += 1
        scores[code] = service_score + min(marker_score, 2) + min(distinctive_score, 2) + script_score

    best_unsupported = max(
        (code for code in scores if code not in allowed),
        key=lambda code: (scores[code], code),
        default=None,
    )
    if best_unsupported is None:
        return None
    unsupported_service_score = language_service_scores[best_unsupported]
    unsupported_service_score += min(
        sum(
            token in _LANGUAGES and _LANGUAGES[token] == best_unsupported
            for token in tokens
        ),
        2,
    )
    unsupported_service_score += min(
        distinctive_letter_counts.get(best_unsupported, 0),
        2,
    )
    unsupported_script = _LANGUAGE_SCRIPT_BY_CODE.get(best_unsupported)
    unsupported_script_letters = script_letter_counts.get(unsupported_script, 0)
    unsupported_service_score += min(unsupported_script_letters // 8, 3)
    distinctive_word_count = sum(
        token in _LANGUAGE_DISTINCTIVE_SERVICE_WORDS.get(best_unsupported, frozenset())
        for token in tokens
    )
    distinctive_letter_count = distinctive_letter_counts.get(best_unsupported, 0)
    distinctive_word_evidence = distinctive_word_count > 0
    distinctive_letter_evidence = distinctive_letter_count > 0
    script_evidence = unsupported_script_letters >= 8
    best_supported_score = max(
        (scores.get(code, 0) for code in allowed),
        default=0,
    )
    if len(tokens) >= _MIN_DENSITY_TEXT_TOKENS:
        supported_sentence_coverage, substantive_sentence_count = _supported_sentence_coverage(
            description,
            allowed,
        )
        if (
            substantive_sentence_count >= 3
            and supported_sentence_coverage < _MIN_SUPPORTED_SENTENCE_COVERAGE_PERCENT
        ):
            return _unknown_language_match(
                description,
                sentences,
                tuple(sorted(allowed)),
                scores,
                len(tokens),
            )
    if not (distinctive_word_evidence or distinctive_letter_evidence or script_evidence):
        if (
            len(tokens) >= _MIN_DENSITY_TEXT_TOKENS
            and best_supported_score < _MIN_SUPPORTED_LANGUAGE_FALLBACK_SCORE
            and unsupported_service_score >= _MIN_LANGUAGE_SERVICE_WORDS
        ):
            return _unknown_language_match(
                description,
                sentences,
                tuple(sorted(allowed)),
                scores,
                len(tokens),
            )
        return None
    if (
        len(tokens) >= _MIN_DENSITY_TEXT_TOKENS
        and distinctive_word_count < 2
        and distinctive_letter_count < 2
        and not script_evidence
    ):
        return None
    if (
        len(tokens) >= _MIN_DENSITY_TEXT_TOKENS
        and best_supported_score * 100 >= len(tokens) * _MIN_SUPPORTED_LANGUAGE_DENSITY_PERCENT
    ):
        return None
    if unsupported_service_score < _MIN_LANGUAGE_SERVICE_WORDS:
        return None
    if unsupported_service_score <= best_supported_score + dominance_margin:
        return None
    minimum_service_score = max(
        _MIN_LANGUAGE_SERVICE_WORDS,
        (len(tokens) * _MIN_LANGUAGE_SERVICE_RATIO_PERCENT + 99) // 100,
    )
    if unsupported_service_score < minimum_service_score:
        return None
    if best_supported_score >= 1:
        required_language_match = _language_match(sentences, allowed)
        if required_language_match is not None:
            return None

    service_words = _LANGUAGE_SERVICE_WORDS[best_unsupported]
    fragments = tuple(
        sentence
        for sentence in sentences
        if set(re.findall(r"[^\W\d_]+", sentence.lower(), flags=re.UNICODE)) & service_words
    )
    if not fragments and sentences:
        fragments = (sentences[0],)
    languages = (best_unsupported,)
    supported = tuple(sorted(allowed))
    return RuleMatch(
        "description_language_not_supported",
        fragments,
        f"The vacancy description is primarily in unsupported language(s): {', '.join(languages)}; supported languages: {', '.join(supported)}.",
        {
            "detected_languages": languages,
            "supported_languages": supported,
            "language_service_scores": tuple(sorted(scores.items())),
            "token_count": len(tokens),
            "cyrillic_letter_count": cyrillic_letters,
            "latin_letter_count": latin_letters,
        },
    )


def _unknown_language_match(
    description: str,
    sentences: tuple[str, ...],
    supported: tuple[str, ...],
    scores: dict[str, int],
    token_count: int,
) -> RuleMatch:
    return RuleMatch(
        "description_language_not_supported",
        sentences[:1] if sentences else (description,),
        "The vacancy description has no sufficiently supported-language signal and is treated as unsupported.",
        {
            "detected_languages": ("unknown",),
            "supported_languages": supported,
            "language_service_scores": tuple(sorted(scores.items())),
            "token_count": token_count,
        },
    )


def _has_unsupported_language_title_marker(title: str, supported_codes: set[str]) -> bool:
    tokens = set(re.findall(r"[^\W\d_]+", title.lower(), flags=re.UNICODE))
    return any(
        code not in supported_codes and tokens & markers
        for code, markers in _LANGUAGE_TITLE_MARKERS.items()
    )


def _base_language_service_score(
    code: str,
    tokens: tuple[str, ...],
    token_counts: dict[str, int] | None = None,
) -> int:
    token_counts = token_counts if token_counts is not None else Counter(tokens)
    score_words = _LANGUAGE_SCORE_WORDS[code]
    ambiguous_score = sum(
        token_counts.get(token, 0) for token in score_words["ambiguous"]
    )
    exclusive_score = sum(
        token_counts.get(token, 0) for token in score_words["exclusive"]
    )
    if code != "en":
        return exclusive_score + min(ambiguous_score, 2)
    high_signal_score = sum(
        token_counts.get(token, 0) for token in score_words["high_signal"]
    )
    low_signal_score = min(
        sum(token_counts.get(token, 0) for token in score_words["low_signal"]),
        4,
    )
    return high_signal_score + low_signal_score + min(ambiguous_score, 2)


def _language_service_scores(tokens: tuple[str, ...]) -> dict[str, int]:
    counts = {
        code: [0, 0, 0]
        for code in _LANGUAGE_SERVICE_WORDS
    }
    for token in tokens:
        for code, kinds in _LANGUAGE_TOKEN_SCORE_KINDS.get(token, ()):
            for kind in kinds:
                counts[code][kind] += 1
    return {
        code: (
            values[0]
            + min(values[1], 2)
            if code != "en"
            else values[0] + min(values[2], 4) + min(values[1], 2)
        )
        for code, values in counts.items()
    }


@lru_cache(maxsize=16)
def _letter_range_translation_table(
    ranges: tuple[tuple[int, int], ...],
) -> dict[int, None]:
    return {
        codepoint: None
        for start, end in ranges
        for codepoint in range(start, end + 1)
    }


def _count_letters_in_ranges(text: str, ranges: tuple[tuple[int, int], ...]) -> int:
    return len(text) - len(text.translate(_letter_range_translation_table(ranges)))


def _supported_sentence_coverage(
    description: str,
    supported_languages: set[str],
) -> tuple[int, int]:
    body = description.split("Job criteria:", 1)[0]
    substantive_sentences: list[tuple[str, tuple[str, ...]]] = []
    for sentence in _sentences(body):
        tokens = tuple(re.findall(r"[^\W\d_]+", sentence.lower(), flags=re.UNICODE))
        if len(tokens) >= _MIN_SUPPORTED_SENTENCE_TOKENS:
            substantive_sentences.append((sentence, tokens))
    if not substantive_sentences:
        return 100, 0

    supported_count = 0
    minimum_supported_count = (
        len(substantive_sentences) * _MIN_SUPPORTED_SENTENCE_COVERAGE_PERCENT + 99
    ) // 100
    for sentence, tokens in substantive_sentences:
        sentence_service_scores = _language_service_scores(tokens)
        sentence_scores = []
        for code in supported_languages:
            if code == "ru":
                letters = _count_letters_in_ranges(sentence, _LANGUAGE_LETTER_RANGES["cyrillic"])
                if letters >= max(8, _count_letters_in_ranges(sentence, _LANGUAGE_LETTER_RANGES["latin"])):
                    sentence_scores.append(len(tokens))
                    continue
            score = sentence_service_scores[code]
            sentence_scores.append(score)
        sentence_score = max(sentence_scores, default=0)
        sentence_supported = sentence_score * 100 >= len(tokens) * _MIN_SUPPORTED_LANGUAGE_DENSITY_PERCENT
        supported_count += sentence_supported
        if supported_count >= minimum_supported_count:
            return _MIN_SUPPORTED_SENTENCE_COVERAGE_PERCENT, len(substantive_sentences)
    return (
        supported_count * 100 // len(substantive_sentences),
        len(substantive_sentences),
    )


_DOMAIN_EXPERTISE_RULES = (
    {
        "domain": "banking_core_and_payment_infrastructure",
        "legacy_rule_id": "banking_software_portfolio",
        "label": "banking core and payment infrastructure",
        "patterns": (
            r"\bcore\s+banking\b",
            r"\bbanking\s+(?:software|platform|solutions?|product\s+portfolio)\b",
            r"\bsoftware\s+products?\s+for\s+(?:banks|banking)\b",
            r"\bpayment\s+infrastructure\b",
        ),
        "mandatory_patterns": (r"\bpayment\s+infrastructure\b",),
    },
    {
        "domain": "credit_p_and_l",
        "legacy_rule_id": "banking_credit_p_and_l",
        "label": "credit P&L",
        "patterns": (
            r"\b(?:bank\w*|banking)\b.*\b(?:credit|lending|loan|mortgage)\b.*(?:\bp\s*&\s*l\b|\bprofit\s+and\s+loss\b|\bp&l\b)",
            r"(?:\bp\s*&\s*l\b|\bprofit\s+and\s+loss\b|\bp&l\b).*\b(?:bank\w*|banking)\b.*\b(?:credit|lending|loan|mortgage)\b",
        ),
        "all_of": (
            r"\b(?:bank\w*|banking)\b",
            r"\b(?:credit|lending|loan|mortgage)\b",
            r"\b(?:p\s*&\s*l|p&l|profit\s+and\s+loss)\b",
        ),
    },
    {
        "domain": "adtech_and_advertising_platforms",
        "legacy_rule_id": "advertising_platform",
        "label": "adtech and advertising platforms",
        "patterns": (
            r"\b(?:must\s+have|required|deeply?\s+speciali[sz]ed|experience|background|expertise|knowledge|(?:strong|deep|solid)\s+understanding)\b[^.!?\n]{0,140}\b(?:adtech|ad\s*tech|digital\s+ads?|advertising\s+network|ads?\s+platform|ad\s+operations?|martech|marketing\s+technology|online\s+marketing|ad\s+impressions?)\b",
            r"\b(?:adtech|ad\s*tech|digital\s+ads?|advertising\s+network|ads?\s+platform|ad\s+operations?|martech|marketing\s+technology|online\s+marketing|ad\s+impressions?)\b[^.!?\n]{0,140}\b(?:must\s+have|required|experience|background|expertise|knowledge)\b",
        ),
        "subject_predicate": "_is_advertising_platform",
    },
    {
        "domain": "fraud_and_anti_fraud",
        "legacy_rule_id": "fraud_and_anti_fraud",
        "label": "fraud and anti-fraud",
        "patterns": (
            r"\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b[^.!?\n]{0,140}\b(?:fraud|anti[- ]fraud)\b",
            r"\b(?:fraud|anti[- ]fraud)\b[^.!?\n]{0,140}\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b",
            r"\b(?:fraud\s+(?:prevention|detection|risk)|anti[- ]fraud\s+(?:systems?|products?))\b",
            r"\b(?:fraud|financial\s+crime)\s+(?:systems?|products?|team|platform|risk)\b",
        ),
    },
    {
        # Only an explicit requirement counts: an EdTech company hiring a product
        # lead is the target, an EdTech track record demanded of the candidate
        # is not (MindGate, release 084423Z: "Обязателен опыт в EdTech").
        "domain": "edtech_and_online_education",
        "legacy_rule_id": "edtech_and_online_education",
        "label": "EdTech and online education",
        "patterns": (
            r"(?:\b(?:required|mandatory|must\s+have)\b|обязател\w*)[^.!?\n]{0,140}(?:\bed-?tech\b|\bonline\s+education\b|\beducation(?:al)?\s+technology\b|онлайн[- ]образовани\w*)",
            r"(?:\bed-?tech\b|\bonline\s+education\b|\beducation(?:al)?\s+technology\b|онлайн[- ]образовани\w*)[^.!?\n]{0,80}(?:\b(?:required|mandatory|must)\b|обязател\w*)",
        ),
    },
    {
        "domain": "erp_and_manufacturing_systems",
        "legacy_rule_id": "erp_and_manufacturing_systems",
        "label": "ERP and manufacturing systems",
        "patterns": (
            r"\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b[^.!?\n]{0,160}\b(?:erps?|enterprise\s+resource\s+planning|manufacturing\s+systems?)\b",
            r"\b(?:erps?|enterprise\s+resource\s+planning|manufacturing\s+systems?)\b[^.!?\n]{0,160}\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b",
            r"\b(?:erps?|enterprise\s+resource\s+planning)\b[^.!?\n]{0,120}\bmanufactur\w*\b",
        ),
    },
)

_OPTIONAL_DOMAIN_CUES = re.compile(
    r"\b(?:preferred|nice[- ]to[- ]haves?|bonus|plus|advantage|desirable|optional)\b"
    r"|\bstand\s+out\s+by\s+having\b"
    r"|\b(?:continuous\s+)?opportunities?\s+to\s+(?:learn|grow|expand)\b"
    r"|\bexpand\s+your\s+expertise\b",
    re.IGNORECASE,
)


def _industry_matches(sentences: Iterable[str], title: str) -> tuple[RuleMatch, ...]:
    sentence_list = tuple(sentences)
    matches: list[RuleMatch] = []
    combined = " ".join(sentence_list)
    product_title = bool(_find_patterns(title, _PRODUCT_LEADERSHIP))
    for spec in _DOMAIN_EXPERTISE_RULES:
        if not product_title:
            continue
        fragments = tuple(
            sentence
            for sentence in sentence_list
            if _domain_sentence_matches(sentence, spec, title)
        )
        if not fragments and spec.get("all_of") and all(
            _compiled_pattern(pattern).search(combined) for pattern in spec["all_of"]
        ):
            fragments = tuple(
                sentence
                for sentence in sentence_list
                if _compiled_pattern(
                    r"\b(?:bank\w*|banking|credit|lending|loan|mortgage|p\s*&\s*l|p&l|profit\s+and\s+loss)\b",
                ).search(sentence)
            )
        if fragments:
            matches.append(
                RuleMatch(
                    "domain_expertise_required",
                    tuple(dict.fromkeys(fragments)),
                    f"The role requires deep expertise in {spec['label']}, which is outside the target role fit.",
                    {
                        "domain": spec["domain"],
                        "legacy_rule_id": spec["legacy_rule_id"],
                    },
                )
            )

    commercial_role_fragments = tuple(
        sentence
        for sentence in sentence_list
        if _compiled_pattern(
            r"\b(?:chief|head|director|vp|vice president|lead|leader|leadership)\s+(?:of\s+)?commercial\b",
        ).search(sentence)
    )
    commercial_duty_fragments = tuple(
        sentence for sentence in sentence_list if _has_ecommerce_commercial_duty(sentence)
    )
    ecommerce_fragments = tuple(
        sentence
        for sentence in sentence_list
        if _compiled_pattern(r"\b(?:e[- ]?commerce|ecommerce|marketplace)\b").search(sentence)
    )
    if commercial_role_fragments and commercial_duty_fragments and ecommerce_fragments:
        matches.append(
            RuleMatch(
                "ecommerce_commercial_leadership",
                tuple(dict.fromkeys((*commercial_role_fragments, *commercial_duty_fragments, *ecommerce_fragments))),
                "The text identifies commercial/e-commerce or assortment leadership, which is outside the target role fit.",
                {},
            )
        )
    return tuple(matches)


def _domain_sentence_matches(sentence: str, spec: dict[str, Any], title: str) -> bool:
    if _OPTIONAL_DOMAIN_CUES.search(sentence):
        return False
    subject_predicate = spec.get("subject_predicate")
    if isinstance(subject_predicate, str):
        subject_predicate = globals()[subject_predicate]
    if subject_predicate is not None and subject_predicate(sentence):
        return True
    if not _combined_pattern(tuple(spec["patterns"])).search(sentence):
        return False
    if spec["domain"] == "banking_core_and_payment_infrastructure" and not _compiled_pattern(
        r"\b(?:portfolio|strategy|product|own|lead|responsible|accountable|experience|expertise|required)\b",
    ).search(sentence) and not _compiled_pattern(r"\b(?:banking|payments?)\b").search(title):
        return False
    if spec["domain"] == "banking_core_and_payment_infrastructure":
        payment_infrastructure = _combined_pattern(
            tuple(spec.get("mandatory_patterns", ()))
        ).search(sentence)
        if payment_infrastructure and not _compiled_pattern(
            r"\b(?:must|required|experience|background|expertise|knowledge|deep\s+understanding)\b",
        ).search(sentence):
            return False
    return True


def _adjacent_function_matches(title: str) -> tuple[RuleMatch, ...]:
    """Reject a title whose mandate is an adjacent function rather than product.

    Only the title is examined. A product-leadership description mentions
    marketing and design in passing all the time, so reading the body here
    would reject the very roles the rule exists to protect.
    """
    fragments = _find_patterns(title, _ADJACENT_FUNCTION_PATTERNS)
    if not fragments:
        return ()
    return (
        RuleMatch(
            "adjacent_product_function",
            fragments,
            "The title names an adjacent function (design, marketing, localisation, partnerships) rather than a product mandate.",
            {"title": title},
        ),
    )


def _offers_sponsorship(text: str) -> bool:
    """True only where a sponsorship cue appears in a sentence that does not deny it.

    Sentence scope is what makes this readable: "we cannot sponsor visas" and
    "we sponsor work visas" differ by one word that sits beside the cue, not
    anywhere in the posting. A denial elsewhere in a long description must not
    cancel a genuine offer, and an offer elsewhere must not excuse a denial in
    the sentence that states the requirement.
    """
    denial = _compiled_pattern(_SPONSORSHIP_DENIAL_PATTERN)
    for sentence in _sentences(text):
        if not _find_patterns(sentence, _SPONSORSHIP_CUES):
            continue
        if denial.search(sentence.lower()):
            continue
        return True
    return False


def _work_authorisation_matches(location: str, text: str) -> tuple[RuleMatch, ...]:
    """Separate a US onsite requirement from an open question about remote eligibility."""
    if not _find_patterns(location, _US_LOCATION_PATTERNS):
        return ()
    if _offers_sponsorship(text):
        return ()
    if _find_patterns(location, (_REMOTE_LOCATION_PATTERN,)):
        return (
            RuleMatch(
                "us_remote_eligibility_unknown",
                (location,),
                "The role is remote within the US and states no eligibility or sponsorship, which is unresolved rather than disqualifying.",
                {"location": location},
            ),
        )
    return (
        RuleMatch(
            "us_onsite_without_sponsorship",
            (location,),
            "The role requires presence in the US and states no sponsorship, which is a hard gate.",
            {"location": location},
        ),
    )


# A product lead whose mandate is content itself - templates, creators, content
# partners - is expected to know the whole UGC landscape (owner, canva Content
# Group, release 084423Z). The title must name content as the mandate and the
# text must describe that domain; either alone is ordinary product work.
_CONTENT_MANDATE_TITLE_PATTERN = r"\b(?:content|creators?|ugc|user[- ]generated)\b"
_UGC_DOMAIN_PATTERN = (
    r"\b(?:creators?|user[- ]generated|ugc|content\s+(?:library|platform|partners?|acquisitions?|review|sources?|marketplace))\b"
)


def _content_platform_matches(title: str, text: str) -> tuple[RuleMatch, ...]:
    if not _find_patterns(title, _PRODUCT_LEADERSHIP):
        return ()
    title_fragments = _find_patterns(title, (_CONTENT_MANDATE_TITLE_PATTERN,))
    domain_fragments = _find_patterns(text, (_UGC_DOMAIN_PATTERN,))
    if not title_fragments or not domain_fragments:
        return ()
    return (
        RuleMatch(
            "domain_expertise_required",
            (title, *domain_fragments),
            "The role's mandate is a content/UGC platform, which requires deep knowledge of the UGC landscape.",
            {"domain": "ugc_and_content_platforms", "legacy_rule_id": "ugc_and_content_platforms"},
        ),
    )


def _russia_location_matches(location: str) -> tuple[RuleMatch, ...]:
    if not _find_patterns(location, _RUSSIA_LOCATION_PATTERNS):
        return ()
    return (
        RuleMatch(
            "russia_location",
            (location,),
            "The role is located in Russia, which the owner excludes.",
            {"location": location},
        ),
    )


def _advanced_degree_matches(sentences: Iterable[str]) -> tuple[RuleMatch, ...]:
    required = _compiled_pattern(_DEGREE_REQUIRED_CUE)
    alternative = _compiled_pattern(_DEGREE_ALTERNATIVE_CUE)
    degree = _compiled_pattern(_ADVANCED_DEGREE_PATTERN)
    fragments = tuple(
        sentence
        for sentence in sentences
        if degree.search(sentence) and required.search(sentence) and not alternative.search(sentence)
    )
    if not fragments:
        return ()
    return (
        RuleMatch(
            "advanced_degree_required",
            fragments,
            "The text makes a master's degree or doctorate a hard requirement.",
            {},
        ),
    )


def _staffing_agency_matches(company: str, text: str) -> tuple[RuleMatch, ...]:
    staffing_signal_text = f"{company} {text}".lower()
    if not any(
        term in staffing_signal_text
        for term in (
            "client",
            "behalf",
            "recruit",
            "staffing",
            "headhunt",
            "executive search",
            "talent acquisition",
            "partner",
            "human capital",
        )
    ):
        return ()
    patterns = (
        r"\bour\s+client\s+(?:is\s+)?(?:seeking|hiring|looking\s+for|recruiting)\b",
        r"\bour\s+client\s*,\s*[^.!?\n]{0,120}\b(?:is\s+)?(?:seeking|hiring|looking\s+for|recruiting)\b",
        r"\bon\s+behalf\s+of\s+(?:our|a|the)\s+(?:client|partner)\b",
        r"\blisted\s+on\s+behalf\s+of\b",
        r"\bwe\s+are\s+recruiting\s+for\s+our\s+client\b",
        r"\bpartner\s+company\b[^.!?\n]{0,80}\b(?:applications?|hiring)\b",
        r"\b(?:we\s+are|we're)\s+(?:an?\s+)?(?:recruitment|staffing|executive\s+search|talent\s+acquisition)\s+(?:agency|firm|partner)\b",
        r"\b(?:recruitment|staffing|executive\s+search|talent\s+acquisition)\s+(?:agency|firm|partner)\s+(?:representing|for|that|who)\b",
    )
    company_patterns = (
        r"\bhuman\s+capital\b",
        r"\b(?:staffing|recruit(?:ment|er)|headhunt(?:ing)?|executive\s+search)\b",
        r"\btalent\s+acquisition(?:\s+partner)?\b",
    )
    found = _find_patterns(text, patterns)
    found += _find_patterns(company, company_patterns)
    if not found:
        return ()
    return (
        RuleMatch(
            "staffing_agency_or_aggregator",
            found,
            "The vacancy appears to be published by a staffing agency or aggregator for a client, not by the hiring company.",
            {"signals": found},
        ),
    )
def _is_advertising_platform(sentence: str) -> re.Match[str] | None:
    if _AD_NEGATION_PATTERN.search(sentence):
        return None
    match = _AD_PLATFORM_PATTERN.search(sentence)
    if match is None:
        return None
    if not _has_positive_ad_subject_signal(sentence):
        return None
    prefix = sentence[max(0, match.start() - 80) : match.start()]
    if _EXPERIENCE_PREFIX_PATTERN.search(prefix):
        return None
    return match


def _has_positive_ad_subject_signal(sentence: str) -> bool:
    for subject_match in _AD_SUBJECT_PATTERN.finditer(sentence):
        prefix = sentence[max(0, subject_match.start() - 24) : subject_match.start()]
        if not _NEGATED_SUBJECT_PATTERN.search(prefix):
            return True
    return False


def _has_ecommerce_commercial_duty(sentence: str) -> re.Match[str] | None:
    for duty_match in _ECOMMERCE_DUTY_PATTERN.finditer(sentence):
        prefix = sentence[max(0, duty_match.start() - 80) : duty_match.start()]
        if not _NEGATED_SUBJECT_PATTERN.search(prefix):
            return duty_match
    return None


def _normalise_language(value: str) -> str:
    key = value.strip().lower()
    if key in _LANGUAGE_CODES:
        return _LANGUAGE_CODES[key]
    if key in _LANGUAGES:
        return _LANGUAGES[key]
    raise ValueError(f"unsupported language: {value}")


def _language_match(sentences: Iterable[str], owner_languages: Iterable[str]) -> RuleMatch | None:
    allowed = {_normalise_language(language) for language in owner_languages}
    required: dict[str, list[str]] = {}
    for sentence in sentences:
        if not _LANGUAGE_ANY_REQUIREMENT_PATTERN.search(sentence):
            continue
        for code, patterns in _LOCALIZED_LANGUAGE_REQUIREMENT_PATTERNS.items():
            if code not in allowed and _combined_pattern(
                tuple(pattern.pattern for pattern in patterns)
            ).search(sentence):
                required.setdefault(code, []).append(sentence)
        for code, patterns in _LANGUAGE_CODE_REQUIREMENT_PATTERNS.items():
            if code not in allowed and _combined_pattern(
                tuple(pattern.pattern for pattern in patterns)
            ).search(sentence):
                required.setdefault(code, []).append(sentence)
        if not _LANGUAGE_REQUIRED_CUES.search(sentence):
            continue
        for language_match in _LANGUAGE_NAME_COMBINED.finditer(sentence):
            name = language_match.group().lower()
            if not _is_optional_language(sentence, language_match, _OPTIONAL_LANGUAGE_CUES):
                code = _LANGUAGES[name]
                if code not in allowed:
                    required.setdefault(code, []).append(sentence)
    if not required:
        return None
    fragments = tuple(dict.fromkeys(fragment for values in required.values() for fragment in values))
    languages = tuple(sorted(required))
    return RuleMatch(
        "required_language_unavailable",
        fragments,
        f"The text requires working language(s) unavailable to the owner: {', '.join(languages)}.",
        {"languages": languages, "owner_languages": tuple(sorted(allowed))},
    )


def _is_optional_language(sentence: str, language_match: re.Match[str], optional: re.Pattern[str]) -> bool:
    start = max(0, language_match.start() - 40)
    end = min(len(sentence), language_match.end() + 60)
    return optional.search(sentence[start:end]) is not None


def _urgency_matches(sentences: Iterable[str], short_contract_months: int) -> tuple[RuleMatch, ...]:
    sentence_list = tuple(sentences)
    if not any(
        term in " ".join(sentence_list).lower()
        for term in (
            "interim",
            "maternity",
            "parental",
            "paternity",
            "replacement",
            "backfill",
            "cover",
            "contract",
            "fixed-term",
            "temporary",
            "month",
        )
    ):
        return ()
    interim_fragments: list[str] = []
    short_fragments: list[str] = []
    duration_pattern = re.compile(r"\b(\d{1,3})\s*[- ]?month(?:s)?\b", re.IGNORECASE)
    for sentence in sentence_list:
        if re.search(
            r"\binterim\b|\b(?:maternity|parental|parent)\s+(?:leave\s+)?cover\b|\b(?:temporary\s+)?replacement\b|\bbackfill\b",
            sentence,
            re.IGNORECASE,
        ):
            interim_fragments.append(sentence)
        duration = duration_pattern.search(sentence)
        short_term_context = re.search(r"\b(?:contract|fixed[- ]term|temporary)\b", sentence, re.IGNORECASE)
        leave_replacement_context = re.search(
            r"\b(?:while|cover|replace|replacement|backfill|interim)\b[^.!?\n]{0,100}\b(?:maternity|parental|paternity)\s+leave\b"
            r"|\b(?:maternity|parental|paternity)\s+leave\b[^.!?\n]{0,100}\b(?:cover|replacement|interim|while)\b",
            sentence,
            re.IGNORECASE,
        )
        if duration and int(duration.group(1)) < short_contract_months and (
            short_term_context or leave_replacement_context
        ):
            short_fragments.append(sentence)
    matches: list[RuleMatch] = []
    if interim_fragments:
        matches.append(
            RuleMatch(
                "interim_or_cover",
                tuple(dict.fromkeys(interim_fragments)),
                "The text describes an interim, replacement, maternity-cover, or parental-cover appointment.",
                {},
            )
        )
    if short_fragments:
        matches.append(
            RuleMatch(
                "short_contract",
                tuple(dict.fromkeys(short_fragments)),
                f"The text describes a contract shorter than the {short_contract_months}-month threshold.",
                {"threshold_months": short_contract_months},
            )
        )
    return tuple(matches)


def _ruleset_version() -> str:
    """Digest the rule source itself, so a rule edit cannot ship under an old version.

    A hand-bumped constant is only as good as the memory of whoever edits a
    regex below; comparing two observation runs then silently compares two
    different rule sets. Hashing this module makes the version a consequence of
    the rules rather than a promise about them. A cosmetic edit also moves it,
    which is the safe direction: it claims a difference that may not matter,
    never sameness that is untrue.
    """
    try:
        source = Path(__file__).read_bytes()
    except OSError:
        return "rf1-unknown"
    return f"rf1-{hashlib.sha256(source).hexdigest()[:12]}"


ROLE_FIT_RULESET_VERSION = _ruleset_version()
