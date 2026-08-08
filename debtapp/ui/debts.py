"""My debts — the data-entry page. Edits autosave."""

from __future__ import annotations

from dataclasses import fields

import pandas as pd
import streamlit as st

from .. import engine as E
from ..models import ACCOUNT_TYPES, Debt, kind_for, type_of
from .common import (banner, caption, current_user, money, page_header, persist, section,
                     stat_row, toast)

# One grid, not two. Splitting cards from loans made the user classify a debt
# before entering it, and the classification is ours — see `models.ACCOUNT_TYPES`.
#
# "id" rides along hidden (column_config maps it to None). It is what keeps an
# account's logged payment history attached to it across renames and edits.
# "type" is synthetic: it is `kind` and `subtype` folded into one question.
BASIC_COLS = ["id", "name", "type", "balance", "apr", "current_payment"]
DETAIL_COLS = ["min_payment", "min_percent", "credit_limit", "term_months", "due_day"]
COLS = BASIC_COLS + DETAIL_COLS

# Everything in COLS that is a field on Debt — i.e. all of it but the synthetic
# "type" and the "name" handled separately.
_MODEL_COLS = [c for c in COLS if c not in ("name", "type")]
_INT_COLS = ("term_months", "due_day")
# A blank cell means "I didn't say", which is the dataclass default — not zero.
_DEFAULTS = {f.name: f.default for f in fields(Debt)}


def _to_df(debts: list[Debt]) -> pd.DataFrame:
    rows = [{c: (type_of(d) if c == "type" else getattr(d, c)) for c in COLS} for d in debts]
    return pd.DataFrame(rows, columns=COLS)


def _seed(debts: list[Debt], key: str) -> pd.DataFrame:
    """The frame to hand the editor — deliberately the *same* one on every rerun.

    ``st.data_editor`` hashes the data it is given into its own widget id, so
    handing it back the rows we saved a moment ago makes it a *different*
    widget: the grid remounts, and the cell the user has already started typing
    arrives labelled with an id that no longer exists and is thrown away. That
    is the "saves one cell, eats the next one" bug, and it fires on every edit,
    because every edit is autosaved back into the rows below.

    The editor holds its edits as a delta against this frame, so it only wants a
    new one when it has no delta to lose — a first render, or a return to the
    page after Streamlit binned its state — or when the rows moved underneath
    it, which the callers that can do that announce by bumping ``debts_rev``,
    and which shows up here as a widget key it has not seen before. Toggling the
    detail columns changes the key too, which is safe for the same reason: every
    edit is already autosaved by the time the toggle can be clicked.
    """
    stash = st.session_state.get("_seed_debts")
    if stash is None or stash[0] != key or key not in st.session_state:
        stash = (key, _to_df(debts))
        st.session_state["_seed_debts"] = stash
    return stash[1]


def _from_df(df: pd.DataFrame) -> list[Debt]:
    out = []
    for _, r in df.iterrows():
        name = str(r.get("name") or "").strip()
        if not name:
            continue  # a blank row the user hasn't filled in yet
        raw_type = r.get("type")
        kind, subtype = kind_for(str(raw_type) if pd.notna(raw_type) else "Credit card")
        vals = {"name": name, "kind": kind, "subtype": subtype}
        for c in _MODEL_COLS:
            v = r.get(c)
            if pd.isna(v):
                # The default, not 0.0. `min_percent` is why this matters: it
                # defaults to 2%, it *is* the minimum-payment model for a card
                # (`Debt.required_payment`), and coercing a blank cell to zero
                # silently removed the floor from every card added after the
                # first — flattering every projection downstream with a minimum
                # no lender would accept.
                vals[c] = _DEFAULTS[c]
            elif c == "id":
                # Blank on rows the user just added — those become new records.
                vals[c] = int(v)
            elif c in _INT_COLS:
                vals[c] = int(v)
            else:
                vals[c] = float(v)
        out.append(Debt(**vals))
    return out


