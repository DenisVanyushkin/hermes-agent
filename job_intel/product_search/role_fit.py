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
_LANGUAGE_NAMES = tuple(sorted(_LANGUAGES, key=len, reverse=True))

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
    short_contract_months: int = DEFAULT_SHORT_CONTRACT_MONTHS,
) -> RoleFitDecision:
    """Evaluate one role without network, database, clock, or model calls.

    Text fragments in every returned rule match are copied from the supplied
    fields, so callers can show the evidence that activated a rule.
    """
    if short_contract_months <= 0:
        raise ValueError("short_contract_months must be positive")

    text = " ".join(part.strip() for part in (title, company, location, description) if part).strip()
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

    matches.extend(_industry_matches(sentences))

    language_match = _language_match(sentences, owner_languages)
    if language_match is not None:
        matches.append(language_match)

    matches.extend(_urgency_matches(sentences, short_contract_months))

    hard_reject = any(match.rule_id in _HARD_REJECT_RULES for match in matches)
    if hard_reject:
        verdict: Verdict = "reject"
        explanation = "A hard exclusion rule was triggered."
    elif language_match is not None:
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
        "advertising_platform",
        "banking_software_portfolio",
        "banking_credit_p_and_l",
        "ecommerce_commercial_leadership",
        "interim_or_cover",
        "short_contract",
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


def _industry_matches(sentences: Iterable[str]) -> tuple[RuleMatch, ...]:
    sentence_list = tuple(sentences)
    matches: list[RuleMatch] = []
    for rule_id, predicate, explanation in (
        (
            "advertising_platform",
            _is_advertising_platform,
            "The text identifies an advertising/adtech platform, which is outside the target industry.",
        ),
        (
            "banking_software_portfolio",
            lambda sentence: re.search(
                r"\b(?:core\s+banking|banking\s+(?:software|platform|solutions?)|software\s+products?\s+for\s+(?:banks|banking)|banking\s+product\s+portfolio)\b",
                sentence,
                re.IGNORECASE,
            ),
            "The text identifies an industry-specific banking-software portfolio, which is outside the target industry.",
        ),
        (
            "banking_credit_p_and_l",
            lambda sentence: re.search(
                r"\b(?:bank\w*|banking)\b.*\b(?:credit|lending|loan|mortgage)\b.*(?:\bp\s*&\s*l\b|\bprofit\s+and\s+loss\b|\bp&l\b)|(?:\bp\s*&\s*l\b|\bprofit\s+and\s+loss\b|\bp&l\b).*\b(?:bank\w*|banking)\b.*\b(?:credit|lending|loan|mortgage)\b",
                sentence,
                re.IGNORECASE,
            ),
            "The text assigns P&L ownership for a banking credit/lending product, which is outside the target role fit.",
        ),
        (
            "ecommerce_commercial_leadership",
            _is_ecommerce_commercial_leadership,
            "The text identifies commercial/e-commerce or assortment leadership, which is outside the target role fit.",
        ),
    ):
        fragments = tuple(sentence for sentence in sentence_list if predicate(sentence))
        if fragments:
            matches.append(RuleMatch(rule_id, fragments, explanation, {}))
    if not any(match.rule_id == "banking_credit_p_and_l" for match in matches):
        combined = " ".join(sentence_list)
        if _has_banking_credit_p_and_l(combined):
            fragments = tuple(
                sentence
                for sentence in sentence_list
                if re.search(
                    r"\b(?:bank\w*|banking|credit|lending|loan|mortgage|p\s*&\s*l|p&l|profit\s+and\s+loss)\b",
                    sentence,
                    re.IGNORECASE,
                )
            )
            matches.append(
                RuleMatch(
                    "banking_credit_p_and_l",
                    fragments,
                    "The text assigns P&L ownership for a banking credit/lending product, which is outside the target role fit.",
                    {},
                )
            )
    if not any(match.rule_id == "ecommerce_commercial_leadership" for match in matches):
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
                    "The text identifies commercial/e-commerce leadership, which is outside the target role fit.",
                    {},
                )
            )
    return tuple(matches)


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


def _has_banking_credit_p_and_l(text: str) -> bool:
    return bool(
        re.search(r"\b(?:bank\w*|banking)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:credit|lending|loan|mortgage)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:p\s*&\s*l|p&l|profit\s+and\s+loss)\b", text, re.IGNORECASE)
    )


def _is_ecommerce_commercial_leadership(sentence: str) -> re.Match[str] | None:
    if not re.search(r"\b(?:e[- ]?commerce|ecommerce|marketplace)\b", sentence, re.IGNORECASE):
        return None
    return _has_ecommerce_commercial_duty(sentence)


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
