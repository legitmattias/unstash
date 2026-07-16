"""Generate the synthetic evaluation corpus from the world bible.

Every document is fully fictional (see world_bible.md). Each spec lists
anchor strings that must appear verbatim in the generated document —
they are the ground truth that golden queries are judged against. A
document whose anchors are missing is retried once, then reported.

Usage:
    MISTRAL_API_KEY=... python generate_corpus.py [--force]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
CORPUS = HERE / "corpus"
MODEL = "mistral-small-latest"
URL = "https://api.mistral.ai/v1/chat/completions"


def spec(
    filename: str, doc_type: str, date: str, brief: str, anchors: list[str], style: str = ""
) -> dict:
    return {
        "filename": filename,
        "doc_type": doc_type,
        "date": date,
        "brief": brief,
        "anchors": anchors,
        "style": style,
    }


SPECS: list[dict] = [
    # --- styrelseprotokoll ---------------------------------------------------
    spec(
        "protokoll-styrelse-2019-11.md",
        "styrelseprotokoll",
        "2019-11-14",
        "Ordinary board meeting. Main item: budget 2020 and the DECISION to raise "
        "the monthly fee by 2 % from 2020-01-01. Also: garden contract status, winter checklist.",
        ["2 %", "Karin Lindqvist", "Per Nyström"],
    ),
    spec(
        "protokoll-styrelse-2020-03.md",
        "styrelseprotokoll",
        "2020-03-12",
        "Ordinary meeting. Spring maintenance round, pandemic adjustments for the laundry "
        "room, discussion (no decision) about courtyard lighting.",
        ["Maria Holm", "Gröndalsvägen"],
    ),
    spec(
        "protokoll-styrelse-2020-09.md",
        "styrelseprotokoll",
        "2020-09-17",
        "Ordinary meeting. DECISION: adopt updated trivselregler including rules on "
        "djurhållning (dogs leashed in the courtyard). Autumn garden day planned.",
        ["trivselregler", "djurhållning", "kopplade"],
    ),
    spec(
        "protokoll-styrelse-2021-05.md",
        "styrelseprotokoll",
        "2021-05-20",
        "Ordinary meeting. OVK inspection results: remarks on ventilation in "
        "Gröndalsvägen 12, action plan agreed. Garden day summary.",
        ["OVK", "Gröndalsvägen 12", "ventilation"],
    ),
    spec(
        "protokoll-styrelse-2021-09.md",
        "styrelseprotokoll",
        "2021-09-16",
        "Ordinary meeting. Water damage in apartment 41 traced to worn roofing felt on "
        "Gröndalsvägen 16; DECISION to commission a full roof inspection. Insurance case opened.",
        ["lägenhet 41", "Gröndalsvägen 16", "takbesiktning"],
    ),
    spec(
        "protokoll-styrelse-2022-02.md",
        "styrelseprotokoll",
        "2022-02-10",
        "Ordinary meeting. Roof inspection report received: renovation needed within two "
        "years. DECISION to request quotes from at least two roofing contractors. OVK remarks від 2021 under åtgärd.",
        ["takrenovering", "offerter"],
    ),
    spec(
        "protokoll-styrelse-2022-04.md",
        "styrelseprotokoll",
        "2022-04-21",
        "Ordinary meeting. Two roofing quotes presented and DISCUSSED (no decision yet): "
        "Norrbygg Tak & Plåt AB at 1 850 000 kr and TakExperten Stockholm AB at 2 140 000 kr. "
        "References to be checked before decision.",
        ["Norrbygg Tak & Plåt AB", "1 850 000 kr", "TakExperten Stockholm AB", "2 140 000 kr"],
    ),
    spec(
        "protokoll-styrelse-2022-05.md",
        "styrelseprotokoll",
        "2022-05-19",
        "Extra board meeting. § 5: DECISION — Norrbygg Tak & Plåt AB is chosen for the roof "
        "renovation of Gröndalsvägen 16 at 1 850 000 kr, work scheduled summer 2023, financed "
        "partly by loan. This is THE roof decision document.",
        ["§ 5", "Norrbygg Tak & Plåt AB", "1 850 000 kr", "beslutade"],
    ),
    spec(
        "protokoll-styrelse-2022-11.md",
        "styrelseprotokoll",
        "2022-11-17",
        "Ordinary meeting. Budget 2023: fee UNCHANGED. Planning for the 2023 roof works "
        "communication to members. Christmas gathering.",
        ["oförändrad", "2023"],
    ),
    spec(
        "protokoll-styrelse-2023-03.md",
        "styrelseprotokoll",
        "2023-03-16",
        "Ordinary meeting. DECISION: install a digital booking system (app) for the laundry "
        "room, replacing the padlock board. Roof works start date confirmed to June.",
        ["tvättstugan", "bokningssystem", "app"],
    ),
    spec(
        "protokoll-styrelse-2023-05.md",
        "styrelseprotokoll",
        "2023-05-25",
        "Ordinary meeting. Member complaint from Lars Öberg about double bookings in the new "
        "laundry app handled; DECISION: update trivselregler to max 2 pass per week per household.",
        ["Lars Öberg", "max 2 pass", "tvättstugan"],
    ),
    spec(
        "protokoll-styrelse-2023-09.md",
        "styrelseprotokoll",
        "2023-09-21",
        "Ordinary meeting. § 6: DECISION — refinance the association loan of 24 000 000 kr, "
        "moving from SBAB to Handelsbanken at 4,1 % fixed for 3 years. Roof slutbesiktning approved.",
        ["§ 6", "24 000 000 kr", "Handelsbanken", "4,1 %", "slutbesiktning"],
    ),
    spec(
        "protokoll-styrelse-2023-11.md",
        "styrelseprotokoll",
        "2023-11-16",
        "Ordinary meeting. § 4: DECISION — raise the monthly fee by 3,5 % from 2024-01-01, "
        "motivated by interest costs and the roof loan. Budget 2024 adopted.",
        ["§ 4", "3,5 %", "avgiften"],
    ),
    spec(
        "protokoll-styrelse-2024-02.md",
        "styrelseprotokoll",
        "2024-02-15",
        "Ordinary meeting. Anna Berg presents the EV-charging investigation; quote from "
        "GrönEl Installation AB at 380 000 kr for 8 charging points. DECISION POSTPONED "
        "(bordlagt) pending grant application check. No approval here.",
        ["Anna Berg", "GrönEl Installation AB", "380 000 kr", "bordlades"],
    ),
    spec(
        "protokoll-styrelse-2024-05.md",
        "styrelseprotokoll",
        "2024-05-23",
        "Ordinary meeting. Garden contract with TrädgårdsTeam Söder HB renewed at "
        "42 000 kr per year. Spring inspection round notes.",
        ["TrädgårdsTeam Söder HB", "42 000 kr"],
    ),
    spec(
        "protokoll-styrelse-2024-09.md",
        "styrelseprotokoll",
        "2024-09-19",
        "Ordinary meeting. Rörproffsen Sthlm AB stambesiktning results: pipes OK for roughly "
        "8-10 more years, stambyte recommended around 2032. NO decision — monitoring only.",
        ["Rörproffsen Sthlm AB", "stambyte", "2032"],
    ),
    spec(
        "protokoll-styrelse-2024-11.md",
        "styrelseprotokoll",
        "2024-11-21",
        "Ordinary meeting. Budget 2025: fee unchanged. Grant approved for EV charging "
        "(Naturvårdsverket, 50 %); final decision moved to February meeting.",
        ["Naturvårdsverket", "50 %"],
    ),
    spec(
        "protokoll-styrelse-2025-02.md",
        "styrelseprotokoll",
        "2025-02-13",
        "Ordinary meeting. § 3: DECISION — GrönEl Installation AB installs 8 charging points "
        "for 380 000 kr, with the Naturvårdsverket grant covering 50 % of installation. "
        "This is THE charging decision document.",
        ["§ 3", "GrönEl Installation AB", "380 000 kr", "beslutade"],
    ),
    # --- stämmoprotokoll -----------------------------------------------------
    spec(
        "protokoll-stamma-2020.md",
        "stämmoprotokoll",
        "2020-06-11",
        "Annual general meeting (short, pandemic format). Standard items; board re-elected; "
        "Karin Lindqvist re-elected ordförande.",
        ["Karin Lindqvist", "ansvarsfrihet"],
    ),
    spec(
        "protokoll-stamma-2021.md",
        "stämmoprotokoll",
        "2021-06-10",
        "Annual general meeting. Standard items; information about the upcoming roof "
        "inspection; no motions.",
        ["ansvarsfrihet", "tak"],
    ),
    spec(
        "protokoll-stamma-2022.md",
        "stämmoprotokoll",
        "2022-06-09",
        "Annual general meeting. Sofia Dahl's motion about a fenced dog area (hundrastgård) "
        "in the garden is REJECTED after vote: 11 for, 26 against. Roof renovation info point.",
        ["Sofia Dahl", "motion", "hundar", "11", "26", "avslogs"],
    ),
    spec(
        "protokoll-stamma-2023.md",
        "stämmoprotokoll",
        "2023-06-08",
        "Annual general meeting. Karin Lindqvist steps down; Erik Sandberg elected new "
        "ordförande. Roof works in progress reported.",
        ["Erik Sandberg", "ordförande", "Karin Lindqvist"],
    ),
    spec(
        "protokoll-stamma-2024.md",
        "stämmoprotokoll",
        "2024-06-13",
        "Annual general meeting. Fee increase 3,5 % noted; loan refinancing reported; "
        "question from the floor about EV charging answered (under investigation).",
        ["3,5 %", "Handelsbanken", "laddstolpar"],
    ),
    spec(
        "protokoll-stamma-2025.md",
        "stämmoprotokoll",
        "2025-06-12",
        "Annual general meeting. EV charging installation reported complete; garden day "
        "thanks; board re-elected.",
        ["laddstolpar", "Erik Sandberg"],
    ),
    # --- ekonomi -------------------------------------------------------------
    spec(
        "budget-2021.md",
        "ekonomi",
        "2020-12-01",
        "Budget summary for 2021. Fee explicitly UNCHANGED. Maintenance reserve building up "
        "ahead of possible roof works. Key figures table.",
        ["oförändrad", "underhåll"],
    ),
    spec(
        "budget-2022.md",
        "ekonomi",
        "2021-12-01",
        "Budget summary for 2022. Fee unchanged; provision for roof inspection and quotes; "
        "note on rising interest environment.",
        ["oförändrad", "tak"],
    ),
    spec(
        "budget-2023.md",
        "ekonomi",
        "2022-12-01",
        "Budget summary for 2023. Fee unchanged; roof renovation 1 850 000 kr financed by "
        "loan + reserves; interest cost projection.",
        ["1 850 000 kr", "oförändrad"],
    ),
    spec(
        "ekonomisk-rapport-2023-h2.md",
        "ekonomi",
        "2023-12-15",
        "Half-year economic report H2 2023: roof project closed on budget with two weeks' "
        "delay; loan refinanced to Handelsbanken at 4,1 %; liquidity good.",
        ["Handelsbanken", "4,1 %", "takprojektet"],
    ),
    spec(
        "budget-2024.md",
        "ekonomi",
        "2023-12-01",
        "Budget summary for 2024. Fee raised 3,5 % from January; interest costs dominate; "
        "EV charging investigation funded.",
        ["3,5 %", "räntekostnader"],
    ),
    spec(
        "arsredovisning-2023-sammanfattning.md",
        "ekonomi",
        "2024-04-15",
        "Plain-language summary of the 2023 annual report: roof renovation completed "
        "(1 850 000 kr), loan 24 000 000 kr moved to Handelsbanken, result slightly negative, "
        "planned and covered by reserves.",
        ["1 850 000 kr", "24 000 000 kr", "Handelsbanken"],
    ),
    # --- avtal & offerter ----------------------------------------------------
    spec(
        "offert-norrbygg-2022.md",
        "offert",
        "2022-03-28",
        "Quote from Norrbygg Tak & Plåt AB for full roof renovation of Gröndalsvägen 16: "
        "1 850 000 kr inkl. moms, itemised scope (tegel, underlagspapp, plåtarbeten), "
        "10 års garanti offered, valid 90 days.",
        ["Norrbygg Tak & Plåt AB", "1 850 000 kr", "10 års garanti"],
    ),
    spec(
        "offert-takexperten-2022.md",
        "offert",
        "2022-04-02",
        "Quote from TakExperten Stockholm AB for the same roof scope: 2 140 000 kr inkl. "
        "moms, 8 års garanti, longer lead time.",
        ["TakExperten Stockholm AB", "2 140 000 kr"],
    ),
    spec(
        "avtal-norrbygg-2022.md",
        "avtal",
        "2022-06-15",
        "Contract between Brf Gröndalen and Norrbygg Tak & Plåt AB per the accepted quote: "
        "1 850 000 kr, execution June-August 2023, ABT 06 referenced, payment plan in three steps.",
        ["Norrbygg Tak & Plåt AB", "1 850 000 kr", "ABT 06"],
    ),
    spec(
        "garantibevis-tak-2023.md",
        "avtal",
        "2023-09-15",
        "Guarantee certificate from Norrbygg after approved slutbesiktning: 10 years on "
        "roofing work for Gröndalsvägen 16, from 2023-09-08.",
        ["10 år", "slutbesiktning", "Gröndalsvägen 16"],
    ),
    spec(
        "offert-gronel-2024.md",
        "offert",
        "2024-01-20",
        "Quote from GrönEl Installation AB: 8 EV charging points in the garage, 380 000 kr "
        "inkl. moms, load balancing included, prepared for 8 more.",
        ["GrönEl Installation AB", "380 000 kr", "laddpunkter"],
    ),
    spec(
        "avtal-tradgardsteam-2024.md",
        "avtal",
        "2024-04-01",
        "Renewed garden maintenance contract with TrädgårdsTeam Söder HB: 42 000 kr/år, "
        "weekly rounds April-October, snow shovelling excluded.",
        ["TrädgårdsTeam Söder HB", "42 000 kr"],
    ),
    # --- info till medlemmar & korrespondens ---------------------------------
    spec(
        "info-takrenovering-2023.md",
        "medlemsinfo",
        "2023-05-30",
        "Letter to members about the roof works: schedule June-August, scaffolding, balcony "
        "access restrictions, contact person at Norrbygg. Mentions the two weeks' delay in a "
        "follow-up postscript dated August.",
        ["Norrbygg", "ställning", "två veckor"],
    ),
    spec(
        "info-tvattstuga-app-2023.md",
        "medlemsinfo",
        "2023-04-05",
        "Letter to members introducing the laundry booking app: how to install, how to book, "
        "old padlock board retired end of April.",
        ["tvättstugan", "appen", "boka"],
    ),
    spec(
        "motion-hundrastgard-2022.md",
        "motion",
        "2022-04-30",
        "Sofia Dahl's motion to the 2022 AGM: proposes a fenced dog area (hundrastgård) in "
        "the courtyard garden, arguments about safety for both dogs and children. Uses the "
        "words hundar/hundrastgård (not djurhållning).",
        ["Sofia Dahl", "hundrastgård", "hundar", "lägenhet 23"],
    ),
    spec(
        "kallelse-stamma-2024.md",
        "kallelse",
        "2024-05-20",
        "Notice + agenda for the 2024 AGM: standard items, report on fee increase and loan, "
        "info point on EV charging investigation.",
        ["Kallelse", "föreningsstämma", "Dagordning"],
    ),
    spec(
        "info-laddstolpar-2025.md",
        "medlemsinfo",
        "2025-03-01",
        "Letter to members: EV charging decided — GrönEl installs 8 points in the garage "
        "during April, grant covers half the installation, how to request a charging space, "
        "monthly cost model.",
        ["GrönEl", "laddpunkter", "garaget"],
    ),
    # --- regler & stadgar ----------------------------------------------------
    spec(
        "trivselregler-2020.md",
        "regler",
        "2020-09-17",
        "House rules adopted 2020. Sections on noise hours, laundry room, garbage, balconies, "
        "and djurhållning: dogs must be leashed (kopplade) in the courtyard; owners clean up. "
        "Does NOT use the word hundrastgård.",
        ["djurhållning", "kopplade", "trivselregler"],
    ),
    spec(
        "trivselregler-2023.md",
        "regler",
        "2023-06-01",
        "Revised house rules 2023: same structure as 2020 plus the laundry rule max 2 pass "
        "per week per household via the app; djurhållning section unchanged.",
        ["max 2 pass", "djurhållning", "appen"],
    ),
    spec(
        "stadgar-utdrag.md",
        "stadgar",
        "2019-01-01",
        "Excerpt of the association statutes: purpose, membership, fees (styrelsen sets "
        "avgifter), maintenance responsibility split (inre/yttre underhåll). Deliberately "
        "contains NOTHING about pets or animals.",
        ["stadgar", "underhåll", "avgift"],
    ),
    # --- older-style documents ----------------------------------------------
    spec(
        "beslut-vindsombyggnad-1998.md",
        "styrelseprotokoll",
        "1998-10-08",
        "Older-style board protocol (dated officialese, no modern formatting): decision to "
        "convert the attic of Gröndalsvägen 14 into four new apartments, numbered 41-44, "
        "with byggnadslov reference. Style: 1990s formal Swedish.",
        ["vindsombyggnad", "fyra", "byggnadslov"],
        style="1990s Swedish officialese, typewriter-plain, long sentences",
    ),
    spec(
        "protokoll-garage-2005.md",
        "styrelseprotokoll",
        "2005-04-14",
        "Older-style board protocol: renovation of the garage floor behind Gröndalsvägen 16, "
        "contractor Betong & Golv i Sthlm AB (fictional), 145 000 kr. Style: mid-2000s plain.",
        ["garaget", "145 000 kr"],
        style="mid-2000s plain formal Swedish",
    ),
]


PROMPT_TEMPLATE = """You are generating a fully FICTIONAL Swedish housing-cooperative document
for a search-evaluation corpus. Everything is invented; stay strictly consistent
with the world bible below. Output ONLY the document text in markdown (no
preamble, no code fences).

