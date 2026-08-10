"""Insight generation.

Every insight is quantified in dollars or months — nothing here says "consider
paying more" without telling you exactly what it buys. Insights are ranked by
``stake`` (money on the table) so the most consequential advice sorts first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence, Union

from . import engine as E
from . import payments as P
from .models import Debt, Payment, Profile, amortized_payment

CRITICAL, SERIOUS, WARNING, GOOD, INFO = "critical", "serious", "warning", "good", "info"

SEVERITY_RANK = {CRITICAL: 0, SERIOUS: 1, WARNING: 2, GOOD: 3, INFO: 4}
SEVERITY_ICON = {CRITICAL: "🛑", SERIOUS: "🔥", WARNING: "⚠️", GOOD: "✅", INFO: "💡"}
SEVERITY_LABEL = {CRITICAL: "Critical", SERIOUS: "Serious", WARNING: "Warning",
                  GOOD: "Good news", INFO: "Opportunity"}


# ------------------------------------------------------------------ formatting

def money(x: Optional[float], cents: bool = False) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def duration(months: Optional[int]) -> str:
    if months is None:
        return "never"
    y, m = divmod(int(months), 12)
    if y and m:
        return f"{y} yr {m} mo"
    if y:
        return f"{y} yr"
    return f"{m} mo"


@dataclass
class Insight:
    severity: str
    title: str
    body: str
    action: Optional[str] = None
    metric: Optional[str] = None
    stake: float = 0.0  # dollars at stake — drives ordering
    # True only when acting on *this* insight puts roughly `stake` dollars back
    # in the user's pocket. Set it False whenever `stake` is a ranking proxy —
    # money already spent, a total that measures how bad the situation is, or a
    # credit-score effect priced in dollars. Anything that totals these adds up
    # only the recoverable ones, so a loose flag here turns into a headline
    # figure the app cannot stand behind.
    recoverable: bool = True
    # Optional UI hand-off. Insight generation stays pure: it describes the
    # destination and preset, while the Streamlit layer decides how to open it.
    action_type: Optional[str] = None
    action_value: Optional[Union[float, str]] = None
    action_label: Optional[str] = None

    @property
    def sort_key(self) -> tuple:
        return (SEVERITY_RANK[self.severity], -self.stake)


# --------------------------------------------------------------------- helpers

def _delta(base: E.Schedule, alt: E.Schedule) -> tuple[float, Optional[int]]:
    """(interest saved, months saved) going from base -> alt.

    Only meaningful when *both* plans terminate — see :func:`_quotable`. If the
    baseline never pays off its "total interest" is just wherever the simulation
    gave up, so a dollar difference against it is meaningless.
    """
    saved = base.total_interest - alt.total_interest
    months = None
    if not base.never_pays_off and not alt.never_pays_off:
        months = base.months - alt.months
    return saved, months


def _quotable(base: E.Schedule, alt: E.Schedule) -> bool:
    """True when a dollars-saved figure between these two plans is honest."""
    return not base.never_pays_off and not alt.never_pays_off


def _residual(s: E.Schedule) -> float:
    """Balance still outstanding when the simulation gave up."""
    if s.ledger.empty:
        return 0.0
    last = s.ledger[s.ledger["month"] == s.ledger["month"].max()]
    return float(last["end_balance"].sum())


def _sim(debts, budget, **kw) -> E.Schedule:
    return E.simulate(debts, budget, **kw)


# ------------------------------------------------------------------ generators

def generate(
    debts: Sequence[Debt],
    profile: Profile,
    budget: Optional[float] = None,
    payments: Optional[Sequence[Payment]] = None,
    today: Optional[date] = None,
) -> list[Insight]:
    """Produce the full ranked insight list for a user's situation."""
    debts = [d for d in debts if d.balance > 0]
    payments = list(payments or [])
    if not debts:
        return [Insight(GOOD, "You're debt free", "No balances on file. Nothing to optimize — "
                        "put the payment you were making into savings instead.")]

    out: list[Insight] = []
    total_balance = sum(d.balance for d in debts)
    min_budget = E.minimum_budget(debts)
    cur_budget = E.current_budget(debts)
    # Explicit arg wins, then the user's stated budget, then what they actually
    # pay today, then the bare minimums. Must match ui.common.effective_budget.
    if not (budget and budget > 0):
        budget = profile.monthly_budget or 0.0
    if budget <= 0:
        budget = max(cur_budget, min_budget)
    strategy = profile.strategy or E.AVALANCHE
    order = profile.custom_order

    plan = _sim(debts, budget, strategy=strategy, custom_order=order)
    mins = _sim(debts, min_budget, strategy=E.MINIMUM)
    aval = _sim(debts, budget, strategy=E.AVALANCHE)
    snow = _sim(debts, budget, strategy=E.SNOWBALL)

    out += _payment_calendar(debts, payments, today)
    out += _ledger_reality(payments, plan, today)
    out += _feasibility(debts, plan, budget, min_budget)
    out += _the_trap(debts, plan, mins, budget, min_budget)
    out += _strategy_gap(plan, aval, snow, strategy)
    out += _interest_burn(debts, plan, total_balance)
    out += _per_debt(debts, plan)
    out += _extra_payments(debts, budget, strategy, order, plan)
    out += _windfall(debts, budget, strategy, order, plan)
    out += _refinance(debts, plan, budget, strategy, order)
    out += _balance_transfer(debts)
    out += _utilization(debts)
    out += _household(debts, profile, min_budget, budget, total_balance)
    out += _emergency_fund(debts, profile)
    out += _milestones(plan)
    out += _cost_of_waiting(debts, budget, strategy, order, plan)

    return sorted(out, key=lambda i: i.sort_key)


