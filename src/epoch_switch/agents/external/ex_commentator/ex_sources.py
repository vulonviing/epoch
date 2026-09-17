"""Official-institution allowlist for EX1/EX2 web search.

A single source of truth: the same list feeds (a) the ``allowed_domains``
parameter passed to the Anthropic ``web_search_20250305`` tool, so the model
can never search outside these domains, and (b) the institution table
rendered into the EX1/EX2 system prompt, so the model knows who each domain
belongs to. Do not duplicate this list anywhere else -- import it.
"""
from __future__ import annotations

from typing import NamedTuple


class OfficialSource(NamedTuple):
    domain: str
    institution: str
    scope: str


OFFICIAL_SOURCES: tuple[OfficialSource, ...] = (
    # EU -- regulation and standard setters
    OfficialSource("eur-lex.europa.eu", "EUR-Lex", "EU Official Journal, consolidated legislation"),
    OfficialSource("efrag.org", "EFRAG", "ESRS standard setter"),
    OfficialSource("commission.europa.eu", "European Commission", "EU policy and legislative proposals"),
    OfficialSource("esma.europa.eu", "ESMA", "EU sustainability-reporting supervision"),
    OfficialSource("eea.europa.eu", "European Environment Agency", "EU environmental data and assessments"),
    OfficialSource("echa.europa.eu", "European Chemicals Agency", "EU chemicals and pollution (ESRS E2)"),
    # Germany -- Siemens' primary jurisdiction, direct authorities for UC1/UC2
    OfficialSource("bafa.de", "BAFA", "EnEfG Sec. 8/16 administration (UC1)"),
    OfficialSource("dehst.de", "DEHSt", "German Emissions Trading Authority (UC2)"),
    OfficialSource("umweltbundesamt.de", "Umweltbundesamt (UBA)", "German Federal Environment Agency"),
    OfficialSource("bmuv.de", "BMUV", "German Federal Ministry for the Environment"),
    # Global standard setters and science bodies
    OfficialSource("ifrs.org", "IFRS Foundation / ISSB", "Global sustainability disclosure standards"),
    OfficialSource("globalreporting.org", "GRI", "Global Reporting Initiative standards"),
    OfficialSource("unfccc.int", "UNFCCC", "UN climate treaty body"),
    OfficialSource("unep.org", "UNEP", "UN Environment Programme"),
    OfficialSource("ipcc.ch", "IPCC", "Intergovernmental Panel on Climate Change"),
    OfficialSource("oecd.org", "OECD", "Environmental and economic policy analysis"),
    OfficialSource("iea.org", "IEA", "International Energy Agency"),
    OfficialSource("epa.gov", "US EPA", "US Environmental Protection Agency"),
)


def allowed_domains() -> list[str]:
    """Domain list for the web_search tool's ``allowed_domains`` parameter."""
    return [source.domain for source in OFFICIAL_SOURCES]


def render_table() -> str:
    """Markdown table of the allowlist, appended to the EX1/EX2 system prompt."""
    header = "| Domain | Institution | Scope |\n|---|---|---|"
    rows = "\n".join(f"| {s.domain} | {s.institution} | {s.scope} |" for s in OFFICIAL_SOURCES)
    return f"{header}\n{rows}"
