# Infrastructure Risk Radar

This Streamlit-based decision support tool estimates an infrastructure risk score for cities using
sample indicators (transport, utilities, water, healthcare, economic resilience, and preparedness).
Leaders can load the included dataset, tune the weighting of risk dimensions, and review
visualizations to prioritize investment.

## Features

- Ingests sample data for 12 cities across three countries.
- Calculates infrastructure, preparedness, economic, and population pressure gaps to build a
  composite risk score.
- Categorizes each city into Low/Moderate/High risk levels.
- Provides interactive tables and visualizations (risk ranking, scatter diagnostics, geographic view).
- Allows scenario analysis via adjustable weights for each risk pillar.

## Getting started

1. Create and activate a Python 3.9+ environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Launch the dashboard from the repository root:

   ```bash
   streamlit run app.py
   ```

4. When Streamlit finishes starting up, open the URL it prints in the terminal
   (by default http://localhost:8501/) in your web browser to interact with the
   application.

The app loads the sample dataset located at `data/city_infrastructure_sample.csv`.
You can replace it with your own file as long as you preserve the column names.

## Repository structure

```
├── app.py                         # Streamlit application entry point
├── data
│   └── city_infrastructure_sample.csv  # Sample infrastructure indicators
├── docs
│   ├── Infrastructure_Risk_Intelligence_v2.md   # Feature brief with user stories
│   └── Infrastructure_Risk_Intelligence_v2.pdf  # Printable version of the brief
├── README.md                      # Project documentation
├── requirements.txt               # Python dependencies
└── scripts
    └── generate_feature_brief_pdf.py            # Recreate the PDF from source data

## Documentation bundle

The `docs/Infrastructure_Risk_Intelligence_v2.md` file captures the Jira-ready
feature bundle discussed with the stakeholder, including three user stories,
acceptance criteria, technical notes, and deliverables. A PDF rendition is
checked in alongside it for immediate distribution. To regenerate the PDF after
editing the content or script, run:

```bash
pip install -r requirements.txt
python scripts/generate_feature_brief_pdf.py
```
```

## Committing your updates to GitHub

1. Check the repository status to confirm which files you have changed:

   ```bash
   git status
   ```

2. Stage the files you want to include in your commit. You can stage specific paths or everything that changed:

   ```bash
   git add <file-path-1> <file-path-2> ...
   ```

   To stage all tracked changes at once, use:

   ```bash
   git add -A
   ```

3. Create a commit with a concise message that summarizes the work:

   ```bash
   git commit -m "Describe your update"
   ```

4. If you have not already configured the GitHub repository as a remote, add it once (replace the URL with your repo):

   ```bash
   git remote add origin https://github.com/<your-org>/<your-repo>.git
   ```

   Verify the remote configuration when needed:

   ```bash
   git remote -v
   ```

5. Push the commit to GitHub. For a new repository, you may need to set the upstream branch:

   ```bash
   git push -u origin main
   ```

   On subsequent pushes you can omit `-u`:

   ```bash
   git push
   ```

These commands run from the project root (`/workspace/dev` in this environment). Update the branch name if you are using something other than `main`.
