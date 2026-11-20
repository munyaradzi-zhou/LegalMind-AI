"""Questions on the evaluation corpus (corpus/manifest.json), merged into the golden sets.

Run ``python tests/evaluation/datasets/corpus_questions.py`` after changing this file: it
looks up each document's title in the manifest (the evaluations match sources by title)
and rewrites the corpus part of both golden sets, leaving the original questions alone.

Every expected passage and fact was checked against the source text. Unanswerable
questions are built around a near miss in another document (the same amount, the same
kind of policy, a ruling on the same topic), so they test the wrong-document problem.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
MANIFEST = HERE.parents[2] / "corpus" / "manifest.json"

# (id, query, corpus id, passage, category, language)
RETRIEVAL = [
    ("R36", "Hoe hoog is de kale huur van de woning aan de Lindenlaan 14-2?", "huur-01-huurovereenkomst", "€ 1.185,00 per maand", "rent", "nl"),
    ("R37", "Waaruit bestaat het voorschot op de servicekosten van mevrouw De Wit?", "huur-01-huurovereenkomst", "Totaal voorschot servicekosten", "table_docx", "nl"),
    ("R38", "Hoeveel waarborgsom betaalt de huurder van Lindenlaan 14-2?", "huur-01-huurovereenkomst", "waarborgsom van € 2.370,00", "rent", "nl"),
    ("R39", "Welke lekkage meldde de huurder in januari 2026?", "huur-02-gebrekenbrief", "loopt er water langs de lamp", "scan", "nl"),
    ("R40", "How much rent is Ms De Wit withholding each month until the defects are fixed?", "huur-02-gebrekenbrief", "€ 350,00 per maand van de huurprijs inhouden", "scan", "en"),
    ("R41", "Hoe groot is de huurachterstand van mevrouw De Wit volgens de verhuurder?", "huur-03-huurachterstand", "Wij verzoeken u de achterstand van € 1.950,00", "photo", "nl"),
    ("R42", "Wat wordt de nieuwe kale huur per 1 juli 2026?", "huur-04-huurverhoging", "€ 1.233,59", "rent", "nl"),
    ("R43", "Welke proeftijd geldt voor Eva Kramer?", "arbeid-01-arbeidsovereenkomst", "Er geldt een proeftijd van één maand", "employment", "nl"),
    ("R44", "In welke salarisschaal is Eva Kramer ingedeeld?", "arbeid-01-arbeidsovereenkomst", "schaal 6, trede 4", "table_pdf", "nl"),
    ("R45", "Hoe motiveert Noordlicht Logistiek het concurrentiebeding?", "arbeid-01-arbeidsovereenkomst", "toegang tot de volledige klantenportefeuille", "employment", "nl"),
    ("R46", "Why was Eva Kramer dismissed with immediate effect?", "arbeid-02-ontslagbrief", "zonder toestemming en zonder bericht niet op uw werk verschenen", "scan", "en"),
    ("R47", "Welk brutosalaris is over 1 tot en met 14 maart 2026 berekend voor Eva Kramer?", "arbeid-03-loonoverzicht", "€ 1.558,06", "photo", "nl"),
    ("R48", "Welke vergoeding biedt de werkgever in de concept-vaststellingsovereenkomst?", "arbeid-04-vaststellingsovereenkomst", "vergoeding van € 4.000,00 bruto", "docx", "nl"),
    ("R49", "Binnen welke termijn kan Eva Kramer de vaststellingsovereenkomst ontbinden?", "arbeid-04-vaststellingsovereenkomst", "binnen veertien dagen na de dag waarop zij is gesloten", "docx", "nl"),
    ("R50", "Vanaf welke windsnelheid is stormschade gedekt op de polis van de heer Visser?", "verzekering-01-polisblad", "ten minste 14 meter per seconde", "docx", "nl"),
    ("R51", "Wat is het verzekerde bedrag voor de schuur en garage van de heer Visser?", "verzekering-01-polisblad", "Bijgebouwen (schuur, garage)", "table_docx", "nl"),
    ("R52", "Wat is er met het dak van het woonhuis gebeurd tijdens de storm van 3 februari 2026?", "verzekering-02-schadeformulier", "ongeveer twintig dakpannen", "scan", "nl"),
    ("R53", "Hoeveel kost het herstel van het dak van de schuur volgens de schademelding?", "verzekering-02-schadeformulier", "Herstel dak schuur", "scan_table", "nl"),
    ("R54", "Welke windkracht is volgens de verzekeraar gemeten op 3 februari 2026?", "verzekering-03-afwijzingsbrief", "12 meter per seconde, dat is windkracht 6", "insurance", "nl"),
    ("R55", "What did the loss adjuster find about the roof of the Visser house?", "verzekering-03-afwijzingsbrief", "panlatten van het dak van het woonhuis grotendeels verrot", "insurance", "en"),
    ("R56", "Wanneer kan een huurder huurvermindering vorderen wegens een gebrek?", "wet-bw7-huur", "evenredige vermindering van de huurprijs vorderen", "legislation", "nl"),
    ("R57", "Welke opzegtermijn geldt wettelijk bij opzegging door de huurder van woonruimte?", "wet-bw7-huur", "doch niet korter dan een maand en niet langer dan drie maanden", "legislation", "nl"),
    ("R58", "Wanneer mag een concurrentiebeding in een tijdelijk arbeidscontract?", "wet-bw7-arbeid", "schriftelijke motivering van de werkgever blijkt", "legislation", "nl"),
    ("R59", "Hoe lang mag de proeftijd zijn bij een contract voor bepaalde tijd van een jaar?", "wet-bw7-arbeid", "een maand, indien de overeenkomst is aangegaan voor langer dan zes maanden", "legislation", "nl"),
    ("R60", "Within what period can an employee dissolve a termination agreement?", "wet-bw7-arbeid", "binnen veertien dagen na de datum waarop de overeenkomst tot stand is gekomen", "legislation", "en"),
    ("R61", "Wat moet een verzekeringnemer melden voordat de verzekering wordt gesloten?", "wet-bw7-verzekering", "alle feiten mede te delen die hij kent of behoort te kennen", "legislation", "nl"),
    ("R62", "Wanneer is de werkgever niet aansprakelijk voor schade van een werknemer?", "wet-bw7-arbeid", "opzet of bewuste roekeloosheid van de werknemer", "legislation", "nl"),
    ("R63", "Welke huurachterstand moest de huurder in de zaak van Casade betalen?", "uitspraak-rbzwb-2025-203", "€ 1.957,62", "case_law", "nl"),
    ("R64", "Welke transitievergoeding kende de Rotterdamse kantonrechter toe na het onterechte ontslag op staande voet?", "uitspraak-rbrot-2025-1815", "€ 450,18 bruto aan transitievergoeding", "case_law", "nl"),
    ("R65", "Welke gefixeerde schadevergoeding kende Rechtbank Midden-Nederland toe?", "uitspraak-rbmne-2025-303", "€ 3.064,52 bruto aan gefixeerde schadevergoeding", "case_law", "nl"),
    ("R66", "Hoeveel achterstallig salaris per maand moest de werkgever betalen in de zaak over betalingsonmacht?", "uitspraak-rbzwb-2025-321", "€ 6.000,00 bruto per maand", "case_law", "nl"),
    ("R67", "Welke rechtszaak ging over de Algemene Huurvoorwaarden woonruimte van maart 2024?", "uitspraak-rbnho-2025-422", "Toetsing van de Algemene Huurvoorwaarden", "case_law", "nl"),
    ("R68", "Which ruling dealt with a passenger damage insurance claim after a death?", "uitspraak-rbrot-2023-1772", "overlijdensschade", "case_law", "en"),
]

# (id, question, corpus id or None, facts: each a list of acceptable variants)
GENERATION = [
    ("G28", "Hoe hoog zijn de kale huur en het voorschot servicekosten van de woning aan de Lindenlaan 14-2?", "huur-01-huurovereenkomst", [["1.185"], ["65"]]),
    ("G29", "Welke onderdelen vallen onder het voorschot servicekosten in de huurovereenkomst van mevrouw De Wit?", "huur-01-huurovereenkomst", [["schoonmaak"], ["tuinonderhoud", "tuin"], ["glasbewassing"], ["energie"]]),
    ("G30", "Hoeveel bedraagt de waarborgsom voor de woning aan de Lindenlaan 14-2?", "huur-01-huurovereenkomst", [["2.370"]]),
    ("G31", "Welke gebreken meldde mevrouw De Wit in haar brief van 12 januari 2026, en welke termijn stelde zij voor herstel?", "huur-02-gebrekenbrief", [["badkamer"], ["schimmel"], ["23 februari 2026", "zes weken", "6 weken"]]),
    ("G32", "Hoeveel houdt mevrouw De Wit per maand in op de huur tot de gebreken zijn hersteld?", "huur-02-gebrekenbrief", [["350"]]),
    ("G33", "Hoe hoog is de huurachterstand van mevrouw De Wit volgens het overzicht van 2 april 2026?", "huur-03-huurachterstand", [["1.950"]]),
    ("G34", "Hoeveel heeft mevrouw De Wit in februari 2026 betaald?", "huur-03-huurachterstand", [["900"]]),
    ("G35", "Wat wordt de kale huur van mevrouw De Wit per 1 juli 2026 en met welk percentage stijgt die?", "huur-04-huurverhoging", [["1.233,59", "1233,59"], ["4,1", "4.1"]]),
    ("G36", "Hoe lang is de proeftijd in de arbeidsovereenkomst van Eva Kramer?", "arbeid-01-arbeidsovereenkomst", [["één maand", "een maand", "1 maand"]]),
    ("G37", "Wat is het bruto maandsalaris van Eva Kramer, en hoeveel vakantietoeslag krijgt zij?", "arbeid-01-arbeidsovereenkomst", [["3.450"], ["8%", "8 %", "8 procent"]]),
    ("G38", "Welke opzegtermijnen gelden voor werkgever en werknemer in de arbeidsovereenkomst van Eva Kramer?", "arbeid-01-arbeidsovereenkomst", [["één maand", "een maand", "1 maand"], ["twee maanden", "2 maanden"]]),
    ("G39", "Om welke redenen is Eva Kramer op staande voet ontslagen?", "arbeid-02-ontslagbrief", [["9, 10 en 11 maart", "9 tot en met 11 maart", "niet op uw werk verschenen", "afwezig"], ["weekplanning", "Hanzevracht", "geweigerd", "weigering"]]),
    ("G40", "Is volgens Noordlicht Logistiek een transitievergoeding verschuldigd na het ontslag van Eva Kramer?", "arbeid-02-ontslagbrief", [["niet verschuldigd", "geen transitievergoeding"], ["ernstig verwijtbaar"]]),
    ("G41", "Welk brutosalaris is over maart 2026 voor Eva Kramer berekend, en is het uitbetaald?", "arbeid-03-loonoverzicht", [["1.558,06"], ["niet betaald", "niet uitbetaald"]]),
    ("G42", "Welke bedragen biedt Noordlicht Logistiek Eva Kramer aan in de concept-vaststellingsovereenkomst?", "arbeid-04-vaststellingsovereenkomst", [["4.000"], ["750"]]),
    ("G43", "Hoe lang is de bedenktermijn in de vaststellingsovereenkomst met Eva Kramer?", "arbeid-04-vaststellingsovereenkomst", [["veertien dagen", "14 dagen"]]),
    ("G44", "Wat is het eigen risico voor bijgebouwen op de woonhuisverzekering van de heer Visser?", "verzekering-01-polisblad", [["250"]]),
    ("G45", "Wanneer is stormschade gedekt volgens de polis van de heer Visser?", "verzekering-01-polisblad", [["14 meter per seconde", "windkracht 7"]]),
    ("G46", "Welke oorzaken van de schade heeft de heer Visser aangekruist op het schadeformulier?", "verzekering-02-schadeformulier", [["storm"], ["neerslag"]]),
    ("G47", "Op welk bedrag schat de heer Visser de totale schade in zijn schademelding?", "verzekering-02-schadeformulier", [["6.800"]]),
    ("G48", "Waarom heeft Zekerheid Schadeverzekeringen de claim van de heer Visser afgewezen?", "verzekering-03-afwijzingsbrief", [["windkracht 6", "12 meter per seconde"], ["slecht onderhoud", "panlatten", "verrot"]]),
    ("G49", "Binnen welke termijn kan de heer Visser bezwaar maken tegen de afwijzing van zijn claim?", "verzekering-03-afwijzingsbrief", [["30 dagen", "dertig dagen"]]),
    ("G50", "Welke opzegtermijn moet een huurder van woonruimte volgens de wet in acht nemen?", "wet-bw7-huur", [["een maand", "één maand"], ["drie maanden", "3 maanden"]]),
    ("G51", "Mag een arbeidsovereenkomst voor bepaalde tijd een concurrentiebeding bevatten, en onder welke voorwaarde?", "wet-bw7-arbeid", [["motivering", "gemotiveerd"], ["zwaarwegende"]]),
    ("G52", "Hoe lang mag de proeftijd volgens de wet zijn bij een arbeidsovereenkomst voor bepaalde tijd van twaalf maanden?", "wet-bw7-arbeid", [["een maand", "één maand", "1 maand"]]),
    ("G53", "Binnen welke termijn kan een werknemer een beëindigingsovereenkomst ontbinden, en wat geldt als de werkgever dat recht niet vermeldt?", "wet-bw7-arbeid", [["veertien dagen", "14 dagen"], ["drie weken", "3 weken"]]),
    ("G54", "Wanneer kan een huurder volgens de wet huurvermindering vorderen wegens een gebrek?", "wet-bw7-huur", [["evenredige vermindering", "evenredig"], ["kennis gegeven", "gemeld", "melding"]]),
    ("G55", "Wat moet een verzekeringnemer volgens artikel 7:928 BW doen vóór het sluiten van de verzekering?", "wet-bw7-verzekering", [["feiten", "mededelen", "mede te delen"]]),
    ("G56", "Wanneer kan een werkgever aansprakelijkheid voor een arbeidsongeval ontlopen volgens artikel 7:658 BW?", "wet-bw7-arbeid", [["nagekomen", "zorgplicht"], ["opzet of bewuste roekeloosheid", "bewuste roekeloosheid"]]),
    ("G57", "Waarom werd de huurovereenkomst ontbonden in de zaak van Casade bij Rechtbank Zeeland-West-Brabant?", "uitspraak-rbzwb-2025-203", [["hoofdverblijf"], ["derden"]]),
    ("G58", "Welke vergoedingen kende de Rotterdamse kantonrechter toe na het onterechte ontslag op staande voet in ECLI:NL:RBROT:2025:1815?", "uitspraak-rbrot-2025-1815", [["billijke vergoeding"], ["transitievergoeding"], ["4.000"]]),
    ("G59", "Wat oordeelde de kantonrechter in ECLI:NL:RBMNE:2025:303 over de opzegging van 5 september 2024?", "uitspraak-rbmne-2025-303", [["niet rechtsgeldig"], ["3.064,52"]]),
    ("G60", "Welk achterstallig salaris moest de werkgever betalen in ECLI:NL:RBZWB:2025:321?", "uitspraak-rbzwb-2025-321", [["november"], ["6.000"]]),
    # Unanswerable, each next to a near miss in another document
    ("G61", "Welke billijke vergoeding heeft de kantonrechter aan Eva Kramer toegekend?", None, []),
    ("G62", "Heeft de Huurcommissie de huur van mevrouw De Wit verlaagd?", None, []),
    ("G63", "Wat is het polisnummer van de rechtsbijstandverzekering van de heer Visser?", None, []),
    ("G64", "Hoeveel bedraagt het eigen risico voor stormschade op de verzekering van de heer De Vries?", None, []),
    ("G65", "Op welke datum is de vaststellingsovereenkomst met Eva Kramer ondertekend?", None, []),
    ("G66", "Wat heeft het Kifid beslist over de klacht van de heer Visser?", None, []),
]


def main() -> None:
    titles = {d["id"]: d["title"] for d in json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]}

    path = HERE / "retrieval_golden_set.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    original = [q for q in data["queries"] if int(q["id"][1:]) < 36]
    data["queries"] = original + [
        {"id": i, "query": q, "source": titles[s], "passage": p, "category": c, "language": lang}
        for i, q, s, p, c, lang in RETRIEVAL
    ]
    data["version"] = "3.0"
    data["description"] = (
        "Retrieval golden set: R01-R35 on the three sample files, R36-R68 on the evaluation corpus "
        "(legislation, case law and case files as PDF, Word, scans and photos; see corpus/manifest.json). "
        "Each query has one source document and a short passage that the correct chunk must contain."
    )
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    path = HERE / "generation_golden_set.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    original = [q for q in data["queries"] if int(q["id"][1:]) < 28]
    data["queries"] = original + [
        {"id": i, "question": q, "source": titles[s] if s else None, "answerable": s is not None, "must_contain": f}
        for i, q, s, f in GENERATION
    ]
    data["version"] = "3.0"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generation: {len(data['queries'])} questions, retrieval updated")


if __name__ == "__main__":
    main()
