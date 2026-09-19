"""Deterministic, text-only role-fit rules for Job Intel."""

from __future__ import annotations

from dataclasses import dataclass
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

_PRODUCT_LEADERSHIP = (
    r"\b(?:chief|head|director|vp|vice president|group)\s+(?:of\s+)?product\b",
    r"\b(?:chief|head|director|vp|vice president|group)\s+(?:of\s+)?product\s+(?:management|function|area)\b",
    r"\b(?:chief|head|director|vp|vice president|group)\s*[, :/\-&—–]+\s*(?:of\s+)?product(?:\s+(?:management|function|area))?\b",
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

    software_fragments = _find_patterns(text, (*_PRODUCT_LEADERSHIP, *_SOFTWARE_SIGNALS, *_SCOPE_SIGNALS))
    leadership_fragments = _find_patterns(title, _PRODUCT_LEADERSHIP)
    software_signal_fragments = _find_patterns(text, _SOFTWARE_SIGNALS)
    if leadership_fragments and software_signal_fragments:
        matches.append(
            RuleMatch(
                "software_product_leadership",
                software_fragments,
                "Product leadership is evidenced in a software/SaaS organization; mandate breadth does not gate acceptance.",
                {"leadership_fragments": leadership_fragments, "software_fragments": software_signal_fragments},
            )
        )

    matches.extend(_industry_matches(sentences, title))
    matches.extend(_staffing_agency_matches(company, text))

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
    elif any(match.rule_id == "software_product_leadership" for match in matches):
        verdict = "accept"
        explanation = "The role matches the software/SaaS product-leadership rule."
    else:
        verdict = "reject"
        explanation = "No qualifying software/SaaS product-leadership evidence was found."

    return RoleFitDecision(verdict, tuple(matches), text, explanation)


_HARD_REJECT_RULES = frozenset(
    {
        "domain_expertise_required",
        "ecommerce_commercial_leadership",
        "interim_or_cover",
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


def _find_patterns(text: str, patterns: Iterable[str]) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            fragment = text[match.start() : match.end()].strip()
            if fragment:
                found.append((match.start(), fragment))
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
    tokens = tuple(re.findall(r"[^\W\d_]+", description.lower(), flags=re.UNICODE))
    if len(tokens) < _MIN_LANGUAGE_TEXT_TOKENS:
        return None

    allowed = {_normalise_language(language) for language in supported_languages}
    if set(KNOWN_LANGUAGE_CODES).issubset(allowed):
        return None
    cyrillic_letters = _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES["cyrillic"])
    latin_letters = _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES["latin"])
    scores: dict[str, int] = {}
    for code, service_words in _LANGUAGE_SERVICE_WORDS.items():
        service_score = _base_language_service_score(code, tokens)
        marker_score = sum(
            token in _LANGUAGES and _LANGUAGES[token] == code
            for token in tokens
        )
        distinctive_score = sum(char in _LANGUAGE_DISTINCTIVE_LETTERS.get(code, ()) for char in description.lower())
        script_name = _LANGUAGE_SCRIPT_BY_CODE.get(code)
        script_letters = (
            _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES[script_name])
            if script_name is not None
            else 0
        )
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
    unsupported_service_score = _base_language_service_score(best_unsupported, tokens)
    unsupported_service_score += min(
        sum(
            token in _LANGUAGES and _LANGUAGES[token] == best_unsupported
            for token in tokens
        ),
        2,
    )
    unsupported_service_score += min(
        sum(char in _LANGUAGE_DISTINCTIVE_LETTERS.get(best_unsupported, ()) for char in description.lower()),
        2,
    )
    unsupported_script = _LANGUAGE_SCRIPT_BY_CODE.get(best_unsupported)
    unsupported_script_letters = (
        _count_letters_in_ranges(description, _LANGUAGE_LETTER_RANGES[unsupported_script])
        if unsupported_script is not None
        else 0
    )
    unsupported_service_score += min(unsupported_script_letters // 8, 3)
    distinctive_word_count = sum(
        token in _LANGUAGE_DISTINCTIVE_SERVICE_WORDS.get(best_unsupported, frozenset())
        for token in tokens
    )
    distinctive_letter_count = sum(
        char in _LANGUAGE_DISTINCTIVE_LETTERS.get(best_unsupported, frozenset())
        for char in description.lower()
    )
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
                _sentences(description),
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
                _sentences(description),
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
        required_language_match = _language_match(_sentences(description), allowed)
        if required_language_match is not None:
            return None

    sentences = _sentences(description)
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


def _base_language_service_score(code: str, tokens: tuple[str, ...]) -> int:
    service_words = _LANGUAGE_SERVICE_WORDS[code]
    ambiguous_score = sum(
        token in service_words and token in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
        for token in tokens
    )
    exclusive_score = sum(
        token in service_words and token not in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
        for token in tokens
    )
    if code != "en":
        return exclusive_score + min(ambiguous_score, 2)
    high_signal_score = sum(
        token in service_words
        and token not in _ENGLISH_LOW_SIGNAL_WORDS
        and token not in _AMBIGUOUS_LANGUAGE_SERVICE_WORDS
        for token in tokens
    )
    low_signal_score = min(
        sum(token in _ENGLISH_LOW_SIGNAL_WORDS for token in tokens),
        4,
    )
    return high_signal_score + low_signal_score + min(ambiguous_score, 2)


def _count_letters_in_ranges(text: str, ranges: tuple[tuple[int, int], ...]) -> int:
    return sum(any(start <= ord(char) <= end for start, end in ranges) for char in text)


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
    for sentence, tokens in substantive_sentences:
        sentence_scores = []
        for code in supported_languages:
            if code == "ru":
                letters = _count_letters_in_ranges(sentence, _LANGUAGE_LETTER_RANGES["cyrillic"])
                if letters >= max(8, _count_letters_in_ranges(sentence, _LANGUAGE_LETTER_RANGES["latin"])):
                    sentence_scores.append(len(tokens))
                    continue
            score = _base_language_service_score(code, tokens)
            sentence_scores.append(score)
        sentence_score = max(sentence_scores, default=0)
        sentence_supported = sentence_score * 100 >= len(tokens) * _MIN_SUPPORTED_LANGUAGE_DENSITY_PERCENT
        supported_count += sentence_supported
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
            r"\b(?:must\s+have|required|deeply?\s+speciali[sz]ed|experience|background|expertise|knowledge)\b[^.!?\n]{0,140}\b(?:adtech|ad\s*tech|digital\s+ads?|advertising\s+network|ads?\s+platform|ad\s+operations?)\b",
            r"\b(?:adtech|ad\s*tech|digital\s+ads?|advertising\s+network|ads?\s+platform|ad\s+operations?)\b[^.!?\n]{0,140}\b(?:must\s+have|required|experience|background|expertise|knowledge)\b",
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
        "domain": "erp_and_manufacturing_systems",
        "legacy_rule_id": "erp_and_manufacturing_systems",
        "label": "ERP and manufacturing systems",
        "patterns": (
            r"\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b[^.!?\n]{0,160}\b(?:erp|enterprise\s+resource\s+planning|manufacturing\s+systems?)\b",
            r"\b(?:erp|enterprise\s+resource\s+planning|manufacturing\s+systems?)\b[^.!?\n]{0,160}\b(?:must\s+have|required|experience|background|expertise|knowledge|deep\s+understanding)\b",
            r"\b(?:erp|enterprise\s+resource\s+planning)\b[^.!?\n]{0,120}\bmanufactur\w*\b",
        ),
    },
)

