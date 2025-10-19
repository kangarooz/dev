# Infrastructure Risk Intelligence Enhancement Package

**Initiative:** Infrastructure Risk Modernization  
**Environment:** Palantir Foundry  
**Version:** v2  
**Stakeholders:** Mission Analysis, Regional Operations, Risk Data Engineering

## Overview
This release introduces an integrated risk intelligence capability within Foundry that allows mission analysts and resilience planners to:

1. Calculate dynamic risk scores for infrastructure assets based on both tier criticality and associated hazards and threats.
2. Simulate hazard or threat events and mitigation scenarios to quantify impacts on assets and regions.
3. Automatically extract and process Authoritative Hazard Threat Assessment (AHTA) data from PDF sources to keep risk scores continuously aligned with the latest intelligence.

## Data Sources
- **Authoritative Hazard Threat Assessment (AHTA):** PDF-based source that enumerates hazards, threats, likelihood, impact, mitigation maturity, and affected regions or assets.
- **Asset Registry:** Foundry ontology of infrastructure assets including metadata such as location, tier, system type, and dependencies.
- **Operational Metrics (optional):** Service performance, downtime records, maintenance schedules.

## Foundry Objects and Integration Points
| Object Type | Name | Function |
| --- | --- | --- |
| Dataset | `AHTA_Parsed` | Structured table of hazards and threats extracted from AHTA PDFs. |
| Ontology Object | `Asset` | Represents each physical or digital infrastructure component. |
| Ontology Relationship | `Asset_Has_Hazard` | Links each asset to its relevant hazards or threats. |
| Transformation | `Risk_Scoring_Pipeline` | Computes tier-weighted, hazard-adjusted risk scores. |
| Application | `Risk_Simulation_App` | Enables users to run hazard realization and mitigation simulations. |
| Workflow | `AHTA_Ingestion_Automation` | Monitors for new PDFs, parses, maps, and proposes updates. |

## Inputs and Outputs
| Process | Inputs | Outputs |
| --- | --- | --- |
| Dynamic Risk Scoring | Asset tier, hazard likelihood/impact/mitigation | Asset-level risk score and contributing hazard table |
| Scenario Simulation | Hazard parameters (type, region, intensity), mitigation levels | Per-asset and aggregate impact scores, resilience index deltas |
| PDF Extraction and Update | AHTA PDF | Parsed AHTA dataset, proposed risk score updates, change audit log |

## User Story 1 — Enhanced Risk Scoring with Hazard and Threat Association
**Source:** Infrastructure Risk Modernization Initiative  
**Requested By:** Mission Analyst Team

**As a** Risk Analyst  
**I want** the asset risk score to incorporate all associated hazards and threats from the AHTA, weighted by their tier relevance and exposure levels  
**So that** I can produce a comprehensive and dynamic view of each asset’s true risk posture.

### Description
Risk scores are currently based only on tier level and manually loaded risk data. This enhancement introduces a dynamic scoring model that references all hazard and threat data from the AHTA, using likelihood, impact, exposure, and mitigation maturity to compute an asset’s composite risk score.

### Acceptance Criteria
- Risk scores dynamically calculate from tier weight × sum of all linked hazards and threats.
- Formula uses: (Likelihood × Impact × Exposure) × (1 − Mitigation_Maturity).
- Changes to AHTA records automatically refresh dependent asset scores.
- Risk breakdown is viewable by contributing hazards per asset.
- Data lineage is traceable between asset, hazard, and AHTA record within Foundry.

### Outcome
- Risk assessments reflect real-time hazard intelligence.
- Improved prioritization for mitigation based on exposure.
- Foundational data for scenario simulation and resilience planning.

## User Story 2 — Hazard or Threat Impact Simulation and Mitigation Modeling
**Source:** Mission Analysis and Resilience Planning Team  
**Requested By:** Regional Operations

**As a** Resilience Planner  
**I want** to simulate both the realization of a hazard or threat and the effect of proposed mitigations  
**So that** I can visualize how assets and regions are affected, evaluate mitigation effectiveness, and quantify changes to individual and aggregate risk scores.

### Description
This feature adds dual-mode scenario capability within Foundry:

- **Hazard Realization Mode:** Applies a hazard scenario to assess immediate and cascading effects across assets.
- **Mitigation Modeling Mode:** Simulates the impact of mitigation adjustments on risk scores and resilience indices.