def _payment_calendar(debts, payments, today) -> list[Insight]:
    """What the calendar says, which beats what the amortization says.

    A missed payment is the one failure mode that costs more than every
    ordering decision in this file put together, so it sorts above them.
    """
    out: list[Insight] = []
    items = P.upcoming(debts, payments, today)
    if not items:
        if debts:
            out.append(Insight(
                INFO,
                "Add your due dates and this app can watch the calendar for you",
                "None of your accounts have a due day on file. Add one to each in *Accounts* "
                "page and you'll get a running list of what's coming, a one-click way to record "
                "each payment, and a ledger of what this debt has really cost you.",
                action="It takes about a minute and it's the only number here you can read "
                       "straight off a statement.",
                stake=0.0,
                action_type="accounts",
                action_label="Add due dates",
            ))
        return out

    late = [i for i in items if i.status == P.OVERDUE]
    if late:
        owed = sum(i.outstanding for i in late)
        worst = min(late, key=lambda i: i.days)  # most negative = furthest past due
        names = ", ".join(i.name for i in late)
        # A late fee is a fixed, near-certain cost; the penalty APR is the tail
        # risk that actually does the damage.
        fees = 32.0 * len(late)
        out.append(Insight(
            CRITICAL,
            f"{names} {'is' if len(late) == 1 else 'are'} past due",
            f"{names} — **{money(owed)}** outstanding, and {worst.name} is "
            f"{-worst.days} day{'s' if worst.days != -1 else ''} past its due date.\n\n"
            f"A late fee is typically $32 apiece ({money(fees)} here). Worse, most card "
            "agreements let the issuer impose a penalty APR near 29.99% after a single miss, and "
            "at **30 days** the lender reports it to the credit bureaus — a mark that stays on "
            "your file for seven years and costs you far more than the fee ever will.",
            action="Pay something today even if you can't pay it all; a partial payment before "
                   "day 30 keeps it off your credit report. If you genuinely can't, call the "
                   "lender — a hardship note costs nothing and they can waive the fee.",
            metric=f"{-worst.days} days late",
            stake=fees + owed * 0.15,
        ))

    now_due = [i for i in items if i.status == P.DUE_TODAY]
    if now_due:
        total = sum(i.outstanding for i in now_due)
        out.append(Insight(
            SERIOUS,
            f"{'A payment is' if len(now_due) == 1 else f'{len(now_due)} payments are'} due today",
            f"**{', '.join(i.name for i in now_due)}** — **{money(total)}** due today. Same-day "
            "transfers between banks often don't post until the next business day, so 'today' "
            "usually means 'already late'.",
            action="Send it now, then set up autopay for at least the minimum. Autopay on the "
                   "minimum is a floor, not a plan — you can always pay more on top.",
            metric=money(total),
            stake=32.0 * len(now_due),
        ))

    soon = [i for i in items if i.status == P.DUE_SOON]
    if soon:
        total = sum(i.outstanding for i in soon)
        nxt = min(soon, key=lambda i: i.days)
        out.append(Insight(
            WARNING,
            f"{money(total)} due in the next week",
            f"**{len(soon)}** payment{'s' if len(soon) != 1 else ''} coming up: "
            + ", ".join(f"**{i.name}** {money(i.amount)} ({i.phrase})" for i in soon) + ". "
            f"The first is **{nxt.name}**, {nxt.phrase}.",
            action="Check the balance in your current account covers all of it before the first "
                   "one lands. An overdraft fee to make a debt payment is the worst trade there is.",
            metric=money(total),
            stake=0.0,
        ))

    covered = [i for i in items if i.status == P.PAID]
    if covered and not late and not now_due:
        out.append(Insight(
            GOOD,
            "Everything on file is paid for this cycle",
            f"All **{len(covered)}** account{'s' if len(covered) != 1 else ''} with a due date on "
            "file have been paid this cycle. On-time payment history is **35% of a FICO score** — "
            "the single largest input, larger than the balances themselves.",
            metric=f"{len(covered)}/{len(items)}",
            stake=0.0,
        ))
    return out


