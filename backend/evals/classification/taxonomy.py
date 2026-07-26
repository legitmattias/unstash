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
    "tillsyn",
    "utredning",
    "korrespondens",
    "ansokan_arende",
    "energideklaration",
    "produktdokumentation",
    "skadeanmalan",
    "nyckelkvittens",
    "medlemsinfo",
    "stadgar_regler",
    "lagenhetsforteckning",
    "overlatelse",
    "pantsattning",
    "andrahandsuthyrning",
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
        "kalkyl",
        "underhallsplan",
        "underhållsplan",
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
    # Formal inspection by an external, often certified party: carries
    # contractual or statutory weight (warranty periods, mandatory checks).
    "besiktning": (
        "besiktning",
        "slutbesiktning",
        "garantibesiktning",
        "overlatelsebesiktning",
        "överlåtelsebesiktning",
        "markbesiktning",
        "ovk",
        "ledningsinspektion",
        "radon",
        "inspektion",
    ),
    # Internal property or apartment oversight run by the board or the
    # property manager: a separate recurring activity from besiktning, with
    # different actors, purpose and consequences.
    "tillsyn": (
        "tillsyn",
        "lagenhetstillsyn",
        "lägenhetstillsyn",
        "egenkontroll",
        "statuskontroll",
    ),
    # Commissioned investigation or technical report from an external party
    # (damp, mould, pest control, pipes, measurements). Distinct from
    # besiktning: no contractual or statutory standing, and from tillsyn:
    # performed by an outside specialist, not the board. The vertical's own
    # folder naming ("Avtal, Utredningar, Anbud") treats it as a peer of
    # contracts and tenders.
    "utredning": (
        "utredning",
        "provtagning",
        "matning",
        "mätning",
        "analys",
        "sanering",
        "mogel",
        "mögel",
        "fuktutredning",
        "skadedjur",
        "statusrapport",
    ),
    # Two-way communication with an individual or organisation, as opposed
    # to medlemsinfo, which is outbound broadcast to all members.
    "korrespondens": (
        "brev",
        "skrivelse",
        "e-post",
        "epost",
        "meddelande",
        "svar till",
        "korrespondens",
    ),
    # A member (or the association) applies, and the board decides:
    # renovations, glazing, accessibility adaptations, permissions.
    "ansokan_arende": (
        "ansokan",
        "ansökan",
        "begaran",
        "begäran",
        "medgivande",
        "godkannande",
        "godkännande",
        "tillbyggnadshandling",
        "ombyggnation",
        "bygglov",
    ),
    "energideklaration": (
        "energideklaration",
        "energidekl",
    ),
    # Manufacturer and supplier literature received with an installation:
    # manuals, maintenance instructions, datasheets, product declarations.
    "produktdokumentation": (
        "bruksanvisning",
        "skotselanvisning",
        "skötselanvisning",
        "skotsel",
        "skötsel",
        "monteringsanvisning",
        "driftinstruktion",
        "anvisning",
        "produktblad",
        "datablad",
        "broschyr",
        "byggvarudeklaration",
    ),
    # Reporting damage, loss or an incident to an insurer or authority.
    # The incident itself is an event facet, not a type: a burglary produces
    # a police report, a claim, repair offers and photographs.
    "skadeanmalan": (
        "skadeanmalan",
        "skadeanmälan",
        "polisanmalan",
        "polisanmälan",
        "forsakringsarende",
        "försäkringsärende",
        "skadereglering",
        "inbrott",
        "vattenskada",
    ),
    "nyckelkvittens": (
        "nyckelkvittens",
        "nyckelkvitto",
        "nyckelkvitt",
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
    # The apartment-related types below were briefly collapsed into one
    # "lägenhetsdokument" bucket. That was a subject grouping, not a type:
    # the sector's retention guidance assigns each of these a different
    # retention rule, which is only possible if they are distinct types.
    "lagenhetsforteckning": (
        "lagenhetsforteckning",
        "lägenhetsförteckning",
        "lagenhetsdokument",
        "lägenhetsdokument",
        "medlemsforteckning",
        "medlemsförteckning",
    ),
    "overlatelse": (
        "overlatelse",
        "överlåtelse",
        "upplatelse",
        "upplåtelse",
        "maklarblankett",
        "mäklarblankett",
    ),
    "pantsattning": (
        "pantsattning",
        "pantsättning",
        "pantforskrivning",
        "pantförskrivning",
    ),
    "andrahandsuthyrning": (
        "andrahandsuthyrning",
        "andrahand",
        "uthyrning",
    ),
}

# Terms that legitimately signal several types; they raise every candidate
# rather than picking one, so the margin decides.
AMBIGUOUS_TERMS: dict[str, tuple[str, ...]] = {
    "protokoll": ("protokoll_styrelse", "protokoll_stamma", "besiktning"),
    "kallelse": ("protokoll_stamma", "protokoll_styrelse"),
    "bilaga": ("protokoll_styrelse", "protokoll_stamma"),
    "rapport": ("ekonomi_ovrigt", "besiktning", "tillsyn", "utredning"),
    "intyg": ("korrespondens", "besiktning"),
    "utlatande": ("besiktning", "tillsyn"),
    "utlåtande": ("besiktning", "tillsyn"),
    "kontroll": ("besiktning", "tillsyn"),
    "plan": ("ritning", "ekonomi_ovrigt"),
    "hus": ("ritning",),
}