### Acceptance Criteria
- Users can define or import hazard scenarios (type, intensity, region, probability).
- Affected assets and dependencies are automatically identified.
- Service degradation and operational impact are calculated per asset.
- Users can modify mitigation variables to see their quantitative effects.
- Output includes per-asset and aggregate risk score deltas.
- Visualization layer displays comparative before/after charts and maps.
- All simulation runs are saved with parameter metadata and results.

### Example Simulation
Scenario: Flood — Region 3, Severity 0.8

- 12 assets impacted (including 2 Tier 1).
- Average degradation: 27%.
- Regional resilience index drops from 0.74 to 0.59.
- Proposed mitigation (levee reinforcement plus sensor network):
  - Average risk reduction per asset: −0.009.
  - Aggregate risk reduction: −0.11 (15%).
  - Resilience index improves to 0.68.

### Outcome
- Quantifies hazard impacts and mitigation ROI.
- Enables cross-asset and regional comparisons of resilience improvement.
- Provides actionable insight for investment prioritization and continuity planning.

## User Story 3 — Automated AHTA Extraction and Risk Impact Updates (PDF Source)
**Source:** Mission Data Engineering Team  
**Requested By:** Risk Intelligence Lead

**As a** Risk Data Engineer  
**I want** to automatically extract and process AHTA data from PDF reports  
**So that** asset risk scores are continuously aligned with the latest hazard and threat intelligence.

### Description
This automation parses AHTA PDFs into structured datasets, identifies relevant hazards and mitigations, and maps them to existing assets in the ontology. The pipeline drafts proposed updates to risk scores and provides a review interface for analysts to approve or reject changes.

### Acceptance Criteria
- Foundry pipeline ingests PDF files from a defined source location.
- Hazard and threat data are parsed into structured AHTA tables.
- Assets are matched to hazards based on region, function, or metadata.
- Draft risk updates are computed and previewed before commit.
- Visualization compares current versus proposed risk scores.
- Audit log records all updates, user actions, and timestamps.

### Example Flow
1. Analyst uploads `AHTA_Q3.pdf` into the Foundry workspace.
2. Parser extracts a "Severe Drought" hazard affecting specific regions.
3. System maps the hazard to eight assets using geospatial metadata.
4. Draft update shows an average risk score increase of +0.12 across those assets.
5. Analyst reviews, approves, and commits updates.

### Outcome
- Reduces manual data handling and delays between AHTA publication and operational use.
- Maintains up-to-date asset risk profiles.
- Establishes a continuous feedback loop between hazard intelligence and risk analytics.

## Technical Implementation Notes
| Component | Description | Technologies / Foundry Modules |
| --- | --- | --- |
| PDF Parsing | Extract structured data (tables, key-value pairs) from AHTA PDFs. | Foundry Code Workbook (Python), pdfplumber or PyMuPDF |
| Entity Mapping | Link parsed hazards to asset ontology entries. | Foundry Ontology Mapping Service, geospatial joins |
| Risk Scoring Logic | Apply tier-weighted hazard aggregation formula. | Transformation pipeline or Code Workbook |
| Simulation Engine | Apply hazard parameters and mitigation variables to risk model. | Simulation app or Foundry Contour/Streamlit wrapper |
| Visualization | Comparative dashboards for before/after analysis. | Foundry Contour dashboards, Palantir Workshop |
| Automation and Scheduling | Detect and process new AHTA PDFs. | Foundry Scheduling and Event Hooks |

## Jira Hierarchy Overview
- **Epic:** INF-RISK-V2 – Integrated Risk Intelligence
  - **Story:** INF-RISK-V2-001 – Enhanced Risk Scoring with Hazard and Threat Association
    - Subtasks: Transformation pipeline; Link hazards to assets; Tier-weighted aggregation; Validation.
  - **Story:** INF-RISK-V2-002 – Hazard or Threat Impact Simulation and Mitigation Modeling
    - Subtasks: Simulation engine; Mitigation modeling; Risk score delta computation; Comparative dashboards; Simulation validation.
  - **Story:** INF-RISK-V2-003 – Automated AHTA PDF Extraction and Risk Impact Updates
    - Subtasks: PDF ingestion and parsing; Hazard-to-asset mapping; Draft risk score updates; Analyst review interface; Audit logging.

## Deliverables
- Updated asset risk scoring model dataset and ontology relationships.
- New risk simulation application for scenario and mitigation modeling.
- Automated AHTA PDF extraction pipeline with analyst review interface.
- Documentation and sample dashboards showing simulation and scoring outputs.