def _ledger_reality(payments, plan, today) -> list[Insight]:
    """The counters off the ledger. Recorded money, not projected money."""
    out: list[Insight] = []
    t = P.totals(payments)
    if t["count"] < 2:
        return out

    share = t["interest_share"]
    body = (f"Across {t['count']} logged payments totalling {money(t['paid'])}, "
            f"**{money(t['interest'])}** went to interest and only {money(t['principal'])} came "
            f"off what you owe — {share * 100:.0f}¢ of every dollar you sent, about "
            f"{money(t['interest'] / max(1, t['months']))} a month buying nothing.\n\n"
            "This is the one figure in the app that isn't a projection. It already happened.")

    # The trailing-year number belongs in the same card: on its own it was a
    # third variation on "your interest is large", which is not a third insight.
    year = P.interest_since_tracking(payments, 365, today)
    if year > 100 and t["months"] >= 3:
        body += (f"\n\nIn the last 12 months alone: {money(year)}. Put differently, clearing this "
                 f"debt is worth a guaranteed **{money(year)}-a-year raise**, tax-free.")

    out.append(Insight(
        SERIOUS if share >= 0.35 else WARNING if share >= 0.2 else INFO,
        f"You've handed over {money(t['interest'])} in interest since {t['first']:%b %Y}",
        body,
        action="Every extra dollar above the minimum skips the interest line entirely and lands "
               "straight on principal. That is the whole game.",
        metric=f"{share:.0%} to interest",
        stake=t["interest"],
        recoverable=False,
    ))

    if t["principal"] > 0:
        out.append(Insight(
            GOOD,
            f"You've knocked {money(t['principal'])} off your balances",
            f"**{money(t['principal'])}** of real principal is gone, over "
            f"**{t['months']}** month{'s' if t['months'] != 1 else ''} of logged payments — an "
            f"average of **{money(t['principal'] / max(1, t['months']))}/month** of genuine "
            "progress. Projections are easy to doubt; this part already happened.",
            metric=money(t["principal"]),
            stake=0.0,
        ))
    return out


def _feasibility(debts, plan, budget, min_budget) -> list[Insight]:
    out = []
    if budget < min_budget - 0.5:
        gap = min_budget - budget
        out.append(Insight(
            CRITICAL,
            "Your budget doesn't cover your minimum payments",
            f"Your required payments total **{money(min_budget)}/mo** but your budget is "
            f"**{money(budget)}/mo** — a shortfall of **{money(gap)}** every month. Missed "
            "payments trigger late fees, penalty APRs (often 29.99%), and credit damage that "
            "compounds far faster than the interest itself.",
            action="Call each lender *before* you miss a payment and ask about hardship plans, "
                   "forbearance, or a hardship APR reduction. Lenders almost always cooperate "
                   "with someone who calls first.",
            metric=f"{money(gap)}/mo short",
            stake=gap * 12,
            recoverable=False,
            action_type="plan_budget",
            action_value=min_budget,
            action_label="Fix my monthly plan",
        ))
    if plan.never_pays_off:
        out.append(Insight(
            CRITICAL,
            "At this payment level, this debt never goes away",
            "Your payments are being consumed by interest faster than they reduce principal. "
            "The balance is flat or growing — you could pay forever and still owe the full amount.",
            action=f"You need to get above **{money(E.minimum_budget(debts) * 1.25)}/mo** to make "
                   "real progress. Anything below that is renting the debt, not paying it.",
            metric="Never",
            stake=sum(d.balance for d in debts),
            recoverable=False,
            action_type="plan_budget",
            action_value=E.minimum_budget(debts) * 1.25,
            action_label="Try a workable payment",
        ))
    for d in debts:
        req = d.required_payment(d.balance + d.monthly_interest)
        if req < d.monthly_interest - 0.005:
            out.append(Insight(
                CRITICAL,
                f"{d.name}: the minimum payment is smaller than the interest",
                f"{d.name} accrues {money(d.monthly_interest, cents=True)}/mo in interest but "
                f"the minimum due is only {money(req, cents=True)}. Paying the minimum makes the "
                f"balance *grow* by **{money(d.monthly_interest - req, cents=True)}** every "
                "month.",
                action=f"Pay at least {money(d.monthly_interest * 1.5)}/mo on {d.name} just to "
                       "move backwards no longer.",
                metric=f"+{money(d.monthly_interest - req, cents=True)}/mo",
                stake=(d.monthly_interest - req) * 60,
            ))
    return out


