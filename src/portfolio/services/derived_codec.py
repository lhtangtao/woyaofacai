"""Lossless, closed-type JSON for derived values; never execute persisted code."""
import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


def encode(value):
    if isinstance(value, Enum):
        return {'@': 'enum', 'type': type(value).__name__, 'value': value.value}
    if isinstance(value, Decimal):
        return {'@': 'decimal', 'value': str(value)}
    if isinstance(value, (date, datetime)):
        return {'@': type(value).__name__, 'value': value.isoformat()}
    if dataclasses.is_dataclass(value):
        return {'@': type(value).__name__, 'fields': {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}}
    if isinstance(value, dict):
        return {'@': 'dict', 'items': [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return {'@': type(value).__name__, 'items': [encode(v) for v in items]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'Unsupported derived value: {type(value).__name__}')


def decode(value):
    if not isinstance(value, dict) or '@' not in value:
        return value
    tag = value['@']
    if tag == 'decimal': return Decimal(value['value'])
    if tag == 'date': return date.fromisoformat(value['value'])
    if tag == 'datetime': return datetime.fromisoformat(value['value'])
    if tag == 'dict': return {decode(k): decode(v) for k, v in value['items']}
    if tag in ('tuple', 'list', 'set', 'frozenset'):
        return {'tuple': tuple, 'list': list, 'set': set, 'frozenset': frozenset}[tag](decode(v) for v in value['items'])
    from portfolio.domain import analytics, models
    from portfolio.services.charts_service import Chart, Series
    allowed = {c.__name__: c for c in (analytics.Issue, analytics.Event, analytics.Price, analytics.Position,
               analytics.ReportRow, analytics.Report, analytics.Metric, analytics.Snapshot, analytics.Analytics,
               models.QueryScope, Chart, Series)}
    if tag == 'enum' and value['type'] == 'Ledger': return models.Ledger(value['value'])
    if tag not in allowed: raise ValueError('Unknown derived type: ' + tag)
    return allowed[tag](**{k: decode(v) for k, v in value['fields'].items()})


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def loads(value):
    return decode(json.loads(value))
