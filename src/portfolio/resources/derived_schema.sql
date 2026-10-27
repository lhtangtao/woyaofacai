PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects(
 kind TEXT NOT NULL, object_key TEXT NOT NULL, dependency TEXT NOT NULL,
 algorithm TEXT NOT NULL, payload BLOB NOT NULL, checksum TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(kind,object_key));
CREATE TABLE IF NOT EXISTS intraday_contributions(
 ledger TEXT NOT NULL,person_id INTEGER NOT NULL,account_id INTEGER NOT NULL,
 instrument_id INTEGER NOT NULL,day TEXT NOT NULL,stamp TEXT NOT NULL,
 channel TEXT NOT NULL,ticker TEXT NOT NULL,quantity TEXT,market_value TEXT,
 opening_value TEXT,buy TEXT,sell TEXT,pnl TEXT,status TEXT NOT NULL,
 PRIMARY KEY(ledger,person_id,account_id,instrument_id,day,stamp));
CREATE INDEX IF NOT EXISTS ix_intraday_person_day ON intraday_contributions(person_id,day,stamp);
CREATE INDEX IF NOT EXISTS ix_intraday_person_channel ON intraday_contributions(person_id,channel,day,stamp);
CREATE INDEX IF NOT EXISTS ix_intraday_person_security ON intraday_contributions(person_id,instrument_id,day,stamp);
CREATE INDEX IF NOT EXISTS ix_intraday_security_time ON intraday_contributions(instrument_id,stamp);
CREATE TABLE IF NOT EXISTS partitions(
 ledger TEXT NOT NULL,person_id INTEGER NOT NULL,day TEXT NOT NULL,
 dependency TEXT NOT NULL,algorithm TEXT NOT NULL,status TEXT NOT NULL,warning TEXT NOT NULL,
 updated_at TEXT NOT NULL,PRIMARY KEY(ledger,person_id,day));
CREATE TABLE IF NOT EXISTS report_details(
 ledger TEXT NOT NULL,person_id INTEGER NOT NULL,report TEXT NOT NULL,
 period TEXT NOT NULL,account_id INTEGER NOT NULL,instrument_id INTEGER NOT NULL,
 ordinal INTEGER NOT NULL,payload BLOB NOT NULL,
 PRIMARY KEY(ledger,person_id,report,period,account_id,instrument_id,ordinal));
CREATE INDEX IF NOT EXISTS ix_reports_person_period ON report_details(person_id,report,period);
CREATE INDEX IF NOT EXISTS ix_reports_account_period ON report_details(person_id,account_id,report,period);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,mode TEXT NOT NULL,state TEXT NOT NULL,
 started_at TEXT NOT NULL,finished_at TEXT,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_manifest(table_name TEXT NOT NULL,row_key TEXT NOT NULL,
 digest TEXT NOT NULL,day TEXT,person_id INTEGER,account_id INTEGER,instrument_id INTEGER,
 PRIMARY KEY(table_name,row_key));
CREATE TABLE IF NOT EXISTS invalidations(id INTEGER PRIMARY KEY,detected_at TEXT NOT NULL,
 table_name TEXT NOT NULL,day TEXT,person_id INTEGER,account_id INTEGER,instrument_id INTEGER,
 reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS partition_inputs(
 ledger TEXT NOT NULL,person_id INTEGER NOT NULL,day TEXT NOT NULL,quote_revision TEXT NOT NULL,
 PRIMARY KEY(ledger,person_id,day));
