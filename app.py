"""Streamlit dashboard for assessing national infrastructure risk across cities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import altair as alt
import pandas as pd
import streamlit as st

DATA_PATH = Path(__file__).parent / "data" / "city_infrastructure_sample.csv"


@dataclass
class RiskWeights:
    """Weights controlling the relative influence of each risk pillar."""

    infrastructure: float = 0.35
    preparedness: float = 0.25
    economic: float = 0.2
    population: float = 0.2

    @property
    def normalized(self) -> Dict[str, float]:
        total = self.infrastructure + self.preparedness + self.economic + self.population
        if total == 0:
            # Avoid division by zero and default to equal weights.
            return {key: 0.25 for key in ("infrastructure", "preparedness", "economic", "population")}
        return {
            "infrastructure": self.infrastructure / total,
            "preparedness": self.preparedness / total,
            "economic": self.economic / total,
            "population": self.population / total,
        }


def load_data() -> pd.DataFrame:
    """Load infrastructure indicators for each city."""

    df = pd.read_csv(DATA_PATH)
    return df


def compute_risk_scores(df: pd.DataFrame, weights: RiskWeights) -> pd.DataFrame:
    """Derive risk scores and supporting indicators for each city."""

    wt = weights.normalized
    infra_columns = [
        "road_quality_index",
        "power_grid_stability",
        "water_security",
        "healthcare_capacity",
    ]
    avg_infra_quality = df[infra_columns].mean(axis=1)
    infrastructure_gap = 100 - avg_infra_quality

    preparedness_gap = 100 - df["disaster_preparedness_score"]

    gdp_cap = df["gdp_per_capita_usd"].clip(lower=0)
    economic_vulnerability = ((60000 - gdp_cap).clip(lower=0) / 60000) * 100

    population_pressure = (df["population_millions"] / df["population_millions"].max()) * 100

    risk_score = (
        wt["infrastructure"] * infrastructure_gap
        + wt["preparedness"] * preparedness_gap
        + wt["economic"] * economic_vulnerability
        + wt["population"] * population_pressure
    )

    result = df.copy()
    result["avg_infrastructure_quality"] = avg_infra_quality
    result["infrastructure_gap"] = infrastructure_gap
    result["preparedness_gap"] = preparedness_gap
    result["economic_vulnerability"] = economic_vulnerability
    result["population_pressure"] = population_pressure
    result["risk_score"] = risk_score
    result["risk_level"] = pd.cut(
        risk_score,
        bins=[-1, 40, 60, 100],
        labels=["Low", "Moderate", "High"],
    )
    return result


def render_overview(df: pd.DataFrame) -> None:
    st.header("National Infrastructure Risk Overview")
    st.markdown(
        """
        This dashboard aggregates sample indicators describing the condition and resilience of
        critical infrastructure in major cities. Adjust the weighting sliders in the sidebar to
        simulate different policy priorities and observe how relative risk rankings evolve.
        """
    )

    summary_columns = ["risk_score", "avg_infrastructure_quality", "disaster_preparedness_score"]
    summary = df.groupby("country")[summary_columns].mean().reset_index()
    summary_chart = (
        alt.Chart(summary)
        .transform_fold(summary_columns, as_=["metric", "value"])
        .mark_bar()
        .encode(
            x=alt.X("country:N", title="Country"),
            y=alt.Y("value:Q", title="Score"),
            color=alt.Color("metric:N", title="Metric"),
            column=alt.Column("metric:N", title=""),
            tooltip=["country", "metric", alt.Tooltip("value", format=".1f")],
        )
        .properties(width=120)
    )
    st.altair_chart(summary_chart, use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Highest risk city", df.loc[df["risk_score"].idxmax(), "city"])
        st.metric(
            "Average risk score",
            f"{df['risk_score'].mean():.1f}",
            delta=f"±{df['risk_score'].std():.1f} (stdev)",
        )
    with col2:
        st.metric("Most resilient city", df.loc[df["risk_score"].idxmin(), "city"])
        st.metric("Cities assessed", len(df))


def render_city_table(df: pd.DataFrame) -> None:
    st.subheader("City level diagnostics")
    columns = [
        "city",
        "country",
        "region",
        "risk_score",
        "risk_level",
        "avg_infrastructure_quality",
        "disaster_preparedness_score",
        "economic_vulnerability",
        "population_pressure",
    ]
    st.dataframe(
        df[columns]
        .sort_values("risk_score", ascending=False)
        .style.format(
            {
                "risk_score": "{:.1f}",
                "avg_infrastructure_quality": "{:.1f}",
                "disaster_preparedness_score": "{:.0f}",
                "economic_vulnerability": "{:.0f}",
                "population_pressure": "{:.0f}",
            }
        )
    )


def render_visuals(df: pd.DataFrame) -> None:
    st.subheader("Visual analytics")

    risk_chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("city:N", sort="-y", title="City"),
            y=alt.Y("risk_score:Q", title="Risk score"),
            color=alt.Color("risk_level:N", title="Risk level"),
            tooltip=["city", "country", alt.Tooltip("risk_score", format=".1f"), "risk_level"],
        )
    )
    st.altair_chart(risk_chart, use_container_width=True)

    scatter = (
        alt.Chart(df)
        .mark_circle(size=120, opacity=0.7)
        .encode(
            x=alt.X("avg_infrastructure_quality:Q", title="Average infrastructure quality"),
            y=alt.Y("risk_score:Q", title="Risk score"),
            color=alt.Color("country:N", title="Country"),
            tooltip=[
                "city",
                "country",
                alt.Tooltip("avg_infrastructure_quality", format=".1f"),
                alt.Tooltip("risk_score", format=".1f"),
                alt.Tooltip("population_millions", title="Population (M)", format=".1f"),
            ],
        )
    )
    st.altair_chart(scatter, use_container_width=True)

    geo = (
        alt.Chart(df)
        .mark_circle(size=160, opacity=0.6)
        .encode(
            longitude="longitude:Q",
            latitude="latitude:Q",
            color=alt.Color("risk_level:N", title="Risk level"),
            size=alt.Size("risk_score:Q", title="Risk score"),
            tooltip=["city", "country", alt.Tooltip("risk_score", format=".1f"), "risk_level"],
        )
        .project(type="mercator")
    )
    st.altair_chart(geo, use_container_width=True)


def render_sidebar(df: pd.DataFrame) -> RiskWeights:
    st.sidebar.header("Scenario assumptions")
    st.sidebar.markdown(
        "Adjust the weights to emphasize different policy priorities. The risk scores are"
        " recalculated instantly."
    )

    infrastructure = st.sidebar.slider(
        "Infrastructure condition weight", min_value=0.0, max_value=1.0, value=0.35, step=0.05
    )
    preparedness = st.sidebar.slider(
        "Disaster preparedness weight", min_value=0.0, max_value=1.0, value=0.25, step=0.05
    )
    economic = st.sidebar.slider(
        "Economic resilience weight", min_value=0.0, max_value=1.0, value=0.2, step=0.05
    )
    population = st.sidebar.slider(
        "Population pressure weight", min_value=0.0, max_value=1.0, value=0.2, step=0.05
    )

    st.sidebar.markdown("---")
    st.sidebar.metric(
        "Total population assessed",
        f"{df['population_millions'].sum():.1f} million residents",
    )

    return RiskWeights(
        infrastructure=infrastructure,
        preparedness=preparedness,
        economic=economic,
        population=population,
    )


def main() -> None:
    st.set_page_config(page_title="Infrastructure Risk Radar", layout="wide")
    st.title("Infrastructure Risk Radar")

    df = load_data()
    weights = render_sidebar(df)

    scores = compute_risk_scores(df, weights)
    render_overview(scores)
    render_city_table(scores)
    render_visuals(scores)


if __name__ == "__main__":
    main()
