"""Starter document-type taxonomy and lexicon for the housing-cooperative vertical.

The taxonomy is authored, not discovered (ADR 0014). Types are drawn from
the sector's own retention guidance, which defines them as the recurring
outputs of recurring processes rather than as subject areas.

The lexicon maps surface terms to types. Terms are matched against a
normalised filename and against path segments; a term appearing in the
filename is stronger evidence than the same term in a distant ancestor
folder. Multi-word terms are matched as phrases.

``AMBIGUOUS_TERMS`` names terms that legitimately belong to several types
(a bare "protokoll" may be a board minute, a general-meeting minute, or an
inspection record). They contribute evidence to every candidate type, so
the scorer's margin — not just its top score — decides whether to abstain.
"""

from __future__ import annotations

# Label set. Two adjudication escapes are deliberate: real archives contain
# genuinely ambiguous and genuinely multi-type documents, and counting those
# as classifier failures would be measuring the wrong thing.
TYPES: tuple[str, ...] = (
    "protokoll_styrelse",
    "protokoll_stamma",
    "arsredovisning",
    "ekonomi_ovrigt",
    "avtal",
    "offert",
    "faktura",
    "ritning",
    "besiktning",
    "energideklaration",
    "medlemsinfo",
    "stadgar_regler",
    "lagenhetsdokument",
    "ovrigt",
)

ADJUDICATION_ESCAPES: tuple[str, ...] = ("osaker", "flera")

LEXICON: dict[str, tuple[str, ...]] = {
    "protokoll_styrelse": (
        "styrelseprotokoll",
        "styrelsemote",
        "styrelsemöte",
        "konstituerande",
        "dagordning",
        "kallelse styrelse",
    ),
    "protokoll_stamma": (
        "stammoprotokoll",
        "stämmoprotokoll",
        "foreningsstamma",
        "föreningsstämma",
        "arsstamma",
        "årsstämma",
        "extra stamma",
        "stamma",
        "stämma",
        "motion",
        "rostlangd",
        "röstlängd",
    ),
    "arsredovisning": (
        "arsredovisning",
        "årsredovisning",
        "bokslut",
        "balansrakning",
        "balansräkning",
        "resultatrakning",
        "resultaträkning",
        "revisionsberattelse",
        "revisionsberättelse",
    ),
    "ekonomi_ovrigt": (
        "budget",
        "ekonomisk rapport",
        "forvaltningsrapport",
        "förvaltningsrapport",
        "manadsrapport",
        "månadsrapport",
        "likviditet",
        "kassabok",
        "avgiftshojning",
        "avgiftshöjning",
    ),
    "avtal": (
        "avtal",
        "kontrakt",
        "uppsagning",
        "uppsägning",
        "overenskommelse",
        "överenskommelse",
        "garantibevis",
    ),
    "offert": (
        "offert",
        "anbud",
        "forfragningsunderlag",
        "förfrågningsunderlag",
        "prisforslag",
        "prisförslag",
        "kostnadsforslag",
    ),
    "faktura": (
        "faktura",
        "rakning",
        "räkning",
        "kvitto",
        "betalning",
        "kreditering",
    ),
    "ritning": (
        "ritning",
        "fasad",
        "sektion",
        "situationsplan",
        "bottenplan",
        "overvaning",
        "övervåning",
        "planritning",
        "konstruktionsritning",
        "relationshandling",
        "anlaggning",
        "anläggning",
        "rorgrav",
        "rörgrav",
    ),
    "besiktning": (
        "besiktning",
        "slutbesiktning",
        "garantibesiktning",
        "ovk",
        "tillsyn",
        "utlatande",
        "utlåtande",
        "inspektion",
    ),
    "energideklaration": (
        "energideklaration",
        "energidekl",
    ),
    "medlemsinfo": (
        "medlemsbrev",
        "infoblad",
        "informationsbrev",
        "medlemsinformation",
        "portanslag",
        "nyhetsbrev",
    ),
    "stadgar_regler": (
        "stadgar",
        "trivselregler",
        "ordningsregler",
        "policy",
        "riktlinjer",
    ),
    "lagenhetsdokument": (
        "lagenhetsdokument",
        "lägenhetsdokument",
        "lagenhetsforteckning",
        "lägenhetsförteckning",
        "andrahandsuthyrning",
        "overlatelse",
        "överlåtelse",
        "pantsattning",
        "pantsättning",
        "nyckelkvittens",
    ),
}

# Terms that legitimately signal several types; they raise every candidate
# rather than picking one, so the margin decides.
AMBIGUOUS_TERMS: dict[str, tuple[str, ...]] = {
    "protokoll": ("protokoll_styrelse", "protokoll_stamma", "besiktning"),
    "kallelse": ("protokoll_stamma", "protokoll_styrelse"),
    "bilaga": ("protokoll_styrelse", "protokoll_stamma"),
    "rapport": ("ekonomi_ovrigt", "besiktning"),
    "plan": ("ritning", "ekonomi_ovrigt"),
    "hus": ("ritning",),
}
