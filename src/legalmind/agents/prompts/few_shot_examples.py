"""Few-shot examples for LegalMind AI agents.

Each constant provides a list of (user_message, assistant_response) tuples that
demonstrate expected output quality and domain expertise for the respective agent.
These are injected into conversation history to improve response quality.
"""

from __future__ import annotations

from typing import TypeAlias

FewShotExample: TypeAlias = dict[str, str]  # {"role": ..., "content": ...}

# ──────────────────────────────────────────────────────────────────────────────
# Document Analysis Agent — Few-shot examples
# ──────────────────────────────────────────────────────────────────────────────

DOCUMENT_FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    {
        "role": "user",
        "content": ("Kun je dit huurcontract samenvatten en de belangrijkste clausules eruit halen?"),
    },
    {
        "role": "assistant",
        "content": """\
## Samenvatting Huurovereenkomst

**Documenttype**: Huurovereenkomst woonruimte (model ROZ 2017)

**Partijen**:
- Verhuurder: Van der Berg Vastgoed B.V., KvK 12345678
- Huurder: J.A. de Vries

**Kerngegevens**:
- Gehuurde: Appartement aan de Keizersgracht 123-II, 1015 CE Amsterdam
- Ingangsdatum: 1 maart 2025
- Looptijd: 12 maanden, daarna onbepaalde tijd
- Huurprijs: € 1.450,00 per maand (exclusief servicekosten van € 150,00)

**Belangrijkste clausules**:

1. **Huurtermijn en opzegging** (Art. 3): Minimale looptijd van 12 maanden. Opzegtermijn \
van 1 maand voor huurder, 3 maanden voor verhuurder conform art. 7:271 BW.

2. **Huurprijsaanpassing** (Art. 5): Jaarlijkse indexering op basis van CPI, per 1 juli. \
Maximaal het wettelijk toegestane percentage.

3. **Onderhoudsverdeling** (Art. 9): Klein onderhoud voor rekening huurder conform \
Besluit kleine herstellingen. Groot onderhoud voor verhuurder.

4. **Huisdierenclausule** (Art. 12, bijzondere bepaling): Huisdieren niet toegestaan \
zonder schriftelijke toestemming verhuurder.

**Aandachtspunten**:
- ⚠ Art. 14 bevat een boeteclausule van € 250,00 per dag bij ongeoorloofde onderverhuur. \
Dit is relatief hoog maar gangbaar.
- ⚠ De waarborgsom van 3 maanden huur (€ 4.350,00) ligt boven het gebruikelijke maximum \
van 2 maanden. Dit kan juridisch aanvechtbaar zijn.
- Er ontbreekt een expliciete regeling over de staat van oplevering bij einde huur.\
""",
    },
    {
        "role": "user",
        "content": "Vergelijk deze twee versies van het contract en geef de verschillen aan.",
    },
    {
        "role": "assistant",
        "content": """\
## Documentvergelijking

**Document A**: Huurovereenkomst v1.0 (getekend 15 januari 2025)
**Document B**: Huurovereenkomst v2.0 (concept 10 februari 2025)

### Gewijzigde clausules

| # | Clausule | Versie A | Versie B | Impact |
|---|----------|----------|----------|--------|
| 1 | Art. 5 — Huurprijs | € 1.450,00/mnd | € 1.525,00/mnd | Verhoging van 5,2% |
| 2 | Art. 3.2 — Opzegtermijn huurder | 1 maand | 2 maanden | Langere opzegtermijn |
| 3 | Art. 9.3 — Onderhoudsdrempel | € 250,00 | € 500,00 | Hogere kosten voor huurder |
| 4 | Art. 14 — Boetebeding | € 250,00/dag | € 500,00/dag | Verdubbeling boete |

### Toegevoegde clausules in versie B
- **Art. 15 (nieuw)**: Energielabel-verklaring — verhuurder verklaart minimaal label C.
- **Art. 16 (nieuw)**: Geschillenregeling — verwijzing naar Huurcommissie.

### Verwijderde clausules
- **Art. 12 — Huisdierenclausule**: Volledig verwijderd in versie B.

### Risicobeoordeling
De opzegtermijn van 2 maanden voor de huurder (Art. 3.2 v2) wijkt af van de wettelijke \
minimum van 1 maand (art. 7:271 lid 5 BW). Dit beding is **vernietigbaar** op verzoek \
van de huurder. Ik adviseer dit te corrigeren naar 1 maand.\
""",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Legal Research Agent — Few-shot examples
# ──────────────────────────────────────────────────────────────────────────────

RESEARCH_FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    {
        "role": "user",
        "content": (
            "Mijn cliënt is ontslagen na 8 jaar dienstverband. De werkgever zegt dat het "
            "bedrijfseconomische redenen zijn. Wat zijn de regels hiervoor?"
        ),
    },
    {
        "role": "assistant",
        "content": """\
## Juridisch Kader: Ontslag wegens Bedrijfseconomische Redenen

### Toepasselijke wetgeving

**Art. 7:669 lid 3 sub a BW** — Ontslag wegens bedrijfseconomische omstandigheden is \
een redelijke grond voor opzegging, mits de werkgever aantoont dat:
1. Er structureel arbeidsplaatsen vervallen
2. Herplaatsing binnen een redelijke termijn niet mogelijk is (art. 7:669 lid 1 BW)

**Ontslagregeling (Regeling UWV)** — De werkgever moet een ontslagvergunning aanvragen \
bij het UWV en het afspiegelingsbeginsel toepassen.

### Relevante jurisprudentie

1. **ECLI:NL:HR:2019:1734** (Hoge Raad, 8 november 2019)
   - De werkgever draagt de bewijslast voor bedrijfseconomische noodzaak.
   - Financiële stukken moeten de noodzaak van het verval van arbeidsplaatsen onderbouwen.

2. **ECLI:NL:GHARL:2023:4521** (Gerechtshof Arnhem-Leeuwarden, 12 juni 2023)
   - Het afspiegelingsbeginsel moet correct worden toegepast binnen uitwisselbare functies.
   - Onjuiste toepassing leidt tot vernietiging van het ontslag.

3. **ECLI:NL:RBAMS:2022:7890** (Rechtbank Amsterdam, 3 oktober 2022)
   - Herplaatsingsinspanning moet aantoonbaar en serieus zijn.
   - Alleen wijzen op het ontbreken van vacatures is onvoldoende.

### Rechten van uw cliënt (na 8 jaar dienstverband)

| Onderdeel | Berekening |
|-----------|------------|
| **Transitievergoeding** | 1/3 maandsalaris per dienstjaar = ca. 2,67 maandsalarissen (art. 7:673 BW) |
| **Opzegtermijn werkgever** | 3 maanden (art. 7:672 lid 2 sub d BW, 5-10 jaar dienstverband) |
| **Herplaatsingstermijn** | Redelijke termijn, in de regel gelijk aan de opzegtermijn |

### Praktische implicaties
- Controleer of UWV-toestemming is verkregen.
- Controleer of het afspiegelingsbeginsel correct is toegepast.
- Controleer of herplaatsingsmogelijkheden serieus zijn onderzocht.
- Bij gebreken in de procedure kan het ontslag worden aangevochten via de kantonrechter \
  (art. 7:681 BW), binnen 2 maanden na einde arbeidsovereenkomst.

### Caveat
De transitievergoeding is een minimumrecht. Bij ernstig verwijtbaar handelen van de \
werkgever kan de rechter een billijke vergoeding toekennen bovenop de transitievergoeding \
(art. 7:671b lid 9 sub c BW).\
""",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Drafting Agent — Few-shot examples
# ──────────────────────────────────────────────────────────────────────────────

DRAFTING_FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    {
        "role": "user",
        "content": (
            "Schrijf een sommatiebrief namens mijn cliënt aan de verhuurder die het "
            "borg niet terugbetaalt. Het borg is €2.400 en het huurcontract is 3 maanden "
            "geleden beëindigd."
        ),
    },
    {
        "role": "assistant",
        "content": """\
## Concept Sommatiebrief — Terugbetaling waarborgsom

> ⚠ Dit is een concept dat professionele beoordeling vereist vóór verzending.

---

[NAAM CLIËNT]
[ADRES CLIËNT]
[POSTCODE EN PLAATS]

[NAAM VERHUURDER]
[ADRES VERHUURDER]
[POSTCODE EN PLAATS]

[PLAATS], [DATUM]

**Per aangetekende post**

Betreft: Sommatie terugbetaling waarborgsom
Uw referentie: [REFERENTIE HUURCONTRACT]

Geachte heer/mevrouw [NAAM VERHUURDER],

**1.** Namens mijn cliënt, [NAAM CLIËNT], richt ik mij tot u inzake de terugbetaling \
van de waarborgsom ten bedrage van **€ 2.400,00** (zegge: tweeduizend vierhonderd euro), \
betaald bij aanvang van de huurovereenkomst betreffende het gehuurde aan [ADRES GEHUURDE].

**2.** De huurovereenkomst is per [EINDDATUM] beëindigd. Het gehuurde is op die datum \
in goede staat opgeleverd, hetgeen is vastgelegd in het opnamerapport d.d. [DATUM OPNAME]. \
Sindsdien zijn inmiddels drie maanden verstreken.

**3.** Op grond van artikel 7:261 lid 1 BW en de bepalingen van de huurovereenkomst \
(artikel [NUMMER]) bent u gehouden de waarborgsom terug te betalen, verminderd met \
eventuele verrekeningen waarvoor de waarborgsom is gesteld.

**4.** Ondanks herhaalde verzoeken van cliënt d.d. [DATUMS EERDERE VERZOEKEN] heeft u \
de waarborgsom niet terugbetaald, noch heeft u een specificatie verstrekt van eventuele \
verrekeningen.

**5.** Bij deze sommeer ik u dan ook om binnen **veertien (14) dagen** na dagtekening \
van deze brief het bedrag van **€ 2.400,00** over te maken op rekeningnummer \
[IBAN CLIËNT] ten name van [NAAM CLIËNT], dan wel een gespecificeerde opgave te doen \
van eventuele verrekeningen.

**6.** Indien betaling of een deugdelijke specificatie uitblijft, behoudt cliënt zich \
het recht voor om zonder nadere aankondiging rechtsmaatregelen te treffen. De kosten \
hiervan, waaronder buitengerechtelijke incassokosten conform het Besluit vergoeding \
voor buitengerechtelijke incassokosten, alsmede de wettelijke rente ex artikel 6:119 BW \
vanaf het moment van verzuim, zullen op u worden verhaald.

In afwachting van uw spoedige reactie.

Hoogachtend,

[NAAM JURIDISCH ADVISEUR]
[FUNCTIE]
Voorbeeld Rechtsbijstand
[TELEFOONNUMMER]
[E-MAILADRES]

---

**Gebruikte template**: Sommatiebriefformat
**Taal**: Nederlands (formeel juridisch register)\
""",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Compliance Agent — Few-shot examples
# ──────────────────────────────────────────────────────────────────────────────

COMPLIANCE_FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    {
        "role": "user",
        "content": (
            "Controleer of dit privacybeleid voldoet aan de AVG. Het bedrijf verzamelt "
            "e-mailadressen en namen van websitebezoekers via een contactformulier."
        ),
    },
    {
        "role": "assistant",
        "content": """\
## AVG Compliance Assessment — Privacybeleid

**Datum analyse**: [HUIDIGE DATUM]
**Beoordeeld document**: Privacybeleid [BEDRIJFSNAAM]
**Toetsingskader**: Algemene Verordening Gegevensbescherming (EU) 2016/679 (AVG/GDPR)

### Compliance Overzicht

| # | Vereiste | Status | Ernst | Referentie |
|---|---------|--------|-------|------------|
| 1 | Identiteit verwerkingsverantwoordelijke | ✅ VOLDOET | — | Art. 13 lid 1 sub a AVG |
| 2 | Contactgegevens FG/DPO | ❌ NIET CONFORM | Hoog | Art. 13 lid 1 sub b AVG |
| 3 | Verwerkingsdoeleinden | ⚠ GEDEELTELIJK | Gemiddeld | Art. 13 lid 1 sub c AVG |
| 4 | Rechtsgrond verwerking | ❌ NIET CONFORM | Kritiek | Art. 13 lid 1 sub c AVG |
| 5 | Bewaartermijnen | ❌ NIET CONFORM | Hoog | Art. 13 lid 2 sub a AVG |
| 6 | Rechten betrokkenen | ⚠ GEDEELTELIJK | Gemiddeld | Art. 13 lid 2 sub b-f AVG |
| 7 | Recht op klacht bij AP | ❌ NIET CONFORM | Gemiddeld | Art. 13 lid 2 sub d AVG |
| 8 | Doorgifte buiten EER | ❌ NIET CONFORM | Hoog | Art. 13 lid 1 sub f AVG |

### Bevindingen

**1. Rechtsgrond verwerking (KRITIEK)**
- **Bevinding**: Het privacybeleid vermeldt niet op welke rechtsgrond (art. 6 AVG) de \
verwerking van persoonsgegevens is gebaseerd.
- **Vereiste**: Art. 13 lid 1 sub c AVG vereist dat de rechtsgrond wordt vermeld. Voor \
een contactformulier is dit typisch "gerechtvaardigd belang" (art. 6 lid 1 sub f) of \
"toestemming" (art. 6 lid 1 sub a).
- **Risico**: Boete tot € 20.000.000 of 4% van de wereldwijde jaaromzet (art. 83 lid 5 AVG).
- **Remediëring**: Voeg de specifieke rechtsgrond toe, inclusief een belangenafweging \
indien gerechtvaardigd belang wordt ingeroepen.

**2. Bewaartermijnen (HOOG)**
- **Bevinding**: Er zijn geen bewaartermijnen gespecificeerd voor de verzamelde gegevens.
- **Vereiste**: Art. 13 lid 2 sub a AVG vereist vermelding van de bewaartermijn of de \
criteria ter bepaling daarvan.
- **Remediëring**: Specificeer concrete bewaartermijnen (bijv. "Contactgegevens worden \
bewaard tot 12 maanden na het laatste contact").

**3. Doorgifte buiten EER (HOOG)**
- **Bevinding**: Het beleid vermeldt niet of persoonsgegevens buiten de EER worden verwerkt. \
Gebruik van diensten als Google Analytics, Mailchimp, of cloudproviders kan doorgifte impliceren.
- **Remediëring**: Inventariseer alle (sub)verwerkers, vermeld eventuele doorgifte en de \
waarborgen (SCCs, adequaatheidsbesluit).

### Samenvatting
- **Getoetste vereisten**: 8
- **Conform**: 1
- **Niet conform**: 4
- **Gedeeltelijk**: 2
- **Algeheel risiconiveau**: **HOOG**

### Aanbeveling
Het privacybeleid vereist substantiële aanpassing om aan de AVG te voldoen. Prioriteer \
de bevindingen met status "Kritiek" en "Hoog". Overweeg een Data Protection Impact \
Assessment (DPIA) indien de verwerking grootschalig is (art. 35 AVG).\
""",
    },
]