def _the_trap(debts, plan, mins, budget, min_budget) -> list[Insight]:
    out = []
    if budget <= min_budget + 0.5:
        # They *are* on minimums.
        if mins.never_pays_off:
            years = "never"
        else:
            years = duration(mins.months)
        out.append(Insight(
            SERIOUS,
            "You're on the minimum-payment track",
            f"Paying only the minimum retires this debt in {years} and costs "
            f"**{money(mins.total_interest)}** in interest — {mins.interest_share:.0%} of every "
            "dollar you hand over. Card minimums are a percentage of the balance, so they shrink "
            "as you pay, which stretches the tail out for years by design.",
            action="Fix your payment at today's minimum instead of letting it shrink. That single "
                   "change costs you nothing today and cuts years off the timeline.",
            metric=years,
            stake=mins.total_interest,
            recoverable=False,
        ))
    elif mins.never_pays_off:
        # No honest dollar figure exists against an unbounded baseline — but the
        # lower bound is damning enough on its own.
        left = _residual(mins)
        out.append(Insight(
            SERIOUS,
            "Minimum payments alone would never clear this debt",
            "On minimums only, the required payment on at least one account is smaller than the "
            f"interest it accrues, so the balance grows. After {duration(mins.months)} of paying "
            f"every minimum on time you would have handed over {money(mins.total_interest)} in "
            f"interest and **still owe {money(left)}**.\n\n"
            f"Your {money(budget)}/mo plan escapes that entirely — it's the difference between a "
            "finite debt and a permanent one.",
            metric="Never vs " + (duration(plan.months) if not plan.never_pays_off else "—"),
            stake=mins.total_interest,
            recoverable=False,
        ))
    else:
        saved, months = _delta(mins, plan)
        if saved > 1 and _quotable(mins, plan):
            out.append(Insight(
                GOOD,
                "Paying above the minimum is already saving you real money",
                f"Versus minimums-only, your **{money(budget)}/mo** plan saves "
                f"**{money(saved)}** in interest"
                + (f" and **{duration(months)}** of payments" if months else "") + ". "
                f"Minimums-only would have taken **{duration(mins.months)}** and cost "
                f"**{money(mins.total_interest)}**.",
                metric=money(saved),
                stake=saved,
            ))
    return out


def _strategy_gap(plan, aval, snow, strategy) -> list[Insight]:
    out = []
    if strategy != E.AVALANCHE and plan.never_pays_off and not aval.never_pays_off:
        out.append(Insight(
            CRITICAL,
            "Reordering the same payments is the difference between escaping and not",
            "Your current payment order never clears the debt, but sending the same total to "
            f"your highest-APR balance first pays everything off in **{duration(aval.months)}**. "
            "Not one extra dollar — just a different target.",
            action="Pay minimums on everything, then put every remaining dollar on the highest "
                   "APR until it's gone.",
            metric=duration(aval.months),
            stake=aval.total_interest,
            action_type="plan_strategy",
            action_value=E.AVALANCHE,
            action_label="Preview avalanche",
        ))
    elif strategy != E.AVALANCHE and _quotable(plan, aval):
        saved, months = _delta(plan, aval)
        if saved > 1:
            out.append(Insight(
                SERIOUS,
                "Reordering your payments saves money for free",
                f"Same budget, same dollars out the door — just aimed at the highest APR first. "
                f"Switching to **avalanche** saves **{money(saved)}** in interest"
                + (f" and gets you out **{duration(months)}** sooner" if months and months > 0 else "")
                + ". This costs you nothing; it is purely a change in *which* debt gets the extra.",
                action="Send every spare dollar to your highest-APR debt while paying minimums on "
                       "the rest.",
                metric=money(saved),
                stake=saved,
                action_type="plan_strategy",
                action_value=E.AVALANCHE,
                action_label="Preview avalanche",
            ))
    cost, months = _delta(snow, aval)
    if cost > 1 and strategy != E.SNOWBALL and _quotable(snow, aval):
        out.append(Insight(
            INFO,
            "What the snowball method would cost you",
            f"Attacking the *smallest balance* first clears individual accounts sooner, which some "
            f"people need to stay motivated. The price of that motivation here is "
            f"**{money(cost)}** in extra interest"
            + (f" and **{duration(months)}** longer" if months and months > 0 else "")
            + ". If you've quit debt plans before, that may be money well spent.",
            metric=money(cost),
            stake=cost * 0.3,
            recoverable=False,
        ))
    return out


