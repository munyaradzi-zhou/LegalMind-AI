"""Jinja2-based prompt templates for dynamic context injection.

Templates use Jinja2 syntax with double-brace variables ({{ var }}) and support
conditional blocks, loops, and filters. All templates are designed for the Dutch
legal domain and produce structured prompts for LegalMind AI agents.
"""

from __future__ import annotations

from typing import Any

import structlog
from jinja2 import BaseLoader, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

logger = structlog.get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Jinja2 environment (shared, sandboxed)
# ──────────────────────────────────────────────────────────────────────────────

_jinja_env = SandboxedEnvironment(
    loader=BaseLoader(),
    autoescape=False,  # nosec B701 — plain-text LLM prompts, not HTML
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(template_string: str, **kwargs: Any) -> str:
    """Render a Jinja2 prompt template with the given variables.

    Args:
        template_string: A Jinja2 template string (one of the constants below).
        **kwargs: Template variables to inject.

    Returns:
        The rendered template string.

    Raises:
        ValueError: If a required template variable is missing or the template
            syntax is invalid.
    """
    try:
        template = _jinja_env.from_string(template_string)
        rendered = template.render(**kwargs)
        logger.debug("prompt_template_rendered", template_length=len(rendered))
        return rendered
    except UndefinedError as exc:
        logger.error("prompt_template_missing_variable", error=str(exc))
        raise ValueError(f"Missing template variable: {exc}") from exc
    except TemplateSyntaxError as exc:
        logger.error("prompt_template_syntax_error", error=str(exc))
        raise ValueError(f"Template syntax error: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────────
# Case Analysis Template
# ──────────────────────────────────────────────────────────────────────────────

CASE_ANALYSIS_TEMPLATE = """\
## Zaakinformatie

**Zaaknummer**: {{ case_number }}
**Zaaktype**: {{ case_type }}
**Cliënt**: {{ client_name }}
{% if opposing_party %}
**Wederpartij**: {{ opposing_party }}
{% endif %}
{% if case_date %}
**Datum**: {{ case_date }}
{% endif %}

## Feiten

{{ facts }}

{% if documents %}
## Beschikbare documenten

{% for doc in documents %}
### Document {{ loop.index }}: {{ doc.title }}
- **Type**: {{ doc.document_type }}
- **Datum**: {{ doc.date }}
{% if doc.summary %}
- **Samenvatting**: {{ doc.summary }}
{% endif %}
{% endfor %}
{% endif %}

{% if prior_analysis %}
## Eerdere analyse

{{ prior_analysis }}
{% endif %}

## Opdracht

Analyseer bovenstaande zaak en geef:
1. Een overzicht van de juridische geschilpunten
2. Toepasselijk juridisch kader (relevante wetsartikelen)
3. Sterke en zwakke punten van de positie van cliënt
4. Aanbevolen strategie
5. Belangrijke deadlines of termijnen om in de gaten te houden

{% if specific_questions %}
## Specifieke vragen

{% for question in specific_questions %}
{{ loop.index }}. {{ question }}
{% endfor %}
{% endif %}
"""

# ──────────────────────────────────────────────────────────────────────────────
# Document Comparison Template
# ──────────────────────────────────────────────────────────────────────────────

DOCUMENT_COMPARISON_TEMPLATE = """\
## Documentvergelijking

Je krijgt twee versies van een juridisch document om te vergelijken.

### Document A: {{ document_a.title }}
- **Type**: {{ document_a.document_type }}
- **Datum**: {{ document_a.date }}
- **Versie**: {{ document_a.version | default("onbekend") }}

**Inhoud**:
{{ document_a.content }}

---

### Document B: {{ document_b.title }}
- **Type**: {{ document_b.document_type }}
- **Datum**: {{ document_b.date }}
- **Versie**: {{ document_b.version | default("onbekend") }}

**Inhoud**:
{{ document_b.content }}

---

## Opdracht

Vergelijk Document A en Document B en rapporteer:

1. **Gewijzigde clausules**: Welke bepalingen zijn inhoudelijk gewijzigd? Geef per \
wijziging de oude en nieuwe tekst.
2. **Toegevoegde clausules**: Welke bepalingen zijn nieuw in Document B?
3. **Verwijderde clausules**: Welke bepalingen uit Document A ontbreken in Document B?
4. **Juridische impact**: Beoordeel de juridische consequenties van de wijzigingen.
5. **Risicobeoordeling**: Zijn er wijzigingen die een risico vormen voor \
{{ interested_party | default("de cliënt") }}?

{% if focus_areas %}
## Focusgebieden

Let in het bijzonder op de volgende aspecten:
{% for area in focus_areas %}
- {{ area }}
{% endfor %}
{% endif %}

Presenteer de vergelijking in tabelformaat waar mogelijk.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Legal Q&A Template (with citations)
# ──────────────────────────────────────────────────────────────────────────────

LEGAL_QA_TEMPLATE = """\
## Juridische vraag

{{ question }}

{% if context_documents %}
## Relevante bronnen

De volgende documenten zijn gevonden in de kennisbank en kunnen relevant zijn:

{% for doc in context_documents %}
### Bron {{ loop.index }}
- **Titel**: {{ doc.title }}
- **Type**: {{ doc.source_type | default("onbekend") }}
- **Relevantiescore**: {{ doc.relevance_score | default("n.v.t.") }}
{% if doc.date %}
- **Datum**: {{ doc.date }}
{% endif %}

**Fragment**:
> {{ doc.content }}

{% endfor %}
{% endif %}

{% if case_context %}
## Zaakcontext

{{ case_context }}
{% endif %}

## Instructies

Beantwoord de juridische vraag op basis van de beschikbare bronnen en je kennis van \
het Nederlands recht.

**Vereisten**:
1. **Citeer je bronnen**: Verwijs naar specifieke wetsartikelen (bijv. "Art. 6:162 BW"), \
   ECLI-nummers voor jurisprudentie, en bronnummers uit de kennisbank ([Bron X]).
2. **Structureer je antwoord**: Gebruik het format Regel → Toepassing → Conclusie.
3. **Onderscheid zekerheid**: Geef aan of je antwoord gebaseerd is op vaststaand recht, \
   heersende leer, of een juridische inschatting.
4. **Taal**: Antwoord in {{ response_language | default("het Nederlands") }}.
5. **Beperkingen**: Als de vraag niet met zekerheid te beantwoorden is op basis van de \
   beschikbare informatie, geef dit expliciet aan.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Drafting Template
# ──────────────────────────────────────────────────────────────────────────────

DRAFTING_TEMPLATE = """\
## Schrijfopdracht

**Documenttype**: {{ document_type }}
**Taal**: {{ language | default("Nederlands") }}
**Register**: {{ register | default("Formeel juridisch") }}

{% if sender %}
### Afzender
- **Naam**: {{ sender.name }}
- **Organisatie**: {{ sender.organization | default("Voorbeeld Rechtsbijstand") }}
- **Functie**: {{ sender.role | default("Juridisch adviseur") }}
{% endif %}

{% if recipient %}
### Geadresseerde
- **Naam**: {{ recipient.name }}
{% if recipient.organization %}
- **Organisatie**: {{ recipient.organization }}
{% endif %}
{% if recipient.address %}
- **Adres**: {{ recipient.address }}
{% endif %}
{% endif %}

## Inhoudelijke instructies

{{ instructions }}

{% if key_facts %}
## Relevante feiten

{% for fact in key_facts %}
- {{ fact }}
{% endfor %}
{% endif %}

{% if legal_basis %}
## Juridische grondslag

{% for basis in legal_basis %}
- {{ basis }}
{% endfor %}
{% endif %}

{% if tone_guidance %}
## Toongids

{{ tone_guidance }}
{% endif %}

{% if reference_documents %}
## Referentiedocumenten

{% for doc in reference_documents %}
### {{ doc.title }}
{{ doc.content }}

{% endfor %}
{% endif %}

## Vereisten

1. Schrijf in {{ language | default("het Nederlands") }} met een \
{{ register | default("formeel juridisch") }} register.
2. Gebruik de juiste aanhef en afsluiting voor het documenttype.
3. Nummering van paragrafen voor formele correspondentie.
4. Markeer ontbrekende informatie met [PLACEHOLDER].
5. Voeg een disclaimer toe dat het document professionele beoordeling vereist.
{% if max_length %}
6. Maximale lengte: {{ max_length }} woorden.
{% endif %}
"""
