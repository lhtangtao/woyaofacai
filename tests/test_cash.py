from datetime import date

from test_replay import snapshot

from portfolio.domain.models import Ledger, QueryScope
from portfolio.engine.cash import cash_balance


def test_cash_requires_opening_and_uses_net_amount_without_double_fee():
    s = snapshot([])
    s.scope = QueryScope(date(2026, 2, 2), ledger=Ledger.BROKER)
    assert cash_balance(s, 1) is None
    s.baselines = [dict(baseline_role="OPENING", baseline_kind="CASH_BALANCE", account_id=1, person_id=None,
                        baseline_date="2026-01-01", currency="USD", market_value_e8=100000000000, source_record_id=1)]
    s.extras["broker_trades"] = [dict(account_id=1, record_state="ACTIVE", accounting_role="ACCOUNT_LEDGER", business_date="2026-01-02", net_amount_e8=-10100000000)]
    s.extras["cash_events"] = [dict(account_id=1, person_id=None, record_state="ACTIVE", business_date="2026-01-03", accounting_role="INCOME", event_type="DIVIDEND", broker_trade_id=None, amount_e8=200000000, source_record_id=3)]
    assert cash_balance(s, 1) == 901
    s.extras["cash_events"].append(dict(account_id=1, person_id=None, record_state="ACTIVE", business_date="2026-01-03", accounting_role="EXPENSE", event_type="FEE", broker_trade_id=1, amount_e8=-100000000, source_record_id=4))
    assert cash_balance(s, 1) is None
    assert any(i.rule == "DQ017" for i in s.issues)


def test_physical_account_cash_never_allocated_to_owner():
    s = snapshot([])
    s.scope = QueryScope(date(2026, 2, 2), person_ids=(1,))
    s.baselines = [dict(baseline_role="OPENING", baseline_kind="CASH_BALANCE", account_id=1, person_id=None,
                        baseline_date="2026-01-01", currency="USD", market_value_e8=100000000000, source_record_id=1)]
    assert cash_balance(s, 1) is None
