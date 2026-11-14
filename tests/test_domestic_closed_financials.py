from datetime import date
from decimal import Decimal
import sqlite3

from portfolio.domestic.closed_fund_analytics import closed_transaction_summaries, confirmed_holding_days, tiantian_realized_events, tiantian_return_issue
from portfolio.domestic.fund_transactions import SCHEMA as RAW_SCHEMA
from portfolio.domestic.storage import CALC_SCHEMA
from portfolio.ui.domestic_holdings_dashboard import closed_metrics, closed_trend_points


def test_confirmed_amounts_separate_conversions_and_deduplicate_subscriptions():
    raw = sqlite3.connect(':memory:'); raw.row_factory = sqlite3.Row
    calc = sqlite3.connect(':memory:'); calc.row_factory = sqlite3.Row
    raw.execute('PRAGMA foreign_keys=OFF'); raw.executescript(RAW_SCHEMA)
    calc.executescript(CALC_SCHEMA)
    def add(idx, day, kind, cash, shares, fee='0', status='成功'):
        raw.execute('''INSERT INTO raw_fund_transactions
            (id,account_id,source_platform,source_file_id,import_batch_id,source_page,source_record_no,
             confirmation_date,transaction_type_raw,fund_code,confirmed_amount,confirmed_shares,fee,
             confirmed_nav,confirmation_status,raw_data,parse_status)
             VALUES (?, 'T', 'TIANTIAN', 'f', 'b', 1, ?, ?, ?, '000001', ?, ?, ?, '1', ?, '{}', '正常')''',
             (str(idx),idx,day,kind,cash,shares,fee,status))
    add(1,'2024-01-01','买新基金','100','0')
    add(2,'2024-01-02','认购结果','100','100')
    add(3,'2024-02-01','超级转换-转出','60','60')
    add(4,'2024-03-01','卖基金','40','40')
    add(5,'2024-03-02','现金分红','2',None,None,None)
    raw.execute('''INSERT INTO raw_fund_transactions
        (id,account_id,source_platform,source_file_id,import_batch_id,source_page,source_record_no,
         confirmation_date,transaction_type_raw,fund_code,confirmed_amount,confirmed_shares,raw_data,parse_status)
         VALUES ('a','A','ALIPAY','f','b',1,1,'2024-01-01','用户买入','000001','70','70','{}','正常')''')
    calc.executemany('''INSERT INTO calc_fund_transactions
        (id,raw_transaction_id,account_id,fund_code,effective_date,transaction_type,confirmed_amount,status)
        VALUES (?,?,'A','000001',?,?,?,'正常')''', [
            ('a','a','2024-01-01','BUY','70'),('b','b','2024-02-01','TRANSFER_OUT','30')])
    result=closed_transaction_summaries(raw,calc,{('T','000001'),('A','000001')},'2024-12-31')
    row=result[('T','000001')]
    assert row['buy_amount']==100 and row['sell_amount']==40
    assert row['transfer_out_amount']==60 and row['dividend_amount']==2
    assert row['transfer_in_amount'] is None and row['fee_amount']==0
    assert result[('A','000001')]['buy_amount']==70 and result[('A','000001')]['transfer_out_amount']==30
    assert result[('A','000001')]['sell_amount'] is None
    raw.close();calc.close()


def test_closed_profit_is_weighted_and_events_keep_real_confirmation_dates():
    rows=[dict(realized=Decimal('20'),contributed=Decimal('100'),buy_amount=Decimal('100'),sell_amount=Decimal('120'),holding_days=10,
               closed_events=[('2023-01-05',Decimal('10')),('2024-03-01',Decimal('10'))]),
          dict(realized=Decimal('-10'),contributed=Decimal('200'),buy_amount=None,sell_amount=None,holding_days=20,
               closed_events=[('2024-04-01',Decimal('-10'))]),
          dict(realized=None,contributed=None,buy_amount=None,sell_amount=None,holding_days=None,closed_events=None)]
    stats=closed_metrics(rows)
    assert stats['buy']==100 and stats['sell']==120 and stats['realized']==10
    assert stats['rate']==Decimal(10)/Decimal(300)*100 and stats['holding_days']==15
    points,covered=closed_trend_points(rows,'year',date(2024,12,31))
    assert covered==2 and points==[(date(2024,3,1),Decimal(20)),(date(2024,4,1),Decimal(10))]


def test_realized_timeline_replays_each_sale_and_dividend_date():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE calc_tiantian_positions(account_id TEXT, fund_code TEXT, trade_date TEXT, holding_shares TEXT)')
    db.executemany('INSERT INTO calc_tiantian_positions VALUES (?,?,?,?)', [
        ('T','000001','2024-02-01','6'), ('T','000001','2024-03-01','0'), ('T','000001','2024-03-10','0')])
    def trade(day, kind, amount, shares, nav, status='成功'):
        return dict(confirmation_date=day,transaction_type_raw=kind,confirmed_amount=amount,
                    confirmed_shares=shares,confirmed_nav=nav,fee='0',confirmation_status=status,
                    parse_status='正常',linked_bank_card='')
    rows=[trade('2024-01-01','买基金','100','10','10'),
          trade('2024-02-01','卖基金','48','4','12'),
          trade('2024-03-01','卖基金','66','6','11'),
          trade('2024-03-10','现金分红','2',None,'0',None)]
    assert tiantian_realized_events(db,rows,'T','000001','2024-03-10','16') == [
        ('2024-02-01',Decimal('8')),('2024-03-01',Decimal('6')),('2024-03-10',Decimal('2'))]
    db.execute("DELETE FROM calc_tiantian_positions WHERE trade_date='2024-02-01'")
    assert tiantian_realized_events(db,rows,'T','000001','2024-03-10','16') is None
    db.close()


