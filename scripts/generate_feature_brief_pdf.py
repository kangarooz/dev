"""Generate the Infrastructure Risk Intelligence v2 feature brief as a PDF.

This script renders the structured user stories, acceptance criteria, and
implementation notes into a PDF document using ReportLab. The resulting file
is stored under ``docs/Infrastructure_Risk_Intelligence_v2.pdf``.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "docs" / "Infrastructure_Risk_Intelligence_v2.pdf"


def _build_title(styles) -> list:
    content = [
        Paragraph("Infrastructure Risk Intelligence Enhancement Package", styles["Title"]),
        Spacer(1, 0.3 * inch),
    ]
    metadata = [
        ("Initiative", "Infrastructure Risk Modernization"),
        ("Environment", "Palantir Foundry"),
        ("Version", "v2"),
        (
            "Stakeholders",
            "Mission Analysis, Regional Operations, Risk Data Engineering",
        ),
    ]
    for label, value in metadata:
        content.append(Paragraph(f"<b>{label}:</b> {value}", styles["BodyText"]))
    content.append(Spacer(1, 0.25 * inch))
    return content


def _build_overview(styles) -> list:
    overview = (
        "This release introduces an integrated risk intelligence capability within Foundry "
        "that allows mission analysts and resilience planners to:"  # noqa: E501
    )
    bullets = [
        "Calculate dynamic risk scores for infrastructure assets based on both tier "
        "criticality and associated hazards and threats.",
        "Simulate hazard or threat events and mitigation scenarios to quantify impacts "
        "on assets and regions.",
        "Automatically extract and process Authoritative Hazard Threat Assessment (AHTA) "
        "data from PDF sources to keep risk scores continuously aligned with the latest "
        "intelligence.",
    ]
    content = [Paragraph("Overview", styles["Heading1"]), Paragraph(overview, styles["BodyText"]) ]
    content.append(Spacer(1, 0.1 * inch))
    for bullet in bullets:
        content.append(Paragraph(f"• {bullet}", styles["BodyText"]))
    content.append(Spacer(1, 0.25 * inch))
    return content


def _build_table(title: str, headers: list[str], rows: list[list[str]], styles) -> list:
    content = [Paragraph(title, styles["Heading2"])]
    table = Table([headers, *rows], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.grey),
            ]
        )
    )
    content.append(table)
    content.append(Spacer(1, 0.25 * inch))
    return content


def _story_block(title: str, persona: str, want: str, purpose: str, description: str, criteria: list[str], example: list[str] | None, outcome: list[str], styles) -> list:  # noqa: E501
    content = [Paragraph(title, styles["Heading1"])]
    content.append(Paragraph(f"<b>As a</b> {persona}", styles["BodyText"]))
    content.append(Paragraph(f"<b>I want</b> {want}", styles["BodyText"]))
    content.append(Paragraph(f"<b>So that</b> {purpose}", styles["BodyText"]))
    content.append(Spacer(1, 0.1 * inch))
    content.append(Paragraph("Description", styles["Heading2"]))
    content.append(Paragraph(description, styles["BodyText"]))
    content.append(Spacer(1, 0.05 * inch))
    content.append(Paragraph("Acceptance Criteria", styles["Heading2"]))
    for item in criteria:
        content.append(Paragraph(f"• {item}", styles["BodyText"]))
    if example:
        content.append(Spacer(1, 0.05 * inch))
        content.append(Paragraph("Example", styles["Heading2"]))
        for line in example:
            content.append(Paragraph(f"• {line}", styles["BodyText"]))
    content.append(Spacer(1, 0.05 * inch))
    content.append(Paragraph("Outcome", styles["Heading2"]))
    for line in outcome:
        content.append(Paragraph(f"• {line}", styles["BodyText"]))
    content.append(Spacer(1, 0.3 * inch))
    return content


def _build_document() -> list:
    styles = getSampleStyleSheet()
    styles["BodyText"].leading = 14
    story: list = []
    story.extend(_build_title(styles))
    story.extend(_build_overview(styles))
    story.extend(
        _build_table(
            "Data Sources",
            ["Source", "Details"],
            [
                [
                    "Authoritative Hazard Threat Assessment (AHTA)",
                    (
                        "PDF-based source listing hazards, threats, likelihood, impact, "
                        "mitigation maturity, and affected regions or assets."
                    ),
                ],
                [
                    "Asset Registry",
                    (
                        "Foundry ontology of infrastructure assets including location, "
                        "tier, system type, and dependencies."
                    ),
                ],
                [
                    "Operational Metrics (optional)",
                    "Service performance, downtime records, and maintenance schedules.",
                ],
            ],
            styles,
        )
    )
    story.extend(
        _build_table(
            "Foundry Objects and Integration Points",
            ["Object Type", "Name", "Function"],
            [
                ["Dataset", "AHTA_Parsed", "Structured hazards and threats extracted from PDFs."],
                ["Ontology Object", "Asset", "Represents each infrastructure component."],
                ["Ontology Relationship", "Asset_Has_Hazard", "Links assets to hazards or threats."],
                ["Transformation", "Risk_Scoring_Pipeline", "Calculates tier-weighted risk scores."],
                ["Application", "Risk_Simulation_App", "Runs hazard realization and mitigation simulations."],
                ["Workflow", "AHTA_Ingestion_Automation", "Parses new PDFs and proposes updates."],
            ],
            styles,
        )
    )
    story.extend(
        _build_table(
            "Inputs and Outputs",
            ["Process", "Inputs", "Outputs"],
            [
                [
                    "Dynamic Risk Scoring",
                    "Asset tier, hazard likelihood, impact, mitigation maturity",
                    "Asset-level risk scores with hazard contributions",
                ],
                [
                    "Scenario Simulation",
                    "Hazard parameters, mitigation levels",
                    "Per-asset and aggregate impact scores, resilience deltas",
                ],
                [
                    "PDF Extraction and Update",
                    "AHTA PDF",
                    "Parsed dataset, proposed risk score updates, audit log",
                ],
            ],
            styles,
        )
    )

    story.extend(
        _story_block(
            "User Story 1 — Enhanced Risk Scoring with Hazard and Threat Association",
            "Risk Analyst",
            (
                "the asset risk score to incorporate all associated hazards and threats from the "
                "AHTA, weighted by their tier relevance and exposure levels"
            ),
            "I can produce a comprehensive and dynamic view of each asset’s true risk posture.",
            (
                "Risk scores currently depend on tier level and manually loaded risk data. The "
                "enhanced calculation references AHTA hazards and threats, using likelihood, "
                "impact, exposure, and mitigation maturity to derive composite scores."
            ),
            [
                "Risk scores dynamically calculate from tier weight multiplied by the sum of linked hazards and threats.",
                "Formula uses (Likelihood × Impact × Exposure) × (1 − Mitigation_Maturity).",
                "Changes to AHTA records automatically refresh dependent asset scores.",
                "Risk breakdown is viewable by contributing hazards per asset.",
                "Data lineage is traceable between asset, hazard, and AHTA record within Foundry.",
            ],
            None,
            [
                "Risk assessments reflect current hazard intelligence.",
                "Mitigation work can be prioritized by actual exposure.",
                "Provides a foundation for scenario simulation and resilience planning.",
            ],
            styles,
        )
    )

    story.extend(
        _story_block(
            "User Story 2 — Hazard or Threat Impact Simulation and Mitigation Modeling",
            "Resilience Planner",
            (
                "to simulate both the realization of a hazard or threat and the effect of proposed "
                "mitigations"
            ),
            (
                "I can visualize how assets and regions are affected, evaluate mitigation "
                "effectiveness, and quantify changes to individual and aggregate risk scores."
            ),
            (
                "The scenario engine supports hazard realization to assess direct and cascading "
                "effects, plus mitigation modeling to evaluate how control changes alter risk "
                "scores and resilience indices."
            ),
            [
                "Users can define or import hazard scenarios (type, intensity, region, probability).",
                "Affected assets and dependencies are automatically identified.",
                "Service degradation and operational impact are calculated per asset.",
                "Users can modify mitigation variables to see quantitative effects.",
                "Output includes per-asset and aggregate risk score deltas.",
                "Visualization layer displays comparative before/after charts and maps.",
                "All simulation runs are saved with parameter metadata and results.",
            ],
            [
                "Flood in Region 3, severity 0.8: 12 assets impacted, average degradation 27%.",
                "Resilience index shifts from 0.74 to 0.59 before mitigation.",
                "Levee reinforcement plus sensors reduce aggregate risk by 0.11 (15%) and raise resilience to 0.68.",
            ],
            [
                "Quantifies hazard impacts and mitigation ROI.",
                "Supports comparisons of resilience improvement across assets and regions.",
                "Delivers actionable insight for investment prioritization and continuity planning.",
            ],
            styles,
        )
    )

    story.extend(
        _story_block(
            "User Story 3 — Automated AHTA Extraction and Risk Impact Updates (PDF Source)",
            "Risk Data Engineer",
            "to automatically extract and process AHTA data from PDF reports",
            "asset risk scores are continuously aligned with the latest hazard and threat intelligence.",
            (
                "Automated parsing of AHTA PDFs generates structured datasets, maps hazards to the "
                "asset ontology, and drafts risk updates for analyst approval."
            ),
            [
                "Foundry pipeline ingests PDF files from a defined source location.",
                "Hazard and threat data are parsed into structured AHTA tables.",
                "Assets are matched to hazards based on region, function, or metadata.",
                "Draft risk updates are computed and previewed before commit.",
                "Visualization compares current versus proposed risk scores.",
                "Audit log records all updates, user actions, and timestamps.",
            ],
            [
                "Analyst uploads AHTA_Q3.pdf into Foundry.",
                "Parser extracts a Severe Drought hazard affecting specific regions.",
                "System maps the hazard to eight assets using geospatial metadata and forecasts an average risk increase of 0.12.",
                "Analyst reviews, approves, and commits updates.",
            ],
            [
                "Reduces manual handling and accelerates hazard intelligence adoption.",
                "Maintains up-to-date asset risk profiles.",
                "Creates a continuous feedback loop between hazard intelligence and risk analytics.",
            ],
            styles,
        )
    )

    story.extend(
        _build_table(
            "Technical Implementation Notes",
            ["Component", "Description", "Technologies / Foundry Modules"],
            [
                [
                    "PDF Parsing",
                    "Extract structured data such as tables and key-value pairs from AHTA PDFs.",
                    "Foundry Code Workbook (Python), pdfplumber or PyMuPDF",
                ],
                [
                    "Entity Mapping",
                    "Link parsed hazards to asset ontology entries via metadata and geospatial joins.",
                    "Foundry Ontology Mapping Service",
                ],
                [
                    "Risk Scoring Logic",
                    "Apply tier-weighted hazard aggregation formula.",
                    "Transformation pipeline or Code Workbook",
                ],
                [
                    "Simulation Engine",
                    "Apply hazard parameters and mitigation variables to risk models.",
                    "Simulation app or Foundry Contour/Streamlit wrapper",
                ],
                [
                    "Visualization",
                    "Produce comparative dashboards for before/after analysis.",
                    "Foundry Contour dashboards, Palantir Workshop",
                ],
                [
                    "Automation and Scheduling",
                    "Detect and process new AHTA PDFs.",
                    "Foundry Scheduling and Event Hooks",
                ],
            ],
            styles,
        )
    )

    story.extend(
        _build_table(
            "Jira Hierarchy Overview",
            ["Level", "Key / Title", "Highlights"],
            [
                [
                    "Epic",
                    "INF-RISK-V2 – Integrated Risk Intelligence",
                    "Coordinates dynamic risk scoring, simulation, and PDF automation.",
                ],
                [
                    "Story",
                    "INF-RISK-V2-001 – Enhanced Risk Scoring",
                    "Subtasks: transformation pipeline, hazard links, tier aggregation, validation.",
                ],
                [
                    "Story",
                    "INF-RISK-V2-002 – Hazard or Threat Impact Simulation",
                    "Subtasks: simulation engine, mitigation modeling, risk deltas, dashboards, validation.",
                ],
                [
                    "Story",
                    "INF-RISK-V2-003 – Automated AHTA PDF Extraction",
                    "Subtasks: PDF ingestion, hazard mapping, draft updates, review interface, audit logging.",
                ],
            ],
            styles,
        )
    )

    story.extend(
        _build_table(
            "Deliverables",
            ["Deliverable", "Description"],
            [
                [
                    "Risk Scoring Model",
                    "Updated dataset and ontology relationships incorporating hazard-weighted scoring.",
                ],
                [
                    "Risk Simulation Application",
                    "Scenario and mitigation modeling interface for planners and analysts.",
                ],
                [
                    "AHTA Extraction Pipeline",
                    "Automated PDF ingestion with analyst review and audit logging.",
                ],
                [
                    "Documentation & Dashboards",
                    "Reference material and visualization assets demonstrating outputs.",
                ],
            ],
            styles,
        )
    )

    return story


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUTPUT_PATH), pagesize=LETTER)
    doc.build(_build_document())


if __name__ == "__main__":
    main()
