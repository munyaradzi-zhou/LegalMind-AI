"""Semantic Kernel plugin for legal text drafting via GPT-4o.

Uses specialized legal prompts to generate professional Dutch legal text
sections including response letters, case summaries, contract clauses,
and client communications. Used primarily by the Drafting agent.
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from legalmind.services import AzureOpenAIService

logger = structlog.get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Internal drafting prompts (not exposed to users)
# ──────────────────────────────────────────────────────────────────────────────

_DRAFT_SYSTEM_PROMPT = """\
Je bent een ervaren juridisch tekstschrijver gespecialiseerd in het Nederlands recht. \
Je schrijft professionele juridische teksten in correct formeel Nederlands.

Regels:
1. Schrijf uitsluitend in het Nederlands, tenzij expliciet anders gevraagd.
2. Gebruik formeel juridisch taalgebruik (u-vorm, correcte juridische terminologie).
3. Verwijs naar specifieke wetsartikelen waar relevant.
4. Markeer ontbrekende informatie met [PLACEHOLDER].
5. Volg de standaard opmaak voor het gevraagde documenttype.
6. Houd rekening met de doelgroep: juridische professionals of leken.
"""

_DOCUMENT_TYPE_PROMPTS: dict[str, str] = {
    "response_letter": (
        "Schrijf een formele reactiebrief. Gebruik genummerde paragrafen. "
        "Begin met een aanhef ('Geachte heer/mevrouw'), vermeld het onderwerp, "
        "behandel elk punt systematisch, en sluit af met 'Hoogachtend'."
    ),
    "case_summary": (
        "Schrijf een zaaksamenvatting. Structureer als volgt: "
        "1) Zaakgegevens, 2) Feitencomplex, 3) Juridisch kader, "
        "4) Analyse, 5) Advies/aanbeveling."
    ),
    "client_communication": (
        "Schrijf een heldere brief aan de cliënt. Gebruik begrijpelijk Nederlands "
        "(vermijd onnodig jargon). Leg juridische termen uit. "
        "Gebruik 'Met vriendelijke groet' als afsluiting."
    ),
    "legal_memo": (
        "Schrijf een juridisch memorandum voor intern gebruik. Structureer als: "
        "1) Onderwerp, 2) Vraagstelling, 3) Korte conclusie, "
        "4) Juridisch kader, 5) Analyse, 6) Conclusie en aanbeveling."
    ),
    "demand_letter": (
        "Schrijf een sommatiebrief. Stel de feiten vast, vermeld de rechtsgrond, "
        "formuleer een duidelijke sommatie met termijn (veertien dagen), "
        "en waarschuw voor juridische stappen bij uitblijven."
    ),
    "contract_clause": (
        "Schrijf een contractuele clausule. Gebruik genummerde leden. "
        "Wees precies en ondubbelzinnig. Definieer gebruikte termen."
    ),
    "settlement_proposal": (
        "Schrijf een schikkingsvoorstel. Beschrijf het geschil kort, "
        "formuleer het voorstel concreet met bedragen en termijnen, "
        "en vermeld de voorwaarden voor aanvaarding."
    ),
}


class DraftTool:
    """Semantic Kernel plugin for generating legal text using GPT-4o.

    Uses specialized prompts and the Azure OpenAI service to draft
    professional Dutch legal documents, letters, and communications.
    """

    def __init__(self, openai_service: AzureOpenAIService | None = None) -> None:
        """Initialize the draft tool.

        Args:
            openai_service: An existing AzureOpenAIService. If not provided,
                a new one is created using default configuration.
        """
        self._openai = openai_service or AzureOpenAIService()

    @kernel_function(
        name="draft_legal_text",
        description=(
            "Draft a section of legal text in Dutch. Supports various document types: "
            "response_letter, case_summary, client_communication, legal_memo, "
            "demand_letter, contract_clause, settlement_proposal. Provide context "
            "and instructions for what to write."
        ),
    )
    async def draft_legal_text(
        self,
        document_type: Annotated[
            str,
            "Type of document: 'response_letter', 'case_summary', 'client_communication', "
            "'legal_memo', 'demand_letter', 'contract_clause', 'settlement_proposal'",
        ],
        instructions: Annotated[str, "Detailed instructions for what to draft, including relevant facts and context"],
        tone: Annotated[
            str,
            "Desired tone: 'formal' (default), 'firm', 'conciliatory', 'neutral'",
        ] = "formal",
        max_words: Annotated[int, "Approximate maximum word count (default: 500)"] = 500,
    ) -> Annotated[str, "JSON object with the drafted text and metadata"]:
        """Draft a legal text section using GPT-4o with specialized prompts.

        Args:
            document_type: The type of legal document to draft.
            instructions: What to write, including relevant facts.
            tone: The desired tone of the text.
            max_words: Approximate maximum word count.

        Returns:
            JSON string containing the drafted text and metadata.
        """
        logger.info(
            "draft_tool_invoked",
            document_type=document_type,
            tone=tone,
            max_words=max_words,
        )

        # Get document-type-specific prompt or use generic instructions
        type_prompt = _DOCUMENT_TYPE_PROMPTS.get(document_type, "")
        if not type_prompt:
            type_prompt = f"Schrijf een {document_type}. Volg de standaard juridische opmaak voor dit documenttype."

        tone_instruction = {
            "formal": "Gebruik een formele, zakelijke toon.",
            "firm": "Gebruik een vastberaden, assertieve toon. Wees duidelijk over consequenties.",
            "conciliatory": "Gebruik een verzoenende, constructieve toon. Zoek naar oplossingen.",
            "neutral": "Gebruik een neutrale, objectieve toon. Presenteer feiten zonder oordeel.",
        }.get(tone, "Gebruik een formele, zakelijke toon.")

        user_prompt = (
            f"## Opdracht\n\n"
            f"Documenttype: {document_type}\n"
            f"Toon: {tone_instruction}\n"
            f"Maximale lengte: circa {max_words} woorden\n\n"
            f"## Type-specifieke instructies\n\n{type_prompt}\n\n"
            f"## Inhoudelijke instructies\n\n{instructions}\n\n"
            f"## Vereisten\n\n"
            f"- Schrijf in het Nederlands\n"
            f"- Markeer ontbrekende gegevens met [PLACEHOLDER]\n"
            f"- Voeg een opmerking toe dat het concept professionele beoordeling vereist"
        )

        try:
            response = await self._openai.chat_completion(
                messages=[
                    {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=min(max_words * 3, 8192),  # Rough token estimate
            )

            drafted_text = ""
            choices = response.get("choices", [])
            if choices:
                drafted_text = choices[0].get("message", {}).get("content", "")

            result = {
                "drafted_text": drafted_text,
                "document_type": document_type,
                "tone": tone,
                "word_count": len(drafted_text.split()) if drafted_text else 0,
                "note": "Dit is een concept dat professionele beoordeling vereist voor verzending.",
            }

            logger.info(
                "draft_tool_result",
                word_count=result["word_count"],
                document_type=document_type,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("draft_tool_error")
            return json.dumps(
                {
                    "error": "Text drafting failed. Please try again with different instructions.",
                    "drafted_text": "",
                }
            )

    @kernel_function(
        name="improve_legal_text",
        description=(
            "Improve or refine existing legal text. Can adjust tone, fix terminology, "
            "improve clarity, or make text more formal. Useful for polishing draft text."
        ),
    )
    async def improve_legal_text(
        self,
        text: Annotated[str, "The legal text to improve"],
        improvement_instructions: Annotated[
            str,
            "What to improve: e.g., 'make more formal', 'simplify for client', "
            "'add legal references', 'correct terminology'",
        ],
    ) -> Annotated[str, "JSON object with the improved text"]:
        """Improve existing legal text based on instructions.

        Args:
            text: The text to improve.
            improvement_instructions: What aspects to improve.

        Returns:
            JSON string containing the improved text.
        """
        logger.info(
            "improve_text_invoked",
            text_length=len(text),
            instructions=improvement_instructions[:100],
        )

        user_prompt = (
            f"## Oorspronkelijke tekst\n\n{text}\n\n"
            f"## Verbeterinstructies\n\n{improvement_instructions}\n\n"
            f"## Opdracht\n\n"
            f"Verbeter de bovenstaande tekst volgens de instructies. "
            f"Behoud de juridische correctheid en de kern van de boodschap. "
            f"Geef alleen de verbeterde tekst terug, zonder uitleg."
        )

        try:
            response = await self._openai.chat_completion(
                messages=[
                    {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=8192,
            )

            improved_text = ""
            choices = response.get("choices", [])
            if choices:
                improved_text = choices[0].get("message", {}).get("content", "")

            result = {
                "improved_text": improved_text,
                "original_length": len(text),
                "improved_length": len(improved_text),
                "instructions_applied": improvement_instructions,
            }

            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception:
            logger.exception("improve_text_error")
            return json.dumps(
                {
                    "error": "Text improvement failed.",
                    "improved_text": "",
                }
            )