_OPTIONAL_DOMAIN_CUES = re.compile(
    r"\b(?:preferred|nice[- ]to[- ]haves?|bonus|plus|advantage|desirable|optional)\b",
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
            re.search(pattern, combined, re.IGNORECASE) for pattern in spec["all_of"]
        ):
            fragments = tuple(
                sentence
                for sentence in sentence_list
                if re.search(
                    r"\b(?:bank\w*|banking|credit|lending|loan|mortgage|p\s*&\s*l|p&l|profit\s+and\s+loss)\b",
                    sentence,
                    re.IGNORECASE,
                )
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
        if re.search(
            r"\b(?:chief|head|director|vp|vice president|lead|leader|leadership)\s+(?:of\s+)?commercial\b",
            sentence,
            re.IGNORECASE,
        )
    )
    commercial_duty_fragments = tuple(
        sentence for sentence in sentence_list if _has_ecommerce_commercial_duty(sentence)
    )
    ecommerce_fragments = tuple(
        sentence
        for sentence in sentence_list
        if re.search(r"\b(?:e[- ]?commerce|ecommerce|marketplace)\b", sentence, re.IGNORECASE)
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
    matched = tuple(
        pattern
        for pattern in spec["patterns"]
        if re.search(pattern, sentence, re.IGNORECASE)
    )
    if not matched:
        return False
    if spec["domain"] == "banking_core_and_payment_infrastructure" and not re.search(
        r"\b(?:portfolio|strategy|product|own|lead|responsible|accountable|experience|expertise|required)\b",
        sentence,
        re.IGNORECASE,
    ) and not re.search(r"\b(?:banking|payments?)\b", title, re.IGNORECASE):
        return False
    if spec["domain"] == "banking_core_and_payment_infrastructure":
        payment_infrastructure = any(
            re.search(pattern, sentence, re.IGNORECASE)
            for pattern in spec.get("mandatory_patterns", ())
        )
        if payment_infrastructure and not re.search(
            r"\b(?:must|required|experience|background|expertise|knowledge|deep\s+understanding)\b",
            sentence,
            re.IGNORECASE,
        ):
            return False
    return True


def _staffing_agency_matches(company: str, text: str) -> tuple[RuleMatch, ...]:
    patterns = (
        r"\bour\s+client\b",
        r"\bon\s+behalf\s+of\s+(?:our|a|the)\s+(?:client|partner)\b",
        r"\blisted\s+on\s+behalf\s+of\b",
        r"\bpartner\s+company\b[^.!?\n]{0,80}\b(?:applications?|hiring)\b",
        r"\b(?:recruitment|staffing|executive\s+search)\s+agency\b",
    )
    company_patterns = (
        r"\bhuman\s+capital\b",
        r"\b(?:staffing|recruit(?:ment|er)|headhunt(?:ing)?|executive\s+search)\b",
        r"\b(?:talent|hire)\w*\b",
    )
    found = _find_patterns(text, patterns)
    found += _find_patterns(company, company_patterns)
    if not found and re.search(
        r"\b(?:limited|ltd|consulting|consultancy|solutions|services)\b",
        company,
        re.IGNORECASE,
    ) and re.search(
        r"\b(?:telco|telecom)\b.{0,240}\bfintech\b",
        text,
        re.IGNORECASE | re.DOTALL,
    ) and re.search(
        r"\b(?:across|multiple)\s+(?:business\s+units|industr(?:y|ies)|markets)\b",
        text,
        re.IGNORECASE,
    ):
        found += _find_patterns(
            company,
            (r"\b(?:limited|ltd|consulting|consultancy|solutions|services)\b",),
        )
        found += _find_patterns(
            text,
            (
                r"\b(?:telco|telecom)\b.{0,240}\bfintech\b",
                r"\b(?:across|multiple)\s+(?:business\s+units|industr(?:y|ies)|markets)\b",
            ),
        )
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
    if re.search(
        r"\b(?:not|no)\s+(?:an?\s+)?(?:advertising|adtech|ad\s*tech)\s+(?:platform|company|business)\b",
        sentence,
        re.IGNORECASE,
    ):
        return None
    match = re.search(
        r"\b(?:adtech|ad\s*tech|advertising\s+technology\s+(?:platform|company|business)|programmatic\s+advertising\s+(?:platform|business)|advertising\s+platform|demand[- ]side\s+platform)\b",
        sentence,
        re.IGNORECASE,
    )
    if match is None:
        return None
    if not _has_positive_ad_subject_signal(sentence):
        return None
    prefix = sentence[max(0, match.start() - 80) : match.start()]
    if re.search(r"\b(?:experience|background|familiarity)\s+with\s+(?:an?\s+)?$", prefix, re.IGNORECASE):
        return None
    return match


def _has_positive_ad_subject_signal(sentence: str) -> bool:
    subject_pattern = re.compile(
        r"\b(?:ad[- ]?serv(?:ing|er)|dsp|ssp|ad\s+ops?|ad\s+operations|ad\s+exchange|real[- ]time\s+bidding|rtb|ad(?:vertising)?\s+auction|demand[- ]side\s+platform|advertising\s+inventory|campaign\s+delivery|ecpm|monetization)\b",
        re.IGNORECASE,
    )
    for subject_match in subject_pattern.finditer(sentence):
        prefix = sentence[max(0, subject_match.start() - 24) : subject_match.start()]
        if not re.search(r"\b(?:not|without|never|no)\s*$", prefix, re.IGNORECASE):
            return True
    return False


def _has_ecommerce_commercial_duty(sentence: str) -> re.Match[str] | None:
    duty_pattern = re.compile(
        r"\b(?:assortment|procurement|purchasing|sourcing|margin|merchandising|category\s+management|range\s+planning|supplier\s+(?:management|relationships?))\b",
        re.IGNORECASE,
    )
    for duty_match in duty_pattern.finditer(sentence):
        prefix = sentence[max(0, duty_match.start() - 80) : duty_match.start()]
        if not re.search(r"\b(?:not|without|never|no)\s*$", prefix, re.IGNORECASE):
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
    cues = re.compile(
        r"\b(?:required|mandatory|must\s+(?:speak|be)|fluent|native|professional(?:ly)?\s+proficien(?:cy|t)|proficiency|business[- ]level|working\s+language|language\s+of\s+work|language\s+skills|excellent\s+command|excellent\s+communication|communication|oral|written|spoken|written\s+and\s+spoken|spoken\s+and\s+written|speaker|speaking)\b",
        re.IGNORECASE,
    )
    optional = re.compile(
        r"\b(?:preferred|nice\s+to\s+have|bonus|plus|advantage|desirable|optional)\b|\bnot\s+required\b",
        re.IGNORECASE,
    )
    for sentence in sentences:
        for code, patterns in _LOCALIZED_LANGUAGE_REQUIREMENTS.items():
            if code not in allowed and any(
                re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns
            ):
                required.setdefault(code, []).append(sentence)
        for code, patterns in _LANGUAGE_CODE_REQUIREMENTS.items():
            if code not in allowed and any(
                re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns
            ):
                required.setdefault(code, []).append(sentence)
        if not cues.search(sentence):
            continue
        for name in _LANGUAGE_NAMES:
            language_match = re.search(rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE)
            if language_match and not _is_optional_language(sentence, language_match, optional):
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
    interim_fragments: list[str] = []
    short_fragments: list[str] = []
    duration_pattern = re.compile(r"\b(\d{1,3})\s*[- ]?month(?:s)?\b", re.IGNORECASE)
    for sentence in sentences:
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
