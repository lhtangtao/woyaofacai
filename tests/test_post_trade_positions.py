import hashlib
import sqlite3
from datetime import date
from decimal import Decimal, localcontext

from portfolio.domain.models import Ledger, QueryScope, TradeFilters
from portfolio.services.trades_service import TradesService


def scope(**kwargs):
    return QueryScope(date(2026,1,10), **kwargs)


def test_post_quantity_is_separate_per_person_and_physical_ledger(demo_path):
    service = TradesService(demo_path)
    rows = {r.record_id:r for r in service.get_trades(scope()).rows}
    assert rows[1].values[9] == Decimal(6)
    assert rows[2].values[9] == Decimal(4)
    assert rows[3].values[9] == Decimal(2)
    physical = {r.record_id:r for r in service.get_trades(scope(ledger=Ledger.BROKER)).rows}
    assert physical[1].values[9] == Decimal(10)
    assert physical[2].values[9] == Decimal(6)


def test_side_start_search_paging_and_sort_do_not_drop_prior_buys(demo_path):
    service = TradesService(demo_path)
    page = service.get_trades(scope(person_ids=(1,), start_date=date(2026,1,3), search='DEMO'),
                              filters=TradeFilters(side='SELL'), size=1, sort_column=5, descending=False)
    assert page.total == 1 and page.rows[0].values[9] == Decimal(2)
    second = service.get_trades(scope(person_ids=(1,)), size=1, page=1)
    assert second.rows[0].record_id == 1 and second.rows[0].values[9] == Decimal(6)