def _label(d: Debt) -> str:
    return f"{d.name} — {type_of(d)}, {money(d.balance)}"


def _apply(uid: int, debts: list[Debt], profile) -> None:
    """Save a change made by a button rather than by typing in a table, then
    start the page over from what was saved.

    The rerun is not optional. Both editors below hold their pending edits keyed
    by *row position*, so removing a row underneath them would re-apply an old
    edit to whatever slid up into that slot. Bumping the revision gives them
    fresh widget keys, which is the only way to drop that state — and returning
    here rather than falling through skips the autosave at the bottom of the
    page, which would otherwise write the pre-click rows straight back.
    """
    persist(uid, debts, profile)
    st.session_state.debts_rev = st.session_state.get("debts_rev", 0) + 1
    st.session_state.pop("_saved_sig", None)
    st.session_state.pop("_confirm_remove", None)
    st.rerun()


def _manage(uid: int, debts: list[Debt], profile) -> None:
    """Whole-account actions, spelled out.

    The checkboxes down the left of the table above drive Streamlit's own
    toolbar, which deletes a row the moment you click it. That is fine for a
    blank row you added by accident and much too quiet for closing an account
    you have payment history against — and it cannot express "this one is paid
    off" at all, which is the thing people actually reach for. Both of those are
    decisions about an account rather than edits to a cell, so they live here
    where the consequences fit on screen.
    """
    if not debts:
        return

    section("Paid one off? Added one by mistake?",
            "Pick the accounts and say what happened to them. Marking one paid off keeps it "
            "and its history but drops it out of every projection; removing it deletes the "
            "account.")

    rev = st.session_state.get("debts_rev", 0)
    picked = st.multiselect(
        "Accounts to act on", options=list(range(len(debts))),
        format_func=lambda i: _label(debts[i]), key=f"manage_pick_{rev}",
        label_visibility="collapsed", placeholder="Choose one or more accounts",
    )

    if picked:
        chosen = [debts[i] for i in picked]
        caption(f"{len(chosen)} selected · {money(sum(d.balance for d in chosen))} owed · "
                f"{money(sum(d.effective_payment() for d in chosen))}/mo of your budget")
    else:
        # Say what the tick boxes in the tables do, since they look like they
        # should feed this box and don't.
        caption("Nothing selected. The tick boxes in the table above work too, but the bin "
                "icon there deletes straight away — none of the warnings you get here.")

    c1, c2, _ = st.columns([1, 1, 2])
    paid = c1.button("Mark as paid off", key="mark_paid", width="stretch", disabled=not picked,
                     help="Sets the balance and the payment to zero. The account stays on "
                          "this page and in your Ledger.")
    if c2.button("Remove from my list", key="ask_remove", width="stretch", disabled=not picked):
        st.session_state._confirm_remove = True
        st.rerun()

    if paid:
        for i in picked:
            debts[i].balance = 0.0
            debts[i].current_payment = 0.0   # else it still counts against your budget
        toast(f"Nice — {len(picked)} account{'s' if len(picked) != 1 else ''} paid off.")
        _apply(uid, debts, profile)

    if not (picked and st.session_state.get("_confirm_remove")):
        st.session_state.pop("_confirm_remove", None)
        return

    # Read off `picked` rather than off what was selected when the button was
    # clicked, so editing the selection while this is open changes what goes.
    names = [debts[i].name for i in picked]
    banner("warning",
           f"Remove **{'**, **'.join(names)}**? This deletes the account and cannot be undone. "
           "Any payments you logged against it stay in your Ledger, so what it has already "
           "cost you is still counted.")
    d1, d2, _ = st.columns([1, 1, 2])
    if d1.button("Yes, remove", key="do_remove", type="primary", width="stretch"):
        gone = set(picked)
        removed = {debts[i].name for i in gone}
        profile.custom_order = [n for n in (profile.custom_order or []) if n not in removed]
        toast(f"Removed {len(removed)} account{'s' if len(removed) != 1 else ''}.")
        _apply(uid, [d for i, d in enumerate(debts) if i not in gone], profile)
    if d2.button("Keep them", key="cancel_remove", width="stretch"):
        st.session_state.pop("_confirm_remove", None)
        st.rerun()


