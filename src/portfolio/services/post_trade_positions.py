"""Enrich a raw trade page with quantities and net costs from historical replay."""
from dataclasses import replace

from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.models import Ledger
from portfolio.engine.replay import replay


def attach_post_trade_positions(connection, page, *, cancel=None):
    if not page.rows:
        return page
    # Page/search/side/date-start filters affect display only. Replay all earlier
    # operations for these people/accounts/securities within the same DB snapshot.
    people = tuple(sorted({r.metadata['person_id'] for r in page.rows if r.metadata['person_id'] is not None}))
    accounts = tuple(sorted({r.metadata['account_id'] for r in page.rows}))
    securities = tuple(sorted({r.metadata['instrument_id'] for r in page.rows}))
    scope = replace(page.scope, person_ids=people if page.scope.ledger == Ledger.PERSON else None,
                    account_ids=accounts, instrument_ids=securities, start_date=None, search='',
                    audit=False, include_disabled=True, reporting_currency='USD')
    snapshot = load_snapshot(connection, scope, with_prices=False, cancel=cancel)
    snapshot.extras['broker_trades'] = [r for r in snapshot.extras['broker_trades']
                                        if r['business_date'] <= scope.as_of.isoformat()]
    positions, traces, _ = replay(snapshot, cancel=cancel)
    cost_diffs = snapshot.extras.get("cost_diffs", {})
    rows = []
    for row in page.rows:
        quantity, net, cost = traces.get(row.record_id, (None, None, None))
        cost_diff = cost_diffs.get(row.record_id)
        key = (row.metadata['person_id'] if scope.ledger == Ledger.PERSON else None,
               row.metadata['account_id'], row.metadata['instrument_id'])
        if key in positions and '成交粒度冲突' in positions[key].warnings:
            quantity = net = cost = None
        if quantity is None or cost is None:
            cost_diff = None
        if quantity is None:
            state = 'UNAVAILABLE'
            note = '该记录未进入有效回放或存在成交粒度冲突，操作后数量无法确认。撤销、参考和开账基准之前的记录不会作为有效成交重复计入。'
        elif quantity < 0:
            state = 'NEGATIVE'
            note = '该人员/账户/证券在此操作后回放为负数持仓，需要核对交易或期初数量。'
        elif row.metadata['link_status'] == 'REVIEW_REQUIRED':
            state = 'REVIEW_REQUIRED'
            note = '人员归属或成交关联待审核，此值是按现有账本回放的候选数量。'
        else:
            state = 'AVAILABLE'
            note = '该操作完成当时的持仓数量，按归属人员、券商账户和证券分别回放；已包含此前交易、拆股和划转。券商账本按真实账户独立回放。'
        values = list(row.values)
        values[9] = quantity
        values[10] = net
        values[11] = cost
        if quantity is None:
            cost_note = '该记录没有可信的操作后持仓状态，成本无法确认。'
        elif quantity <= 0:
            cost_note = '已清仓或持仓数量异常，不计算每股成本；累计净投入仍保留原值。'
        elif net is None:
            cost_note = '历史成交金额或划转成本缺失，不能用零代替，操作后成本无法确认。'
        else:
            cost_note = '净投入成本价＝（累计买入金额－累计卖出金额）÷操作后持仓数量；沿用账本金额口径，包含可信开账/划转成本。卖出收入超过投入时可为负数。'
            if state == 'REVIEW_REQUIRED':
                cost_note += '人员归属或成交关联待审核，此值为候选值。'
        rows.append(replace(row, values=tuple(values), metadata={**row.metadata,
                            'post_quantity': quantity, 'post_quantity_status': state, 'post_quantity_note': note,
                            'post_net': net, 'post_cost': cost, 'post_cost_note': cost_note,
                            'cost_diff': cost_diff}))
    return replace(page, rows=tuple(rows))