def _interest_burn(debts, plan, total_balance) -> list[Insight]:
    """What carrying this debt costs, per day and in total.

    One card, not two: "you pay $15.69 a day" and "you'll repay 1.20× what you
    owe" are the same fact at two time horizons, and splitting them made the
    page read like it was padding.

    ``recoverable=False`` because this is a description, not an offer. Acting on
    every other insight on the page does not recover the whole interest bill,
    and the "identified savings" total must not imply it does.
    """
    daily = sum(d.daily_interest for d in debts)
    monthly = sum(d.monthly_interest for d in debts)
    blended = (monthly * 12 / total_balance * 100) if total_balance else 0.0
    year1 = E.where_the_money_goes(plan, 12)

    body = (f"Across {money(total_balance)} at a blended {blended:.1f}% APR, interest accrues at "
            f"**{money(daily, cents=True)} a day** — {money(monthly)}/month, "
            f"{money(monthly * 12)}/year — before you pay down a single dollar of principal.\n\n"
            f"Over the next 12 months you'll pay {money(year1['payment'])}, of which "
            f"**{money(year1['interest'])}** disappears into interest and only "
            f"{money(year1['principal'])} actually reduces what you owe.")

    metric = f"{money(daily, cents=True)}/day"
    if plan.total_paid > 0 and not plan.never_pays_off:
        multiple = plan.total_paid / total_balance
        body += (f"\n\nOver the life of the plan you'll hand over {money(plan.total_paid)} to "
                 f"clear {money(total_balance)} — **{multiple:.2f}× what you actually owe**, with "
                 f"{plan.interest_share * 100:.0f}¢ of every dollar going to your lenders rather "
                 "than your own balance sheet.")
        metric = f"{multiple:.2f}× what you owe"

    return [Insight(
        SERIOUS if blended >= 15 else WARNING,
        f"You are paying {money(daily, cents=True)} per day just to keep this debt",
        body, metric=metric, stake=plan.total_interest, recoverable=False,
    )]


def _per_debt(debts, plan) -> list[Insight]:
    out = []
    if len(debts) < 2:
        return out
    worst = max(debts, key=lambda d: d.apr)
    biggest_cost = max(debts, key=lambda d: d.monthly_interest)
    out.append(Insight(
        WARNING,
        f"{worst.name} is your most expensive debt at {worst.apr:.2f}% APR",
        f"Every $100 you send to {worst.name} earns you a guaranteed, tax-free "
        f"**{worst.apr:.2f}% return** — better than any investment you can buy with certainty. "
        f"It is currently costing {money(worst.monthly_interest)}/mo"
        + (", the largest interest line in your portfolio." if worst is biggest_cost
           else f", while {biggest_cost.name} costs the most in raw dollars "
                f"({money(biggest_cost.monthly_interest)}/mo)."),
        action=f"Until {worst.name} is gone, every spare dollar belongs there.",
        metric=f"{worst.apr:.2f}%",
        stake=worst.monthly_interest * 12,
    ))

    cheap = [d for d in debts if 0 < d.apr <= 5]
    if cheap:
        names = ", ".join(d.name for d in cheap)
        out.append(Insight(
            GOOD,
            "Don't rush your cheap debt",
            f"**{names}** sits at or below 5% APR. Paying it off early is one of the *worst* uses "
            "of a spare dollar while higher-rate balances exist — and often worse than simply "
            "investing. Pay the minimum and route everything else to the expensive debt.",
            metric=f"{min(d.apr for d in cheap):.2f}% APR",
            stake=0.0,
        ))
    return out


def _sooner(months: Optional[int]) -> str:
    """" and N sooner", or nothing when the date doesn't actually move."""
    return f" and {duration(months)} sooner" if months and months > 0 else ""


