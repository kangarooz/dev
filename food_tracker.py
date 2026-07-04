"""A lightweight food tracking tab for the Streamlit dashboard.

Users can log meals with their calorie counts, review the running log, and see
daily calorie totals. Entries are persisted to a small CSV so the log survives
across sessions.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

FOOD_LOG_PATH = Path(__file__).parent / "data" / "food_log.csv"

FOOD_LOG_COLUMNS = ["date", "meal", "food", "calories"]

MEAL_OPTIONS = ["Breakfast", "Lunch", "Dinner", "Snack"]


def load_food_log() -> pd.DataFrame:
    """Load the persisted food log, returning an empty frame if none exists."""

    if FOOD_LOG_PATH.exists():
        df = pd.read_csv(FOOD_LOG_PATH)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    return pd.DataFrame(columns=FOOD_LOG_COLUMNS)


def save_food_log(df: pd.DataFrame) -> None:
    """Persist the food log to disk."""

    FOOD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(FOOD_LOG_PATH, index=False)


def add_entry(df: pd.DataFrame, entry_date: date, meal: str, food: str, calories: float) -> pd.DataFrame:
    """Append a single food entry and return the updated log."""

    new_row = pd.DataFrame(
        [{"date": entry_date, "meal": meal, "food": food, "calories": calories}]
    )
    return pd.concat([df, new_row], ignore_index=True)


def render_food_tracker() -> None:
    """Render the food tracking tab."""

    st.header("Food Tracking")
    st.markdown(
        "Log what you eat and keep an eye on daily calories. Entries are saved"
        " automatically so your log is here when you come back."
    )

    log = load_food_log()

    with st.form("food_entry", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            entry_date = st.date_input("Date", value=date.today())
            meal = st.selectbox("Meal", MEAL_OPTIONS)
        with col2:
            food = st.text_input("Food")
            calories = st.number_input("Calories", min_value=0.0, step=50.0, value=0.0)

        submitted = st.form_submit_button("Add entry")

    if submitted:
        if not food.strip():
            st.warning("Enter a food name before adding an entry.")
        else:
            log = add_entry(log, entry_date, meal, food.strip(), calories)
            save_food_log(log)
            st.success(f"Logged {food.strip()} ({calories:.0f} kcal).")

    if log.empty:
        st.info("No food logged yet. Add your first entry above.")
        return

    today_total = log.loc[log["date"] == date.today(), "calories"].sum()
    col1, col2 = st.columns(2)
    col1.metric("Calories today", f"{today_total:.0f} kcal")
    col2.metric("Entries logged", len(log))

    st.subheader("Food log")
    st.dataframe(
        log.sort_values("date", ascending=False).style.format({"calories": "{:.0f}"}),
        use_container_width=True,
    )

    st.subheader("Daily calories")
    daily = log.groupby("date", as_index=False)["calories"].sum()
    calorie_chart = (
        alt.Chart(daily)
        .mark_bar()
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("calories:Q", title="Calories"),
            tooltip=["date", alt.Tooltip("calories", format=".0f")],
        )
    )
    st.altair_chart(calorie_chart, use_container_width=True)