def test_realized_chart_uses_calendar_spacing_and_step_changes():
    from PySide6.QtCharts import QLineSeries
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_dashboard import TrendView

    app = QApplication.instance() or QApplication([])
    chart = TrendView()
    chart.render([(date(2020,1,1),Decimal(10)), (date(2020,1,11),Decimal(20)),
                  (date(2021,1,1),Decimal(15))], calendar_spacing=True, step_series=True)
    assert chart.days == [0, 10, 366]
    outline = next(s for s in chart.chart().series() if isinstance(s,QLineSeries))
    assert [(p.x(),p.y()) for p in outline.points()] == [(0,10),(10,10),(10,20),(366,20),(366,15)]
    chart.close()


def test_unreconciled_trade_explains_missing_profit():
    row = dict(confirmation_date='2021-01-18', transaction_type_raw='认购结果',
               confirmed_amount='1000.00', confirmed_shares='994.27', fee='5.96',
               confirmed_nav='1.0000', confirmation_status='成功', parse_status='正常', linked_bank_card='')
    assert '确认金额、份额、净值和手续费未能对账' in tiantian_return_issue([row])


def test_holding_days_excludes_zero_periods_and_requires_complete_history():
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE calc_fund_positions_daily(account_id TEXT, fund_code TEXT, trade_date TEXT, holding_shares TEXT)')
    db.executemany('INSERT INTO calc_fund_positions_daily VALUES (?,?,?,?)', [
        ('A','012414','2024-01-01','5'), ('A','012414','2024-01-02','0'),
        ('A','012414','2024-01-03','7'), ('A','012414','2024-01-04','0')])
    start, end = date(2024,1,1), date(2024,1,4)
    assert confirmed_holding_days(db,'calc_fund_positions_daily','A','012414',start,end) == 2
    db.execute("DELETE FROM calc_fund_positions_daily WHERE trade_date='2024-01-02'")
    assert confirmed_holding_days(db,'calc_fund_positions_daily','A','012414',start,end) is None
    db.close()


def test_alipay_opening_cost_transfer_returns_and_events():
    from portfolio.domestic.fund_returns import alipay_realized_events, confirmed_returns

    trades = [
        {"effective_date": "2022-01-10", "transaction_type": "BUY", "signed_shares": "100", "confirmed_amount": "200", "status": "正常"},
        {"effective_date": "2022-02-10", "transaction_type": "TRANSFER_OUT", "signed_shares": "-100", "confirmed_amount": "180", "status": "正常"},
        {"effective_date": "2022-03-10", "transaction_type": "SELL", "signed_shares": "-100", "confirmed_amount": "250", "status": "正常"},
        {"effective_date": "2022-03-20", "transaction_type": "CASH_DIVIDEND", "signed_shares": "0", "confirmed_amount": "15", "status": "正常"},
    ]
    res = confirmed_returns(
        trades, Decimal("0"), Decimal("0"),
        opening_shares=Decimal("100"), opening_cost=Decimal("150"), allow_transfer_amount=True,
    )
    # 总投入 = 150 + 200 = 350；总收回 = 180 + 250 + 15 = 445；净收益 = 95
    assert Decimal(res["pnl"]) == Decimal("95")
    assert Decimal(res["contributed"]) == Decimal("350")
    events = alipay_realized_events(
        trades, opening_shares=Decimal("100"), opening_cost=Decimal("150"), allow_transfer_amount=True,
    )
    assert len(events) == 3
    assert sum(Decimal(v) for _, v in events) == Decimal("95")


def test_platform_list_excludes_historical_baseline_from_fund_count():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.domestic_holdings_dashboard import PlatformList

    app = QApplication.instance() or QApplication([])
    widget = PlatformList(lambda _name: None)
    rows = [
        {"platform": "支付宝", "fund_code": "161725", "market_value": "0", "buy_amount": Decimal("100"), "sell_amount": Decimal("60"), "realized": Decimal("-40")},
        {"platform": "支付宝", "fund_code": "ALIPAY_PRE_2022_CLOSED", "market_value": "0", "buy_amount": None, "sell_amount": None, "realized": Decimal("20"), "is_historical_baseline": True},
    ]
    widget.render(rows, closed=True)
    # 支付宝基金数只计真实基金 1 只，历史收益包含基线合计 -20.00
    assert widget.table.item(0, 0).text() == "支付宝"
    assert widget.table.item(0, 3).text() == "-20.00"
    assert widget.table.item(0, 4).text() == "1"
    app.processEvents()
