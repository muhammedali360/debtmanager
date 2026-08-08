# Debt Manager

A Streamlit app for people carrying debt: enter your cards and loans, see exactly
where the money is going, and find out what it would actually take to get out.

## What it does

- **Two debt shapes, modelled properly.** Credit cards use a percent-of-balance
  minimum with a dollar floor — so the minimum *shrinks as you pay*, which is the
  trap. Term loans use a fixed contractual payment.
- **A real amortization engine.** One code path (`debtapp/engine.py`) produces
  every number in the app, so the dashboard, insights, and scenarios can never
  disagree. Validated against closed-form amortization tables.
- **Visualizations**: balance projection, cumulative principal vs interest, the
  cents-on-the-dollar split, interest by year, payoff timeline, per-account
  interest, and a strategy bake-off.
- **~20 quantified insights**, ranked by dollars at stake — balance transfers net
  of fees, consolidation, utilization and its credit-score cost, debt-to-income,
  biweekly payments, windfall timing, the cost of waiting six months.
- **What-if sandbox**: extra payments, lump sums, annual raises, and a solver
  that works backwards from a target debt-free date to the payment it requires.
- **Accounts and persistence** so you can come back and pick up where you left
  off, plus a progress chart across check-ins.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Create an account on first load. Tick "start with example debts" to get a
realistic portfolio you can edit.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

65 tests: engine math against closed-form answers, insight correctness, and
end-to-end UI tests that drive every page through Streamlit's `AppTest`
(including empty accounts, zero-APR loans, and negative-amortization plans).

## Persistence and deployment

Data lives in SQLite at `data/debt.db`. Override with the `DEBTMANAGER_DB`
environment variable.

> **Deploying to Streamlit Community Cloud:** the container filesystem is
> **ephemeral** — it is wiped on every reboot and redeploy, so `data/debt.db`
> will not survive. Point `DEBTMANAGER_DB` at a mounted volume, or switch
> `debtapp/db.py` to a hosted Postgres (it is the only module that touches
> storage). Everything else works unchanged.

## Design notes

- **Money math** is monthly-compounded and rounded to the cent each month, the
  way a servicer actually posts. A 60-month loan can therefore end with a
  sub-dollar 61st stub payment — that is correct, not a bug.
- **Insights never quote a savings figure against a plan that never terminates.**
  If minimum payments would never clear the debt, the app says so and reports
  what you'd have paid and still owe, rather than inventing a number from
  wherever the simulation stopped.
- **Charts** follow a validated palette (colorblind-safe, checked in both light
  and dark against their own surfaces). Every chart ships a table view, a legend
  for two or more series, and selective direct labels rather than a number on
  every point.

## Layout

```
app.py                 entry point, auth gate, navigation
debtapp/
  models.py            Debt / Profile, amortization formula
  engine.py            the simulation — the only place a month is defined
  insights.py          ranked, quantified advice
  charts.py            Plotly figures
  theme.py             validated palette + Plotly template
  db.py                SQLite persistence, bcrypt auth, sessions
  ui/                  one module per page
tests/                 engine, insight, and end-to-end UI tests
```

## Disclaimer

A planning tool, not financial advice. Projections assume unchanging APRs, no
new borrowing, and monthly compounding. If your minimum payments are
unaffordable, a nonprofit credit counselor (NFCC member agencies are free) will
help more than any calculator.
