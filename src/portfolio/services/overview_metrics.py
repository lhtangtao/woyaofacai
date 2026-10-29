"""Dashboard display metrics backed by the existing EXCEL_COMPAT holdings."""
from dataclasses import replace
from decimal import Decimal,localcontext


COST_NAME='当前持仓总成本'
COST_FORMULA='当前持仓累计净投入之和：累计买入金额减累计卖出金额，沿用原账本历史基准及费用口径；不含已清仓分组，不等于历史累计买入或外部入金。'


def overview_metrics(analytics,metrics=None):
    report=analytics.reports.get('当前持仓')
    value=None
    column=next((c for c in report.columns if c.startswith('累计净投入(')),None) if report is not None else None
    if column is not None:
        index=report.columns.index(column)
        values=[r.values[index] for r in report.rows]
        if all(isinstance(v,Decimal) and v.is_finite() for v in values):
            with localcontext() as context:
                context.prec=50
                value=sum(values,Decimal(0))
    return tuple(replace(m,name=COST_NAME,value=value,formula=COST_FORMULA,
                         day=analytics.scope.as_of.isoformat(),ratio=False,
                         status='COMPLETE' if value is not None else 'INCOMPLETE')
                 if m.name=='当前持仓胜率' else m for m in (analytics.metrics if metrics is None else metrics))