def _extra_payments(debts, budget, strategy, order, plan) -> list[Insight]:
    """One card for the whole "send more each month" family.

    This used to emit up to five: three fixed tiers, a round-up-to-the-next-$50,
    and biweekly. The round-up was arithmetically *the $50 tier* — on the demo
    portfolio both reported saving $760 — and five cards that all say "pay more"
    is how a reader learns to skim the page. One card, one ladder.
    """
    tiers = []
    for extra, label in ((50, "$50"), (100, "$100"), (250, "$250")):
        alt = _sim(debts, budget, strategy=strategy, custom_order=order, extra=extra)
        if plan.never_pays_off and not alt.never_pays_off:
            # Crossing from "never" to "finite" is the headline, not a dollar total.
            return [Insight(
                CRITICAL,
                f"Adding {label}/month is what turns this from permanent into finite",
                f"At your current payment the balance never clears. An extra **{label}/month** — "
                f"about {money(extra / 30.0, cents=True)} a day — is enough to break the interest "
                f"and pay everything off in {duration(alt.months)}.",
                action=f"This is the single most important number on this page. Find {label}.",
                metric=duration(alt.months),
                stake=alt.total_interest,
                action_type="plan_extra",
                action_value=extra,
                action_label=f"Try +{label}/month",
            )]
        saved, months = _delta(plan, alt)
        if saved > 1 and _quotable(plan, alt):
            tiers.append((extra, label, saved, months))

    if not tiers:
        return []

    # Headline the middle rung when there is one: the largest number overpromises
    # what most people can actually find, the smallest undersells the page.
    extra, label, saved, months = tiers[len(tiers) // 2]
    ladder = "\n\n".join(
        f"**+{lb}/mo** (about {money(ex / 30.0, cents=True)} a day) — saves {money(sv)} "
        f"in interest{_sooner(mo)}."
        for ex, lb, sv, mo in tiers
    )

    body = f"Every dollar above the minimum skips the interest line and lands on principal.\n\n{ladder}"

    # Biweekly is the same idea by a different mechanism, so it belongs in the
    # same card rather than in one of its own.
    bi = _sim(debts, budget * 13.0 / 12.0, strategy=strategy, custom_order=order)
    bi_saved, bi_months = _delta(plan, bi)
    if bi_saved > 1 and _quotable(plan, bi):
        body += (f"\n\nOr find it without noticing: pay {money(budget / 2)} every two weeks "
                 f"instead of {money(budget)} once a month. Twenty-six fortnights a year is "
                 f"thirteen monthly payments, which saves **{money(bi_saved)}**{_sooner(bi_months)}.")

    return [Insight(
        SERIOUS,
        f"Adding {label} a month saves you {money(saved)}",
        body,
        action=f"Set up an automatic {label} transfer on payday so it leaves before you can "
               "spend it.",
        metric=money(saved),
        # The headlined rung, not the largest: `stake` is what ranks the card and
        # gets quoted back, so it has to be the number the title already claims.
        stake=saved,
        action_type="plan_extra",
        action_value=extra,
        action_label=f"Try +{label}/month",
    )]


def _windfall(debts, budget, strategy, order, plan) -> list[Insight]:
    """One card for lump sums, covering every tier that applies.

    Two nearly identical cards for $1,000 and $5,000 taught the reader nothing
    the second time; the interesting part is the rate of return and the cost of
    waiting, which is the same story at both sizes.
    """
    rows, delay_cost = [], 0.0
    for amount in (1000, 5000):
        if amount > sum(d.balance for d in debts):
            continue
        now = _sim(debts, budget, strategy=strategy, custom_order=order,
                   lump_sum=amount, lump_month=1)
        saved, months = _delta(plan, now)
        if saved <= 1 or not _quotable(plan, now):
            continue
        later = _sim(debts, budget, strategy=strategy, custom_order=order,
                     lump_sum=amount, lump_month=13)
        delay_cost = later.total_interest - now.total_interest
        rows.append((amount, saved, months))

    if not rows:
        return []

    amount, saved, _ = rows[-1]
    ladder = "\n\n".join(
        f"**{money(amt)} today** — removes {money(sv)} of future interest{_sooner(mo)}, "
        f"an effective {sv / amt * 100:.0f}% return."
        for amt, sv, mo in rows
    )
    return [Insight(
        INFO,
        f"A one-time {money(amount)} payment saves {money(saved)}",
        f"Tax refund, bonus, or a sold couch — applied to your highest-rate balance:\n\n{ladder}"
        f"\n\nTiming matters more than size. Waiting a year to send the same "
        f"{money(amount)} costs you **{money(delay_cost)}** extra.",
        action="Send it the day it arrives, straight at your highest-APR balance. A windfall "
               "that sits in a current account for a month gets spent.",
        metric=money(saved),
        stake=saved,
        action_type="plan_lump",
        action_value=amount,
        action_label=f"Try a {money(amount)} payment",
    )]


def _refinance(debts, plan, budget, strategy, order) -> list[Insight]:
    """Would one consolidation loan beat the current mess?"""
    out = []
    total = sum(d.balance for d in debts)
    weighted_apr = sum(d.balance * d.apr for d in debts) / total if total else 0.0
    if weighted_apr < 10 or total < 2000:
        return out

    for offer_apr, term in ((12.0, 60), (9.0, 48)):
        if offer_apr >= weighted_apr - 1:
            continue
        consolidated = Debt(name="Consolidation loan", kind="term_loan", balance=total,
                            apr=offer_apr, term_months=term)
        consolidated.min_payment = round(amortized_payment(total, offer_apr, term), 2)
        alt = _sim([consolidated], max(budget, consolidated.min_payment), strategy=E.AVALANCHE)
        saved, months = _delta(plan, alt)
        if not _quotable(plan, alt) or saved <= 50:
            continue
        out.append(Insight(
            SERIOUS if saved > 1000 else INFO,
            f"A {offer_apr:.0f}% consolidation loan could save {money(saved)}",
            f"Your balances average a blended {weighted_apr:.1f}% APR. Rolling {money(total)} "
            f"into a single {offer_apr:.0f}% / {term}-month personal loan means one payment of "
            f"{money(consolidated.min_payment)}, a fixed end date of {duration(term)}, and "
            f"**{money(saved)}** less interest.\n\n"
            "The catch, and it is a real one: consolidation only works if you *stop using the "
            "cards*. Half of borrowers run the balances back up within two years and end up with "
            "both the loan and the cards.",
            action="Check your rate with a soft-pull prequalification (no credit hit) at two or "
                   "three lenders and a local credit union before accepting anything.",
            metric=money(saved),
            stake=saved,
        ))
        break
    return out


def _balance_transfer(debts) -> list[Insight]:
    """0% intro-APR transfer math, fee included."""
    out = []
    candidates = [d for d in debts if d.is_card and d.apr >= 15 and d.balance >= 500]
    if not candidates:
        return out
    d = max(candidates, key=lambda x: x.balance * x.apr)
    promo_months, fee_pct = 18, 3.0
    fee = d.balance * fee_pct / 100.0
    # Interest avoided if the balance is paid evenly across the promo window.
    payment = d.balance / promo_months
    bal, avoided = d.balance, 0.0
    for _ in range(promo_months):
        avoided += bal * d.monthly_rate
        bal = max(0.0, bal - payment)
    net = avoided - fee
    if net <= 25:
        return out
    out.append(Insight(
        SERIOUS if net > 500 else INFO,
        f"A 0% balance transfer on {d.name} nets you {money(net)}",
        f"{d.name} carries {money(d.balance)} at {d.apr:.2f}%. Moving it to a 0%-for-"
        f"{promo_months}-months transfer card costs a {fee_pct:.0f}% fee ({money(fee)}) but "
        f"avoids {money(avoided)} of interest — a net **{money(net)}** in your pocket.\n\n"
        f"To clear it inside the promo window you'd pay {money(payment)}/mo. Miss that window "
        "and the leftover balance snaps to the card's regular APR, which is usually worse "
        "than where you started.",
        action=f"Only do this if you can commit to {money(payment)}/mo for {promo_months} months. "
               "Set a calendar reminder for month 16.",
        metric=money(net),
        stake=net,
    ))
    return out


def _utilization(debts) -> list[Insight]:
    out = []
    cards = [d for d in debts if d.is_card and d.credit_limit > 0]
    if not cards:
        return out
    total_bal = sum(d.balance for d in cards)
    total_lim = sum(d.credit_limit for d in cards)
    overall = 100 * total_bal / total_lim if total_lim else 0
    maxed = [d for d in cards if (d.utilization or 0) >= 90]
    high = [d for d in cards if (d.utilization or 0) >= 30]

    if overall >= 30:
        target = total_lim * 0.29
        paydown = total_bal - target
        out.append(Insight(
            WARNING if overall < 70 else SERIOUS,
            f"Your credit utilization is {overall:.0f}% — that's hurting your score",
            f"You're using {money(total_bal)} of {money(total_lim)} in available credit. "
            "Utilization is roughly 30% of a FICO score, and crossing above 30% typically costs "
            "tens of points. Paying "
            f"**{money(paydown)}** would bring you under the 30% line."
            + (f"\n\n{', '.join(d.name for d in maxed)} "
               f"{'is' if len(maxed) == 1 else 'are'} effectively maxed out, which is the single "
               "most damaging pattern on a credit report." if maxed else ""),
            action="A higher score is not cosmetic — it's the difference between a 9% and a 22% "
                   "rate on your next loan. Ask for a credit-limit increase (it lowers "
                   "utilization instantly without paying a dollar) but do not spend against it.",
            metric=f"{overall:.0f}%",
            stake=paydown * 0.2,
            recoverable=False,
        ))
    elif high:
        out.append(Insight(
            INFO,
            "One card is dragging your utilization up",
            f"Overall you're at a healthy **{overall:.0f}%**, but "
            f"**{', '.join(d.name for d in high)}** "
            f"{'is' if len(high) == 1 else 'are'} individually above 30%. Scoring models look at "
            "per-card utilization as well as the aggregate.",
            metric=f"{max(d.utilization or 0 for d in high):.0f}%",
            stake=100.0,
            recoverable=False,
        ))
    return out


def _household(debts, profile, min_budget, budget, total_balance) -> list[Insight]:
    out = []
    income = profile.monthly_income
    if income <= 0:
        return out
    dti = 100 * min_budget / income
    share = 100 * budget / income
    if dti >= 36:
        out.append(Insight(
            SERIOUS if dti >= 43 else WARNING,
            f"Your required debt payments eat {dti:.0f}% of your income",
            f"Minimums alone are **{money(min_budget)}** against **{money(income)}/mo** of income. "
            "Lenders treat 36% as the edge of comfortable and 43% as the ceiling for a qualified "
            "mortgage — above that, new credit gets expensive or unavailable, which is exactly "
            "when people reach for it.",
            action="Two levers only: raise income or cut the balances. Refinancing helps the rate "
                   "but rarely the ratio.",
            metric=f"{dti:.0f}% DTI",
            stake=min_budget * 12,
            recoverable=False,
        ))
    if share >= 20:
        # GOOD, not INFO: there is nothing to recover here, and filing it under
        # "opportunities" made the page promise savings it wasn't offering.
        out.append(Insight(
            GOOD,
            f"You're routing {share:.0f}% of income at debt",
            f"{money(budget)} of {money(income)} goes to debt each month — an aggressive, "
            "effective payoff rate. Just make sure it's sustainable: a plan you abandon in "
            "month 8 costs more than a slower one you finish.",
            metric=f"{share:.0f}%",
            stake=0.0,
        ))
    if total_balance > income * 12:
        out.append(Insight(
            WARNING,
            "Your debt exceeds a full year of income",
            f"**{money(total_balance)}** owed against **{money(income * 12)}** of annual income. "
            "At this ratio, incremental optimization matters less than a structural change — a "
            "consolidation, a higher income, or in the worst case a conversation with a "
            "nonprofit credit counselor (NFCC members are free).",
            metric=f"{total_balance / (income * 12):.1f}× income",
            stake=total_balance * 0.05,
            recoverable=False,
        ))
    return out


def _emergency_fund(debts, profile) -> list[Insight]:
    out = []
    fund = profile.emergency_fund
    if fund <= 0:
        out.append(Insight(
            WARNING,
            "No emergency fund means the next surprise goes on a card",
            "Attacking debt with zero cash buffer is how people end up back where they started: "
            "one car repair, one vet bill, and the balance is right back on the highest-APR card.",
            action="Park **$1,000** in a separate savings account first, *then* go hard at the "
                   "debt. It costs you a little interest and saves you the whole plan.",
            stake=1000.0,
            action_type="plan_settings",
            action_label="Add my emergency fund",
        ))
        return out

    top = max((d for d in debts if d.apr > 0), key=lambda d: d.apr, default=None)
    if top and fund > 1000 and top.apr >= 12:
        deployable = min(fund - 1000, top.balance)
        if deployable > 100:
            annual = deployable * top.apr / 100
            out.append(Insight(
                INFO,
                f"Your idle cash is losing to {top.name} by {top.apr:.1f}% a year",
                f"You hold {money(fund)} in cash earning maybe 4%, while {top.name} charges "
                f"{top.apr:.2f}%. Deploying {money(deployable)} — keeping $1,000 back as a "
                f"buffer — is a guaranteed **{money(annual)} a year**, risk-free, tax-free, and "
                "better than the market's long-run average.",
                action="Keep enough back that you never have to re-borrow. A cash buffer you "
                       "spend and replace with 24% debt was never savings.",
                metric=f"{money(annual)}/yr",
                stake=annual,
            ))
    return out


def _milestones(plan: E.Schedule) -> list[Insight]:
    """The good news, as one card — the first payoff and the last are the same
    story told from either end."""
    if not plan.payoff_month:
        return []
    name, month = min(plan.payoff_month.items(), key=lambda kv: kv[1])
    body = (f"**{name}** clears in {duration(month)}. When it does its payment doesn't disappear "
            "— it rolls onto the next debt, so every payoff after that comes faster than the "
            "last. The hardest part of this plan is the first year.")

    if not plan.never_pays_off and plan.payoff_date:
        return [Insight(
            GOOD,
            f"Debt-free on {plan.payoff_date.strftime('%B %Y')}",
            body + f"\n\nHold the plan and the last dollar goes out in {duration(plan.months)}. "
                   f"From that month on, the {money(plan.budget)}/mo you're sending to lenders is "
                   f"yours — **{money(plan.budget * 12)} a year** redirected to your own balance "
                   "sheet.",
            metric=plan.payoff_date.strftime("%b %Y"),
            stake=0.0,
        )]
    return [Insight(GOOD, f"Your first account is gone in {duration(month)}", body,
                    metric=duration(month), stake=0.0)]


def _cost_of_waiting(debts, budget, strategy, order, plan) -> list[Insight]:
    """What six months of doing nothing costs."""
    if plan.never_pays_off:
        return []
    delayed = _sim(debts, E.minimum_budget(debts), strategy=E.MINIMUM, max_months=6)
    # Balances after six months of minimums-only:
    if delayed.ledger.empty:
        return []
    last = delayed.ledger[delayed.ledger["month"] == delayed.ledger["month"].max()]
    future = []
    for d in debts:
        row = last[last["debt"] == d.name]
        nd = Debt.from_dict(d.to_dict())
        nd.balance = float(row["end_balance"].iloc[0]) if not row.empty else 0.0
        if nd.balance > 0:
            future.append(nd)
    if not future:
        return []
    after = _sim(future, budget, strategy=strategy, custom_order=order)
    cost = (after.total_interest + delayed.total_interest) - plan.total_interest
    if cost <= 25:
        return []
    return [Insight(
        SERIOUS,
        f"Waiting six months to start costs you {money(cost)}",
        f"If you coast on minimums until you 'get organized' and then begin this same plan in six "
        f"months, you'll pay **{money(cost)}** more in total interest and finish "
        f"**{duration(max(0, (after.months + 6) - plan.months))}** later. Nothing about your "
        "situation improves in the meantime — the balances just get bigger.",
        action="Start with whatever number you can commit to today, even if it isn't the ideal "
               "one. The start date matters more than the amount.",
        metric=money(cost),
        stake=cost,
    )]