_MONEY = dict(format="dollar", min_value=0.0, step=25.0)


def _columns(details: bool) -> dict:
    """Column config for the grid. Hidden columns are still *present* in the
    frame — mapping one to ``None`` hides it without dropping the value, which
    is what keeps a minimum you set last month from being wiped by a session
    where you never opened the details."""
    cfg = {
        "id": None,
        "name": st.column_config.TextColumn("Account", required=True, width="medium"),
        "type": st.column_config.SelectboxColumn(
            "Type", options=ACCOUNT_TYPES, default="Credit card", required=True,
            help="Credit cards have a minimum that shrinks as the balance does. Everything "
                 "else has a fixed payment."),
        "balance": st.column_config.NumberColumn("Balance", **_MONEY),
        "apr": st.column_config.NumberColumn("APR", format="%.2f%%", min_value=0.0,
                                             max_value=99.0, step=0.25),
        "current_payment": st.column_config.NumberColumn(
            "You pay now", help="What you actually send each month.", **_MONEY),
        "min_payment": st.column_config.NumberColumn(
            "Minimum", help="On a card, the dollar floor — usually $25–$40; the percentage "
                            "beside it applies too. On a loan, the contractual payment — leave "
                            "it blank and we'll derive it from the months left.",
            format="dollar", min_value=0.0, step=5.0),
        "min_percent": st.column_config.NumberColumn(
            "Min % (cards)", help="Percentage of the balance the card requires — usually 1–3%. "
                                  "Blank means 2%.", format="%.1f%%",
            min_value=0.0, max_value=25.0, step=0.5),
        "credit_limit": st.column_config.NumberColumn(
            "Credit limit (cards)", help="Used for utilization only. Leave blank to skip.",
            **_MONEY),
        "term_months": st.column_config.NumberColumn(
            "Months left (loans)", help="Only used if you leave the payment blank.",
            min_value=0, max_value=480, step=1),
        "due_day": st.column_config.NumberColumn(
            "Due day", help="Day of the month this payment is due (1–31). Set it and the app "
                            "will tell you what's coming and ask whether you've paid.",
            min_value=0, max_value=31, step=1, format="%d"),
    }
    if not details:
        for c in DETAIL_COLS:
            cfg[c] = None
    return cfg


