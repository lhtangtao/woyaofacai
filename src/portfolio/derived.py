"""Explicit maintenance CLI: python -m portfolio.derived --help."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from portfolio.paths import DATABASE_PATH, quote_cache_path
from portfolio.services.derived_store import DerivedStore, exclusive, utcnow


def rebuild(source, *, mode='changed', start=date(2026,1,1), end=None, legacy=None, automatic=False):
    from portfolio.services.derived_builder import build_all,validate_generation,historical_people
    from portfolio.services.derived_dependencies import check_changes,source_identity
    from portfolio.services.derived_maintenance import write_progress,needs_rebuild
    from portfolio.services.quote_store import QuoteStore
    source=Path(source).resolve();published=DerivedStore(source)
    with exclusive(str(published.path)+'.build',wait=False):
        token=uuid.uuid4().hex;target=None
        def notify(**values):write_progress(source,job_id=token,**values)
        try:
            notify(state='INITIALIZING',message='正在后台初始化共享历史派生库。',completed_units=0,total_units=0)
            with exclusive(published.path):published.initialize()
            notify(state='CHECKING',message='正在检查原始数据及已有缓存依赖。')
            check_changes(source,published)
            quotes=QuoteStore(legacy or quote_cache_path(),source=source)
            with closing(quotes.connect()):pass
            if automatic and not needs_rebuild(source,published,quotes,start):
                generation=published.readiness()['generation']
                notify(state='PUBLISHED',message='历史缓存有效，直接读取已有计算代。',generation=generation)
                return {'state':'UNCHANGED','generation':generation}
            before=source_identity(source,financial_only=True)
            def historical_prices_revision():
                from portfolio.services.history_facts import readonly
                from portfolio.services.derived_store import digest
                from portfolio.domain.live_quotes import NY
                cutoff=datetime.now(NY).date().isoformat();result=[]
                with closing(readonly(source)) as c:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_fact_revisions'").fetchone():
                        result.extend(tuple(r) for r in c.execute('SELECT day,revision FROM history_fact_revisions WHERE day<? ORDER BY day',(cutoff,)))
                if quotes.path.is_file():
                    with closing(readonly(quotes.path)) as c:
                        if c.execute("SELECT 1 FROM sqlite_master WHERE name='quote_day_revisions'").fetchone():
                            result.extend(tuple(r) for r in c.execute('SELECT day,revision FROM quote_day_revisions WHERE day<? ORDER BY day',(cutoff,)))
                return digest(result)
            historical_before=historical_prices_revision()
            staging=published.path.with_name(published.path.name+'.building-'+token)
            target=DerivedStore(source,staging)
            if mode=='changed':
                with closing(published.connect()) as original,closing(sqlite3.connect(staging)) as copy:original.backup(copy)
                from importlib.resources import files
                with closing(sqlite3.connect(staging)) as c:c.executescript(files('portfolio').joinpath('resources/derived_schema.sql').read_text())
            else:target.initialize()
            with closing(target.connect(write=True)) as c:
                c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(token,mode,'RUNNING',utcnow(),None,'all historical owners'));c.commit()
            from portfolio.services.intraday_history import source_version
            expected_source=source_version(source)
            from portfolio.services.derived_builder import publish_partition,publish_reports
            from portfolio.services.derived_store import SourceChangedError
            def release(ledger,person,day):
                if source_version(source)!=expected_source:raise SourceChangedError('账本或收盘基准变化，停止旧快照，保留已校验分区。')
                return publish_partition(target,published,quotes,ledger,person,day,expected_source)
            def reports(ledger,person,inputs):
                if source_version(source)!=expected_source:raise SourceChangedError('账本或收盘基准变化，正在重新准备后台任务。')
                return publish_reports(target,published,ledger,person,expected_source,quotes,inputs)
            result=build_all(source,target,quotes,start=start,end=end,force=mode=='all',retry_errors=not automatic,
                             on_reports=reports if automatic else None,on_partition=release if automatic else None,
                             progress=(lambda message:None) if automatic else print,on_progress=lambda values:notify(**values))
            notify(state='VALIDATING',message='正在核对历史计算结果与数据库完整性。')
            result['validation']=validate_generation(target,historical_people(source))
            if source_identity(source,financial_only=True)!=before or (not automatic and historical_prices_revision()!=historical_before):
                raise ValueError('原始数据在生成期间变化，本代未发布，将自动重试。')
            with closing(target.connect(write=True)) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('新派生库完整性校验失败。')
                if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('新派生库外键校验失败。')
                c.execute('UPDATE jobs SET state=?,finished_at=?,detail=? WHERE id=?',('PUBLISHED',utcnow(),json.dumps(result),token))
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('generation',?)",(token,))
                from portfolio.services.derived_store import ALGORITHM
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('algorithm',?)",(ALGORITHM,));c.commit()
            notify(state='PUBLISHING',message='正在发布已核对的历史计算代。')
            if automatic:
                # Per-date releases are already visible. Merge reusable engine
                # units and the completion marker without replacing live rows.
                with closing(target.connect()) as c:
                    objects=c.execute('SELECT * FROM objects').fetchall()
                with exclusive(published.path),closing(published.connect(write=True)) as c:
                    c.execute('BEGIN IMMEDIATE')
                    for row in objects:
                        old=c.execute('SELECT updated_at FROM objects WHERE kind=? AND object_key=?',row[:2]).fetchone()
                        if not old or old[0]<row[6]:c.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',row)
                    c.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)',(token,mode,'PUBLISHED',utcnow(),utcnow(),json.dumps(result)))
                    c.execute("INSERT OR REPLACE INTO metadata VALUES ('generation',?)",(token,))
                    c.execute("INSERT OR REPLACE INTO metadata VALUES ('algorithm',?)",(ALGORITHM,));c.commit()
                staging.unlink(missing_ok=True)
                backup=None
            else:
                # A manual full rebuild still swaps atomically; retain live
                # partitions committed after the staging calculation.
                with exclusive(str(published.path)+'.intraday',wait=True),exclusive(str(published.path)+'.price-history',wait=True):
                    with closing(published.connect()) as c:
                        live=c.execute('SELECT ledger,person_id,day FROM partitions WHERE algorithm=?',(ALGORITHM,)).fetchall()
                    for ledger,person,day in live:publish_partition(published,target,quotes,ledger,person,day,expected_source)
                    with exclusive(published.path):
                        backup=published.path.with_name(published.path.name+'.previous-'+token)
                        with closing(published.connect()) as old,closing(sqlite3.connect(backup)) as copy:old.backup(copy)
                        backup.chmod(0o600);staging.chmod(0o600)
                        with staging.open('rb') as handle:os.fsync(handle.fileno())
                        os.replace(staging,published.path)
                        directory=os.open(str(published.path.parent),os.O_RDONLY)
                        try:os.fsync(directory)
                        finally:os.close(directory)
            gaps=result.get('errors',0)+result.get('partial',0)+result.get('report_errors',0)
            notify(state='PUBLISHED',generation=token,errors=result.get('errors',0),partial=result.get('partial',0),report_errors=result.get('report_errors',0),calendar_warnings=result.get('calendar_warnings',[]),
                   message=f"历史预计算已发布；失败分区 {result.get('errors',0)}，缺失/提示 {result.get('partial',0)}，报表失败 {result.get('report_errors',0)}；有效日期可正常查看。" if gaps else '全部历史人员预计算已完成，走势图已可读取。')
            return {'state':'PUBLISHED','generation':token,'previous':str(backup),**result}
        except BaseException as exc:
            if target is not None and target.available:
                with closing(target.connect(write=True)) as c:
                    c.execute('UPDATE jobs SET state=?,finished_at=?,detail=? WHERE id=?',('FAILED',utcnow(),type(exc).__name__,token));c.commit()
            notify(state='FAILED',error_type=type(exc).__name__,message='历史生成未完成，原有效代保留；后台将稍后重试，请查看本机日志。')
            raise


def prepare_startup(source=DATABASE_PATH,mode=None,*,background=False):
    source=Path(source).resolve()
    if not source.is_file():return {'state':'SOURCE_MISSING'}
    if mode:
        result=rebuild(source,mode=mode)
        if background:
            from portfolio.services.intraday_pipeline import start_pipeline
            start_pipeline(source)
        return result
    if background:
        from portfolio.services.intraday_pipeline import start_pipeline
        start_pipeline(source)
        from portfolio.services.derived_maintenance import schedule_changed
        return {'state':'CHECKING','maintenance':schedule_changed(source)}
    from portfolio.services.derived_dependencies import check_changes
    store=DerivedStore(source)
    if not store.readiness().get('initialized'):return {'state':'NOT_INITIALIZED'}
    return check_changes(source,store)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Money3安全初始化与派生数据维护（不会自动运行生产全量重算）')
    parser.add_argument('--db',type=Path,default=DATABASE_PATH)
    parser.add_argument('--legacy-quotes',type=Path,default=quote_cache_path())
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('inspect',help='只读数据库完整性及表分类盘点')
    coverage=commands.add_parser('coverage',help='只读逐证券/日/时段五分钟覆盖检查')
    coverage.add_argument('--start',type=date.fromisoformat,default=date(2026,1,1));coverage.add_argument('--end',type=date.fromisoformat,default=date.today())
    calendar=commands.add_parser('fetch-calendar',help='显式从长桥保存真实交易所日历，不查询个人资产')
    calendar.add_argument('--start',type=date.fromisoformat,default=date(2026,1,1))
    calendar.add_argument('--end',type=date.fromisoformat,default=date(date.today().year,12,31))
    prices=commands.add_parser('fetch-missing-prices',help='显式仅补取缺失证券/日期的五分钟行情')
    prices.add_argument('--start',type=date.fromisoformat,default=date(2026,1,1))
    prices.add_argument('--end',type=date.fromisoformat,default=date.today())
    commands.add_parser('init',help='创建空派生库，不写事实库')
    commands.add_parser('import-history',help='显式备份后向事实库追加旧历史行情；不覆盖原始表')
    build=commands.add_parser('rebuild',help='所有历史人员，离线本地计算并原子发布')
    build.add_argument('--mode',choices=['changed','all'],default='changed')
    build.add_argument('--start',type=date.fromisoformat,default=date(2026,1,1));build.add_argument('--end',type=date.fromisoformat)
    commands.add_parser('check',help='检测源数据变化并标记旧派生分区无效，不执行重算')
    commands.add_parser('status',help='只读查看派生库状态')
    args=parser.parse_args(argv);store=DerivedStore(args.db)
    if args.command=='inspect':
        from portfolio.services.history_facts import inventory
        result=inventory(args.db,args.legacy_quotes)
    elif args.command=='coverage':
        from portfolio.services.history_facts import coverage
        result=coverage(args.db,args.legacy_quotes,args.start,args.end)
    elif args.command=='fetch-calendar':
        from portfolio.paths import CREDENTIALS_PATH
        from portfolio.services.history_coverage import fetch_calendar
        from portfolio.services.quote_store import QuoteStore
        calendar=fetch_calendar(CREDENTIALS_PATH,args.start,args.end)
        QuoteStore(args.legacy_quotes).save_calendar(calendar.days,calendar.halves,(args.start,args.end))
        result={'state':'SAVED','days':len(calendar.days)}
    elif args.command=='fetch-missing-prices':
        from portfolio.paths import CREDENTIALS_PATH
        from portfolio.services.history_facts import fetch_missing_prices
        result=fetch_missing_prices(args.db,args.legacy_quotes,args.start,args.end,CREDENTIALS_PATH)
    elif args.command=='init':
        with exclusive(store.path):store.initialize()
        result=store.status()
    elif args.command=='import-history':
        from portfolio.services.history_facts import import_history
        result=import_history(args.db,args.legacy_quotes)
    elif args.command=='rebuild':result=rebuild(args.db,mode=args.mode,start=args.start,end=args.end,legacy=args.legacy_quotes)
    elif args.command=='check':result=prepare_startup(args.db)
    else:result=store.status()
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0


if __name__=='__main__':raise SystemExit(main())
