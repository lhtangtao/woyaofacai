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


def rebuild(source, *, mode='changed', start=date(2026,1,1), end=None, legacy=None):
    from portfolio.services.derived_builder import build_all
    from portfolio.services.derived_dependencies import check_changes, source_identity
    from portfolio.services.quote_store import QuoteStore
    source=Path(source).resolve();published=DerivedStore(source)
    # One build across processes. Publication lock is held only at the final swap;
    # readers continue using dependency-validated published generation meanwhile.
    with exclusive(str(published.path)+'.build',wait=False):
        if not published.available:raise ValueError('请先执行 python -m portfolio.derived init。')
        check_changes(source,published)
        before=source_identity(source)
        token=uuid.uuid4().hex
        staging=published.path.with_name(published.path.name+'.building-'+token)
        target=DerivedStore(source,staging)
        if mode=='changed':
            with closing(published.connect()) as original,closing(sqlite3.connect(staging)) as copy:original.backup(copy)
        else:target.initialize()
        started=utcnow()
        with closing(target.connect(write=True)) as c:
            c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(token,mode,'RUNNING',started,None,'all historical owners'));c.commit()
        try:
            result=build_all(source,target,QuoteStore(legacy or quote_cache_path(),source=source),start=start,end=end,force=mode=='all')
            if source_identity(source)!=before:raise ValueError('原始账本在生成期间变化，本代未发布；请重试增量重算。')
            with closing(target.connect(write=True)) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('新派生库完整性校验失败。')
                if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('新派生库外键校验失败。')
                c.execute('UPDATE jobs SET state=?,finished_at=?,detail=? WHERE id=?',('PUBLISHED',utcnow(),json.dumps(result),token))
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('generation',?)",(token,));c.commit()
            # Keep a recoverable SQLite-consistent previous generation.
            with exclusive(published.path):
                backup=published.path.with_name(published.path.name+'.previous-'+token)
                with closing(published.connect()) as old,closing(sqlite3.connect(backup)) as copy:old.backup(copy)
                backup.chmod(0o600);staging.chmod(0o600)
                with staging.open('rb') as handle:os.fsync(handle.fileno())
                os.replace(staging,published.path)
                directory=os.open(str(published.path.parent),os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            return {'state':'PUBLISHED','generation':token,'previous':str(backup),**result}
        except BaseException as exc:
            # Keep failed generation for diagnosis; never publish it or overwrite facts.
            with closing(target.connect(write=True)) as c:
                c.execute('UPDATE jobs SET state=?,finished_at=?,detail=? WHERE id=?',('FAILED',utcnow(),type(exc).__name__,token));c.commit()
            raise


def prepare_startup(source=DATABASE_PATH,mode=None):
    from portfolio.services.derived_dependencies import check_changes
    source=Path(source).resolve()
    if not source.is_file():return {'state':'SOURCE_MISSING'}
    store=DerivedStore(source)
    if mode:return rebuild(source,mode=mode)
    return check_changes(source,store)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Money3安全初始化与派生数据维护（不会自动运行生产全量重算）')
    parser.add_argument('--db',type=Path,default=DATABASE_PATH)
    parser.add_argument('--legacy-quotes',type=Path,default=quote_cache_path())
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('inspect',help='只读数据库完整性及表分类盘点')
    coverage=commands.add_parser('coverage',help='只读逐证券/日/时段五分钟覆盖检查')
    coverage.add_argument('--start',type=date.fromisoformat,default=date(2026,1,1));coverage.add_argument('--end',type=date.fromisoformat,default=date.today())
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
