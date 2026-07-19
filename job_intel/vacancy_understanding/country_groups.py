"""Country-group resolver — a separate, versioned concern.

Contract (Step 2):
- The preference model must NOT maintain sanctions / instability truth; this
  resolver owns the mapping and is versioned independently.
- Resolution is explainable: the result names the matched key, the snapshot
  version and the source kind.
- ``sanctioned`` / ``unstable`` are NEVER inferred from free-text intuition —
  only from the curated snapshot below. An unlisted country resolves to
  ``other`` (or ``unknown`` when no country is given at all).
- Classification precedence for overlapping memberships (e.g. Sudan is both
  unstable-listed and African): sanctioned > unstable > africa > other. The
  ``africa`` mapping is geographically EXHAUSTIVE (whole continent, ISO 3166 +
  aliases) because downstream policy treats Africa as a regional criterion;
  the sanctioned/unstable snapshots remain deliberately minimal curated lists.
- The snapshot is manually curated and reviewed; authoritative future sources
  (to be wired in a later, separately approved slice): consolidated sanctions
  lists (OFAC/EU/UN) for ``sanctioned``; a human-reviewed operator list for
  ``unstable`` (no automatic feed is authoritative for "instability").

KZ note: kazakhstan is its own group because downstream policy routes it to a
separate lane; that is lane routing, not a desirability statement.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from job_intel.vacancy_understanding.model import CountryGroup

RESOLVER_VERSION = "2026.07.19.1"
SNAPSHOT_SOURCE = "curated_snapshot"

# Precedence when a country belongs to several classifications (documented
# and test-enforced): usa/kazakhstan (special policy groups, disjoint from
# the rest) > sanctioned > unstable > africa > other.
PRECEDENCE = ("usa", "kazakhstan", "sanctioned", "unstable", "africa", "other")

_USA = {"united states", "united states of america", "usa", "us"}
_KZ = {"kazakhstan"}
# Policy-status snapshots: manually curated, deliberately NOT exhaustive —
# unlisted countries are "other", never guessed into sanctioned/unstable.
_SANCTIONED = {"russia", "russian federation", "belarus", "iran", "north korea", "syria", "cuba"}
_UNSTABLE = {"afghanistan", "myanmar", "yemen", "sudan", "south sudan", "haiti", "venezuela"}
# Geographic region: EXHAUSTIVE ISO 3166 country→Africa mapping (incl. common
# aliases). Africa is a regional feasibility criterion in downstream policy,
# so this list must cover the whole continent — unlike the policy snapshots.
_AFRICA = {
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi",
    "cabo verde", "cape verde", "cameroon", "central african republic",
    "chad", "comoros", "congo", "republic of the congo",
    "democratic republic of the congo", "dr congo", "drc", "djibouti",
    "egypt", "equatorial guinea", "eritrea", "eswatini", "swaziland",
    "ethiopia", "gabon", "gambia", "the gambia", "ghana", "guinea",
    "guinea-bissau", "ivory coast", "cote d'ivoire", "côte d'ivoire",
    "kenya", "lesotho", "liberia", "libya", "madagascar", "malawi", "mali",
    "mauritania", "mauritius", "morocco", "mozambique", "namibia", "niger",
    "nigeria", "rwanda", "sao tome and principe", "são tomé and príncipe",
    "senegal", "seychelles", "sierra leone", "somalia", "south africa",
    "south sudan", "sudan", "tanzania", "togo", "tunisia", "uganda",
    "zambia", "zimbabwe",
}


class ResolvedCountryGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: CountryGroup
    matched_key: str | None
    resolver_version: str
    source: str


def resolve_country_group(country: str | None) -> ResolvedCountryGroup:
    """Resolve a country name to a policy group, explainably."""
    if not country or not country.strip():
        return ResolvedCountryGroup(
            group=CountryGroup.unknown, matched_key=None,
            resolver_version=RESOLVER_VERSION, source=SNAPSHOT_SOURCE,
        )
    key = country.strip().lower()
    for group, names in (
        (CountryGroup.usa, _USA),
        (CountryGroup.kazakhstan, _KZ),
        (CountryGroup.sanctioned, _SANCTIONED),
        (CountryGroup.unstable, _UNSTABLE),
        (CountryGroup.africa, _AFRICA),
    ):
        if key in names:
            return ResolvedCountryGroup(
                group=group, matched_key=key,
                resolver_version=RESOLVER_VERSION, source=SNAPSHOT_SOURCE,
            )
    return ResolvedCountryGroup(
        group=CountryGroup.other, matched_key=key,
        resolver_version=RESOLVER_VERSION, source=SNAPSHOT_SOURCE,
    )
