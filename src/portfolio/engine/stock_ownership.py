"""Exact share ownership, independently reconciled to the broker ledger."""
from decimal import Decimal, localcontext

from portfolio.domain.analytics import Report, ReportRow, ratio, strict_sum


def ownership_alert_rows(bundle):
    """Check each account/security separately so opposite differences cannot cancel."""
    broker_rows = bundle.broker.reports['股票汇总'].rows
    owner_rows = [r for _, _, book in bundle.persons for r in book.reports['股票汇总'].rows]
    keys = {(r.metadata['account_id'], r.metadata['instrument_id']) for r in (*broker_rows, *owner_rows)}
    result = []
    for key in sorted(keys):
        def matching(rows):
            return [r for r in rows if (r.metadata['account_id'], r.metadata['instrument_id']) == key]
        physical, owners = matching(broker_rows), matching(owner_rows)
        broker = strict_sum(r.values[4] for r in physical)
        assigned = strict_sum(r.values[4] for r in owners)
        difference = broker - assigned if broker is not None and assigned is not None else None
        # The user's reconciliation policy ignores differences smaller than one share.
        if difference is None or abs(difference) >= Decimal('1'):
            sample = (physical or owners)[0]
            result.append((sample.values[0], sample.values[2], broker, assigned, difference))
    return tuple(result)


def ownership_choices(bundle):
    has_ge_ren = any(name == '个人' for _, name, _ in bundle.persons)
    choices = {}
    for person, name, book in bundle.persons:
        if (name == '个人') if has_ge_ren else (person == 1):
            continue
        for row in book.reports['股票汇总'].rows:
            qty = row.values[4]
            if qty is not None and qty != 0 and abs(qty) >= Decimal('0.5'):
                choices[row.metadata['instrument_id']] = row.values[2]
    return sorted(choices.items(), key=lambda item: (item[1], item[0]))


def ownership_report(bundle, instrument_id):
    def stocks(book):
        return [r for r in book.reports['股票汇总'].rows if r.metadata['instrument_id'] == instrument_id]

    with localcontext() as ctx:
        ctx.prec = 50
        broker = strict_sum(r.values[4] for r in stocks(bundle.broker))
        rows = []
        quantities = []
        for person, name, book in bundle.persons:
            quantity = strict_sum(r.values[4] for r in stocks(book))
            quantities.append(quantity)
            if quantity is None or quantity != 0:
                rows.append(ReportRow((name, quantity, ratio(quantity, broker)), {'person_id': person}))
        assigned = strict_sum(quantities)
        difference = broker-assigned if broker is not None and assigned is not None else None
        if difference is None or difference != 0:
            rows.append(ReportRow(('未分配／归属差异', difference, ratio(difference, broker)), {'person_id': 'residual'}))
    note = (f'截止 {bundle.aggregate.scope.as_of.isoformat()} · 按上方渠道范围统计所选股票。'
            '股数来自人员权益账本；占比=个人股数÷券商总股数。已清仓人员不展示，非零小数股保留。'
            '未分配／归属差异=券商总股数－人员合计，不自动分摊；未知不填零。多个渠道同时选中时合并股数。')
    return Report('股票归属', ('人员', '持有股数', '占渠道该股票比例'), tuple(rows), note), broker, assigned, difference
