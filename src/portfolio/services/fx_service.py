"""Public reference FX cache. Never opens the transaction source for writing."""
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROVIDER = 'ECB via Frankfurter'
ENDPOINT = 'https://api.frankfurter.dev/v2/providers/ecb/rates'
POLICY = '优先源库当日汇率；否则ECB参考汇率，非发布日沿用此前最近公布值（最多7天），保留实际发布日期；不含汇率损益。'


class FxStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    def identity(self):
        return (str(self.path), self.path.stat().st_mtime_ns, self.path.stat().st_size) if self.path.exists() else None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=30)
        c.execute('CREATE TABLE IF NOT EXISTS fx_rates (rate_date TEXT PRIMARY KEY, rate_e8 INTEGER NOT NULL CHECK(rate_e8>0), provider TEXT NOT NULL, source_url TEXT NOT NULL, payload_sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS imports (id INTEGER PRIMARY KEY, requested_start TEXT NOT NULL, requested_end TEXT NOT NULL, source_url TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL, record_count INTEGER NOT NULL)')
        return c

    def sync(self, start, end, *, force=False, fetch=None):
        start, end = date.fromisoformat(str(start)), min(date.fromisoformat(str(end)), date.today())
        if start > end:
            raise ValueError('汇率起始日不能晚于截止日。')
        with self._connect() as c:
            old = c.execute('SELECT MIN(rate_date),MAX(rate_date) FROM fx_rates').fetchone()
            covered = c.execute('SELECT requested_start,requested_end,fetched_at FROM imports ORDER BY id DESC LIMIT 1').fetchone()
        today_utc = datetime.now(timezone.utc).date().isoformat()
        if not force and covered and covered[0] <= str(start) and covered[1] >= str(end) and covered[2][:10] == today_utc:
            return {'count': 0, 'latest': old[1], 'cached': True}
        # Recheck a 7-day overlap for provider corrections, full history if needed.
        request_start = start - timedelta(days=7)
        if covered and covered[0] <= str(start) and old[1]:
            request_start = max(request_start, min(date.fromisoformat(old[1]), end) - timedelta(days=7))
        url = ENDPOINT + '?' + urlencode({'base': 'USD', 'quotes': 'CNY', 'from': str(request_start), 'to': str(end)})
        if fetch is None:
            def fetch(url):
                request = Request(url, headers={'User-Agent': 'MoneyPortfolio/0.3 FX reference cache'})
                with urlopen(request, timeout=25) as response:
                    return response.read(8_000_000)
        try:
            raw = fetch(url)
            data = json.loads(raw, parse_float=Decimal)
        except Exception as exc:
            raise ValueError('汇率下载失败，已保留本地汇率；请检查网络后在“文件→更新人民币汇率”重试。') from exc
        if not isinstance(data, list) or not data:
            raise ValueError('汇率接口没有返回有效数据，未更新本地缓存。')
        stamp = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(raw).hexdigest()
        rows, seen = [], set()
        for item in data:
            day = date.fromisoformat(item['date'])
            value = Decimal(str(item['rate']))
            if item.get('base') != 'USD' or item.get('quote') != 'CNY' or not request_start <= day <= end or not value.is_finite() or not Decimal('0') < value < Decimal('1000') or str(day) in seen:
                raise ValueError('汇率响应日期、币种或数值校验失败，未写入。')
            scaled = int((value * Decimal(100000000)).to_integral_value())
            if scaled <= 0:
                raise ValueError('汇率精度校验失败。')
            rows.append((str(day), scaled, PROVIDER, url, digest, stamp))
            seen.add(str(day))
        with self._connect() as c:
            c.executemany('INSERT INTO fx_rates VALUES (?,?,?,?,?,?) ON CONFLICT(rate_date) DO UPDATE SET rate_e8=excluded.rate_e8,provider=excluded.provider,source_url=excluded.source_url,payload_sha256=excluded.payload_sha256,fetched_at=excluded.fetched_at', rows)
            c.execute('INSERT INTO imports(requested_start,requested_end,source_url,payload_json,payload_sha256,fetched_at,record_count) VALUES(?,?,?,?,?,?,?)', (str(start), str(end), url, raw.decode('utf-8'), digest, stamp, len(rows)))
            latest = c.execute('SELECT MAX(rate_date) FROM fx_rates').fetchone()[0]
        return {'count': len(rows), 'latest': latest, 'cached': False}

    def supplement(self, source_rows, through):
        """Preserve source exact dates/conflicts; no look-ahead or unbounded stale fills."""
        if not self.path.exists():
            return list(source_rows)
        with sqlite3.connect(f'{self.path.as_uri()}?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            rates = [dict(r) for r in c.execute('SELECT * FROM fx_rates ORDER BY rate_date')]
            coverage = c.execute('SELECT MAX(requested_end) FROM imports').fetchone()[0]
        result = list(source_rows)
        occupied = {r['rate_date'] for r in source_rows if (r['base_currency'], r['quote_currency']) in [('USD','CNY'),('CNY','USD')]}
        if not rates or not coverage:
            return result
        end = min(date.fromisoformat(str(through)), date.fromisoformat(coverage), date.today())
        cursor, index = date.fromisoformat(rates[0]['rate_date']), 0
        while cursor <= end:
            while index + 1 < len(rates) and rates[index + 1]['rate_date'] <= str(cursor):
                index += 1
            r = rates[index]
            if str(cursor) not in occupied and (cursor - date.fromisoformat(r['rate_date'])).days <= 7:
                result.append({'rate_date': str(cursor), 'base_currency': 'USD', 'quote_currency': 'CNY', 'rate_e8': r['rate_e8'], 'provider': r['provider'], 'source_record_id': None, 'published_date': r['rate_date'], 'source_url': r['source_url'], 'payload_sha256': r['payload_sha256']})
            cursor += timedelta(days=1)
        return result