def render() -> None:
    uid = current_user()
    debts: list[Debt] = st.session_state.debts
    profile = st.session_state.profile

    page_header("My debts",
                "Everything here saves automatically. Come back any time and your numbers "
                "will be waiting.")

    section("Your accounts",
            "One row each. A name, a balance, the rate and what you pay is enough to project "
            "every number in the app — the rest sharpens it and can wait.")

    details = st.toggle(
        "Show payment details", key="debt_details",
        help="Minimums, credit limits, remaining terms and due dates. Hiding them changes "
             "nothing you've already entered.")

    # The grid is keyed by revision so a button below — or a payment logged on
    # the Ledger — can throw its pending edits away. See `_apply` and `_seed`.
    rev = st.session_state.get("debts_rev", 0)
    grid_key = f"ed_debts_{rev}_{'full' if details else 'basic'}"

    edited = st.data_editor(
        _seed(debts, grid_key), num_rows="dynamic", width="stretch", hide_index=True,
        key=grid_key, column_config=_columns(details),
    )
    if not details:
        caption("Minimums, credit limits and due dates are hidden. The projections use sensible "
                "defaults until you set them.")

    new_debts = _from_df(edited)

    # ------------------------------------------------------ account actions
    # After the editors, so it acts on what is on screen right now rather than
    # on what was last written to the database.
    _manage(uid, new_debts, profile)

    # ------------------------------------------------------------- the budget
    section("Your monthly plan",
            "One budget drives every projection in the app, so the differences between "
            "strategies come from ordering alone.")
    min_budget = E.minimum_budget(new_debts) if new_debts else 0.0
    cur_budget = E.current_budget(new_debts) if new_debts else 0.0

    budget = st.number_input(
        "Total you can put toward debt each month", min_value=0.0, step=25.0,
        value=float(profile.monthly_budget or cur_budget or min_budget),
        help="Every strategy is compared at this same budget, so the differences you see "
             "come from ordering alone.",
    )

    strategy = st.radio(
        "Payoff strategy", [E.AVALANCHE, E.SNOWBALL, E.CUSTOM],
        format_func=lambda k: E.STRATEGY_LABELS[k],
        index=[E.AVALANCHE, E.SNOWBALL, E.CUSTOM].index(
            profile.strategy if profile.strategy in (E.AVALANCHE, E.SNOWBALL, E.CUSTOM)
            else E.AVALANCHE),
        horizontal=True, captions=[E.STRATEGY_BLURBS[k] for k in
                                   (E.AVALANCHE, E.SNOWBALL, E.CUSTOM)],
    )

    custom_order = list(profile.custom_order or [])
    if strategy == E.CUSTOM and new_debts:
        names = [d.name for d in new_debts]
        seed = [n for n in custom_order if n in names] + [n for n in names if n not in custom_order]
        custom_order = st.multiselect(
            "Attack in this order (first gets every spare dollar)", options=names, default=seed,
            help="Anything you leave out gets its minimum payment only.",
        )

    if new_debts:
        if budget < min_budget:
            banner("error",
                   f"Your budget of {money(budget)} is below the **{money(min_budget)}** your "
                   "lenders require. Projections below assume payments get pro-rated, which in "
                   "reality means late fees and penalty APRs.")
        stat_row([
            ("Total owed", money(sum(d.balance for d in new_debts))),
            ("Required minimums", f"{money(min_budget)}/mo"),
            ("Your budget", f"{money(budget)}/mo"),
            ("Extra above minimums", f"{money(max(0, budget - min_budget))}/mo",
             "This is the money doing the real work", "good" if budget > min_budget else ""),
        ])
    else:
        st.info("Add an account above to get started.")

    # Income and savings drive two suggestions apiece and nothing else, so they
    # sat on the app's busiest page charging every user for a feature most never
    # reach. Folded away, not deleted — the suggestions they unlock are good.
    with st.expander("Income and savings (optional)"):
        caption("Neither figure changes a projection. They let the app say whether your debt "
                "load is survivable, and whether to build a cushion before overpaying.")
        c1, c2 = st.columns(2)
        income = c1.number_input("Monthly take-home income", min_value=0.0, step=100.0,
                                 value=float(profile.monthly_income),
                                 help="Used for debt-to-income suggestions. Leave 0 to skip.")
        fund = c2.number_input("Emergency fund", min_value=0.0, step=100.0,
                               value=float(profile.emergency_fund),
                               help="Cash on hand. Changes what we recommend you do first.")

    # ---------------------------------------------------------------- autosave
    profile.monthly_budget = budget
    profile.monthly_income = income
    profile.emergency_fund = fund
    profile.strategy = strategy
    profile.custom_order = custom_order

    def signature() -> str:
        return repr(([d.to_dict() for d in new_debts], profile.__dict__.copy()))

    if st.session_state.get("_saved_sig") != signature():
        persist(uid, new_debts, profile)
        # Recomputed *after* the write: saving stamps ids onto brand-new rows, and
        # signing the pre-write state would make the next rerun look dirty again.
        st.session_state._saved_sig = signature()
    caption("✓ Saved")