WORLD BIBLE:
{bible}

DOCUMENT TO WRITE:
- Type: {doc_type}
- Date: {date}
- Content brief: {brief}
- Style: {style}
- Length: roughly 300-600 words (protocols/rules), 150-350 words (letters,
  quotes, summaries). Natural, realistic Swedish for the genre.

HARD REQUIREMENTS:
- Include each of these strings EXACTLY VERBATIM somewhere natural in the text:
{anchor_list}
- Do not contradict the world bible. Do not invent new major events, people, or
  companies beyond incidental flavour.
- Decisions must read as decisions only where the brief says DECISION; briefs
  saying discussed/postponed/rejected must NOT read as approvals.
"""


def generate(client: httpx.Client, bible: str, s: dict) -> str:
    prompt = PROMPT_TEMPLATE.format(
        bible=bible,
        doc_type=s["doc_type"],
        date=s["date"],
        brief=s["brief"],
        style=s["style"] or "modern, realistic for the genre",
        anchor_list="\n".join(f'  - "{a}"' for a in s["anchors"]),
    )
    r = client.post(
        URL,
        json={
            "model": MODEL,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def missing_anchors(text: str, s: dict) -> list[str]:
    return [a for a in s["anchors"] if a not in text]


def main() -> int:
    force = "--force" in sys.argv
    bible = (HERE / "world_bible.md").read_text()
    CORPUS.mkdir(exist_ok=True)
    failures: list[str] = []

    with httpx.Client() as client:
        for s in SPECS:
            out = CORPUS / s["filename"]
            if out.exists() and not force:
                print(f"skip   {s['filename']} (exists)")
                continue
            text = generate(client, bible, s)
            missing = missing_anchors(text, s)
            if missing:
                time.sleep(1)
                text = generate(client, bible, s)  # one retry
                missing = missing_anchors(text, s)
            header = (
                f"<!-- synthetic eval document | type: {s['doc_type']} | date: {s['date']} -->\n\n"
            )
            out.write_text(header + text + "\n")
            if missing:
                failures.append(f"{s['filename']}: missing {missing}")
                print(f"ANCHOR MISS {s['filename']}: {missing}")
            else:
                print(f"ok     {s['filename']}")
            time.sleep(0.4)

    if failures:
        print(f"\n{len(failures)} document(s) need manual curation:")
        for f in failures:
            print(" ", f)
        return 1
    print(f"\nall {len(SPECS)} documents generated with anchors verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