def test_split_applies_only_to_operations_on_or_after_its_effective_day(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("INSERT INTO corporate_actions(action_key,instrument_id,effective_date,action_type,ratio_numerator,ratio_denominator,source_record_id) VALUES('split',1,'2026-01-03','SPLIT',2,1,6)")
    rows = {r.record_id:r for r in TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows}
    assert rows[1].values[9] == Decimal(6)
    assert rows[3].values[9] == Decimal(8)
    assert rows[3].values[10:12] == (Decimal(120), Decimal(15))
    broker = TradesService(demo_path).get_trades(scope(ledger=Ledger.BROKER), filters=TradeFilters(side='SELL'))
    assert broker.rows[0].values[9] == Decimal(16)


def test_ownership_transfer_moves_shares_between_people(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET broker_trade_id=NULL,operation_kind='OWNER_TRANSFER',side='TRANSFER_OUT',counterparty_person_id=2,transfer_group_key='G',amount_basis='TRANSFER_VALUE' WHERE id=3")
        db.execute("INSERT INTO person_operations(operation_key,person_id,account_id,instrument_id,operation_kind,side,business_date,book_time,sequence_no,quantity_e8,amount_e8,amount_basis,currency,counterparty_person_id,transfer_group_key,link_status,source_record_id) VALUES('transfer-in',2,1,1,'OWNER_TRANSFER','TRANSFER_IN','2026-01-03','2026-01-03 10:00:00',6,400000000,48000000000,'TRANSFER_VALUE','USD',1,'G','LINKED',6)")
    rows = TradesService(demo_path).get_trades(scope()).rows
    outgoing = next(r for r in rows if r.metadata['side']=='TRANSFER_OUT')
    incoming = next(r for r in rows if r.metadata['side']=='TRANSFER_IN')
    assert outgoing.values[9] == Decimal(2) and incoming.values[9] == Decimal(8)


def test_reversed_reference_and_pre_opening_rows_are_not_fabricated_balances(demo_path):
    service = TradesService(demo_path)
    rows = {r.record_id:r for r in service.get_trades(scope(ledger=Ledger.BROKER, audit=True)).rows}
    assert rows[3].values[9] is None and rows[4].values[9] is None
    assert rows[2].values[9] == Decimal(6)
    with sqlite3.connect(demo_path) as db:
        db.execute("INSERT INTO historical_baselines(baseline_key,baseline_date,baseline_role,baseline_kind,scope_type,person_id,account_id,instrument_id,quantity_e8,net_cost_e8,currency,source_record_id) VALUES('opening','2026-01-02','OPENING','HOLDING','PERSON_ACCOUNT',1,1,1,2000000000,100000000000,'USD',5)")
    rows = {r.record_id:r for r in service.get_trades(scope(person_ids=(1,))).rows}
    assert rows[1].values[9] is None
    assert rows[3].values[9] == Decimal(16)
    assert rows[3].values[10:12] == (Decimal(520), Decimal('32.5'))


def test_historical_unlinked_records_still_contribute(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET broker_trade_id=NULL,link_status='LEGACY_UNLINKED' WHERE id=1")
    rows = {r.record_id:r for r in TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows}
    assert rows[1].values[9] == Decimal(6) and rows[3].values[9] == Decimal(2)


def test_ambiguous_grain_never_returns_a_guessed_post_quantity(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("INSERT INTO broker_trades(trade_key,account_id,instrument_id,broker_order_id,broker_execution_id,record_granularity,side,business_date,book_time,quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,source_record_id) SELECT 'duplicate-fill',account_id,instrument_id,broker_order_id,'fill-1','FILL',side,business_date,book_time,quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,source_record_id FROM broker_trades WHERE id=1")
    rows = TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows
    assert all(r.values[9] is None and r.metadata['post_quantity_status']=='UNAVAILABLE' for r in rows)
    assert all(r.values[10] is None and r.values[11] is None for r in rows)


def test_post_quantity_view_never_writes_source(demo_path):
    before = hashlib.sha256(demo_path.read_bytes()).hexdigest()
    service = TradesService(demo_path)
    service.get_trades(scope())
    service.get_trades(scope(ledger=Ledger.BROKER))
    assert hashlib.sha256(demo_path.read_bytes()).hexdigest() == before


def test_future_order_evidence_does_not_change_earlier_post_quantity(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("INSERT INTO broker_trades(trade_key,account_id,instrument_id,broker_order_id,broker_execution_id,record_granularity,side,business_date,book_time,quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,source_record_id) SELECT 'future-fill',account_id,instrument_id,broker_order_id,'future-fill-1','FILL',side,'2026-02-01','2026-02-01 10:00:00',quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,source_record_id FROM broker_trades WHERE id=1")
    rows = {r.record_id:r for r in TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows}
    assert rows[1].values[9] == Decimal(6) and rows[3].values[9] == Decimal(2)


def test_user_net_cost_example_not_average_purchase_cost(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET quantity_e8=1000000000,price_e8=10000000000,amount_e8=100000000000 WHERE id=1")
        db.execute("UPDATE person_operations SET quantity_e8=500000000,price_e8=3000000000,amount_e8=15000000000 WHERE id=3")
    rows = {r.record_id:r for r in TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows}
    assert rows[1].values[9:12] == (Decimal(10), Decimal(1000), Decimal(100))
    assert rows[3].values[9:12] == (Decimal(5), Decimal(850), Decimal(170))


def test_post_cost_ledger_separation_and_filtered_history(demo_path):
    service = TradesService(demo_path)
    personal = service.get_trades(scope(person_ids=(1,), start_date=date(2026,1,3)),
                                  filters=TradeFilters(side='SELL'), size=1).rows[0]
    assert personal.values[9:12] == (Decimal(2), Decimal(120), Decimal(60))
    broker = service.get_trades(scope(ledger=Ledger.BROKER), filters=TradeFilters(side='SELL')).rows[0]
    with localcontext() as context:
        context.prec = 50
        assert broker.values[9:12] == (Decimal(6), Decimal(520), Decimal(520)/6)


def test_closed_position_has_no_per_share_cost_and_negative_cost_is_retained(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET amount_e8=90000000000 WHERE id=3")
    service = TradesService(demo_path)
    sale = service.get_trades(scope(person_ids=(1,))).rows[0]
    assert sale.values[9:12] == (Decimal(2), Decimal(-300), Decimal(-150))
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET quantity_e8=600000000 WHERE id=3")
    sale = service.get_trades(scope(person_ids=(1,))).rows[0]
    assert sale.values[9:12] == (Decimal(0), Decimal(-300), None)


def test_missing_transfer_value_keeps_quantity_without_guessed_cost(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET broker_trade_id=NULL,operation_kind='OWNER_TRANSFER',side='TRANSFER_OUT',counterparty_person_id=2,amount_e8=NULL,amount_basis='TRANSFER_VALUE' WHERE id=3")
    sale = TradesService(demo_path).get_trades(scope(person_ids=(1,))).rows[0]
    assert sale.values[9:12] == (Decimal(2), None, None)
