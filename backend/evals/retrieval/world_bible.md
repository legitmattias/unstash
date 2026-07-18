# World bible — Brf Gröndalen (fictional)

Single source of truth for the synthetic evaluation corpus. Every generated
document must be consistent with these facts. All names, companies, numbers,
and events are invented; name collisions with reality are coincidental.

Some invented values may nonetheless coincide with real ones. The
cooperative's organisation number uses a checksum-valid pattern in the real
bostadsrättsförening range; the address uses a real street name and postal
area; and some contractor names and web domains may match real registered
companies. None reference a real entity's data — the coincidences are
structural, not sourced. Treated as an accepted, recorded property of the
corpus so measured benchmark numbers stay tied to a stable artifact; if the
corpus is ever regenerated or the repository is made public, swap these for
clearly-fictional equivalents at that point.

## The cooperative

- **Brf Gröndalen**, org.nr 769600-1234 (fictional range), founded 1962.
- 48 apartments across three buildings: Gröndalsvägen 12, 14, 16 (fictional
  address, Stockholm suburb). Shared courtyard garden, laundry room in no. 14,
  garage with 20 parking spaces behind no. 16.
- Managed by **Fastighetsservice Mälardalen AB** (ekonomisk förvaltning).

## People (all fictional)

- **Karin Lindqvist** — ordförande 2019 → stämman 2023.
- **Erik Sandberg** — ledamot from 2020, ordförande from stämman 2023.
- **Maria Holm** — sekreterare (whole period).
- **Per Nyström** — kassör (whole period).
- **Anna Berg** — ledamot (whole period), driver of the EV-charging question.
- **Johan Ek** — suppleant → ledamot from 2022.
- **Sofia Dahl** — medlem, lgh 23, writes the pet motion 2022.
- **Lars Öberg** — medlem, lgh 7, laundry-booking complaints 2023.

## Companies (all fictional)

- **Norrbygg Tak & Plåt AB** — roofing contractor (wins the roof job).
- **TakExperten Stockholm AB** — competing roofer (loses on price).
- **Rörproffsen Sthlm AB** — plumbing; stambesiktning 2024.
- **GrönEl Installation AB** — electrical; EV charging offert.
- **TrädgårdsTeam Söder HB** — garden maintenance contract.
- **SBAB** and **Handelsbanken** — lenders (loan moved in 2023).

## Event timeline (the retrieval "answer keys")

### The roof saga (deliberate hard-negative cluster: many mentions, one decision)
- 2021-09: water damage in lgh 41; inspection finds worn roofing felt on no. 16.
- 2022-04: two quotes — Norrbygg **1 850 000 kr**, TakExperten **2 140 000 kr**.
- **2022-05-19 (styrelsemöte): DECISION — Norrbygg chosen for the roof
  renovation of Gröndalsvägen 16, 1 850 000 kr, work summer 2023.** § 5.
- 2023-06→08: work executed; two weeks' delay (material delivery).
- 2023-09: slutbesiktning approved; **10 års garanti** on the roof.

### Fees (avgifter)
- 2020-01: fee raised **2 %** (decided styrelsemöte 2019-11).
- 2021–2023: unchanged (explicitly noted in budget docs).
- 2024-01: fee raised **3,5 %** (decided styrelsemöte 2023-11-16, § 4) —
  motivated by interest costs and the roof loan.

### Pets (the semantic-vocabulary cluster: same topic, different words)
- Trivselregler (2020) uses **"djurhållning"**: dogs leashed in the courtyard.
- Sofia Dahl's **motion to stämman 2022** uses **"hundar"**: proposes a fenced
  dog area in the garden. Stämman **rejects** the motion (11 for, 26 against).
- Stadgar say nothing about pets (a deliberate no-answer anchor).

### Laundry room
- 2023-03: digital booking system (app) installed, replacing the padlock board.
- 2023-05: Lars Öberg's complaint about double bookings; resolved — updated
  trivselregler: max 2 pass/vecka per hushåll.

### The loan (numbers cluster)
- Until 2023: loan of **24 000 000 kr** with SBAB, ränta rörlig.
- 2023-10: refinanced to **Handelsbanken**, **4,1 %** bunden **3 år**
  (decided styrelsemöte 2023-09-21, § 6).

### EV charging (the "pending → decided" cluster)
- 2024-02: Anna Berg presents utredning; offert GrönEl **380 000 kr** for
  8 charging points; decision **postponed** (bordlagt) pending grant check.
- **2025-02-13 (styrelsemöte): DECISION — GrönEl, 380 000 kr, with
  Naturvårdsverket grant covering 50 % of installation.** § 3.

### Plumbing (future-tense distractor: investigated, NOT decided)
- 2024-09: Rörproffsen stambesiktning: pipes OK ~8–10 more years; **stambyte
  recommended around 2032**; no decision taken — monitoring only.

### Misc anchors
- OVK-besiktning 2021: anmärkningar on ventilation in no. 12; åtgärdade 2022-03.
- Garden contract with TrädgårdsTeam Söder: **band 42 000 kr/år**, renewed 2024.
- Historic: 1998 attic conversion (vindsombyggnad) created four apartments,
  41–44 (referenced in an older-style document).

## Style notes for generation

- Documents are in Swedish (a couple of member letters may mix in English).
- Protocols: formal but realistic — närvarande list, numbered §§, beslut vs
  diskussion clearly separated, signatures (ordförande + sekreterare + justerare).
- Ekonomi documents: plain summaries with figures, not full annual reports.
- The two "older-style" documents (1998, 2005) use dated officialese and no
  modern formatting.
- Every document must include its **required anchor strings verbatim** (the
  generator enforces this) so relevance judgments have stable ground truth.
