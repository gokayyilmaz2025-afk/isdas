import sqlite3, os, uuid, json, time
from pathlib import Path
DB_PATH = os.getenv('DATABASE_PATH','data/isdas.sqlite3')
class Connection(sqlite3.Connection):
    # sqlite3's ordinary context manager commits/rolls back but does not close.
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def connection():
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DB_PATH,timeout=20,factory=Connection)
    try:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        return db
    except Exception:
        db.close();raise
def query(sql,args=(),one=False):
    with connection() as db:
        result=db.execute(sql,args)
        if result.description:
            rows=[dict(r) for r in result.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return result.rowcount
def uid():return uuid.uuid4().hex
def now():return time.time()
def init():
    with connection() as db:
        db.executescript('''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS account_state(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,verified_at REAL,credential_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS account_tokens(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,purpose TEXT NOT NULL,email TEXT NOT NULL,credential_version INTEGER NOT NULL,expires REAL NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS account_throttles(bucket TEXT NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS account_throttle_lookup ON account_throttles(bucket,created);
CREATE TABLE IF NOT EXISTS mail_outbox(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,purpose TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,token_hash TEXT,expires REAL NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS mail_queue ON mail_outbox(state,created);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,owner_id TEXT REFERENCES users(id),name TEXT NOT NULL,kind TEXT NOT NULL,sector TEXT DEFAULT '',website TEXT DEFAULT '',brand_voice TEXT DEFAULT '',timezone TEXT DEFAULT 'Europe/Istanbul',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_onboarding(workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),step TEXT NOT NULL,goal TEXT NOT NULL,revision INTEGER NOT NULL,attempt_key TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS setup_requests(user_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,payload_hash TEXT NOT NULL,workspace_id TEXT NOT NULL REFERENCES workspaces(id),created REAL NOT NULL,PRIMARY KEY(user_id,request_key));
CREATE TABLE IF NOT EXISTS site_previews(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),user_id TEXT NOT NULL REFERENCES users(id),payload TEXT NOT NULL,expires REAL NOT NULL,memory_id TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memberships(workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id),role TEXT NOT NULL DEFAULT 'member',PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS membership_invites(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),email TEXT NOT NULL,role TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,invited_by TEXT NOT NULL REFERENCES users(id),state TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,accepted_by TEXT REFERENCES users(id),updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS pending_member_invite ON membership_invites(workspace_id,email) WHERE state='pending';
CREATE TABLE IF NOT EXISTS client_reviews(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),post_id TEXT NOT NULL REFERENCES content_posts(id),reviewer_id TEXT NOT NULL REFERENCES users(id),requested_by TEXT NOT NULL REFERENCES users(id),post_revision INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,comment TEXT NOT NULL DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_review ON client_reviews(post_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS assigned_reviews ON client_reviews(workspace_id,reviewer_id,created);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,title TEXT NOT NULL,content TEXT NOT NULL,source TEXT DEFAULT 'manual',created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,name TEXT NOT NULL,title TEXT NOT NULL,prompt TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id),agent_id TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,output TEXT DEFAULT '',error TEXT DEFAULT '',provider TEXT DEFAULT '',usage TEXT DEFAULT '{}',created REAL NOT NULL,updated REAL NOT NULL,attempt INTEGER DEFAULT 0,cancel_requested INTEGER DEFAULT 0,idempotency_key TEXT NOT NULL,UNIQUE(workspace_id,idempotency_key));
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,job_id TEXT REFERENCES jobs(id),title TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,status TEXT DEFAULT 'draft',created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,title TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,notes TEXT DEFAULT '',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS routines(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT REFERENCES users(id),agent_id TEXT NOT NULL,prompt TEXT NOT NULL,interval_hours INTEGER NOT NULL,next_run REAL NOT NULL,enabled INTEGER DEFAULT 1,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,provider TEXT NOT NULL,encrypted_token TEXT NOT NULL,account_label TEXT DEFAULT '',updated REAL NOT NULL,UNIQUE(workspace_id,provider));
CREATE TABLE IF NOT EXISTS oauth_states(state TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),workspace_id TEXT REFERENCES workspaces(id),provider TEXT NOT NULL,verifier TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id),connection_id TEXT REFERENCES connections(id),kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',result TEXT DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,workspace_id TEXT,user_id TEXT,event TEXT NOT NULL,detail TEXT DEFAULT '',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage_limits(workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,monthly_ticks INTEGER NOT NULL CHECK(monthly_ticks>=0));
CREATE TABLE IF NOT EXISTS usage_ledger(job_id TEXT PRIMARY KEY REFERENCES jobs(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),period TEXT NOT NULL,reserved_ticks INTEGER NOT NULL CHECK(reserved_ticks>=0),actual_ticks INTEGER CHECK(actual_ticks>=0),state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain','released')),provider TEXT NOT NULL,request_id TEXT DEFAULT '',note TEXT DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS usage_workspace_period ON usage_ledger(workspace_id,period);
CREATE INDEX IF NOT EXISTS jobs_workspace_created ON jobs(workspace_id,created);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status,created);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,agent_id TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS conversations_workspace_updated ON conversations(workspace_id,updated);
CREATE TABLE IF NOT EXISTS entitlement_periods(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES users(id),plan_id TEXT NOT NULL,plan_version TEXT NOT NULL,label TEXT NOT NULL,starts_at REAL NOT NULL,ends_at REAL NOT NULL,limit_milli INTEGER NOT NULL CHECK(limit_milli>=0),workspace_limit INTEGER NOT NULL CHECK(workspace_limit>=1),source TEXT NOT NULL,external_ref TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,evidence TEXT NOT NULL,created REAL NOT NULL,UNIQUE(source,external_ref),CHECK(ends_at>starts_at));
CREATE INDEX IF NOT EXISTS entitlement_account_period ON entitlement_periods(account_id,starts_at,ends_at);
CREATE TABLE IF NOT EXISTS credit_ledger(job_id TEXT PRIMARY KEY REFERENCES jobs(id),period_id TEXT NOT NULL REFERENCES entitlement_periods(id),account_id TEXT NOT NULL REFERENCES users(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),reserved_milli INTEGER NOT NULL CHECK(reserved_milli>0),charged_milli INTEGER CHECK(charged_milli>=0 AND charged_milli<=reserved_milli),ticks_per_milli INTEGER NOT NULL CHECK(ticks_per_milli>0),state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain','released')),created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS credits_account_period ON credit_ledger(account_id,period_id);
CREATE TABLE IF NOT EXISTS media_assets(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,kind TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS media_workspace_created ON media_assets(workspace_id,created);
CREATE TABLE IF NOT EXISTS media_blobs(asset_id TEXT PRIMARY KEY REFERENCES media_assets(id) ON DELETE CASCADE,data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS brand_profiles(workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS content_plans(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),title TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS content_posts(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),plan_id TEXT REFERENCES content_plans(id),job_id TEXT REFERENCES jobs(id),user_id TEXT NOT NULL REFERENCES users(id),payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',revision INTEGER NOT NULL DEFAULT 1,approved_by TEXT,approved_at REAL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS content_workspace_updated ON content_posts(workspace_id,updated);
CREATE TABLE IF NOT EXISTS content_versions(post_id TEXT NOT NULL REFERENCES content_posts(id),revision INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,user_id TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(post_id,revision));
CREATE TABLE IF NOT EXISTS image_requests(job_id TEXT PRIMARY KEY REFERENCES jobs(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),post_id TEXT NOT NULL REFERENCES content_posts(id),expected_revision INTEGER NOT NULL,prompt TEXT NOT NULL,aspect_ratio TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS system_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,kind TEXT NOT NULL,bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,characters INTEGER NOT NULL,extraction TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL,updated REAL NOT NULL,UNIQUE(workspace_id,sha256));
CREATE TABLE IF NOT EXISTS document_blobs(document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS document_chunks(id INTEGER PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL REFERENCES workspaces(id),ordinal INTEGER NOT NULL,label TEXT NOT NULL,body TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,UNIQUE(document_id,ordinal));
CREATE INDEX IF NOT EXISTS document_workspace ON documents(workspace_id,created);
CREATE INDEX IF NOT EXISTS document_chunk_workspace ON document_chunks(workspace_id,document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(title,text,content='document_chunks',content_rowid='id',tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS document_chunk_insert AFTER INSERT ON document_chunks BEGIN
  INSERT INTO document_search(rowid,title,text) VALUES(new.id,new.title,new.text);
END;
CREATE TRIGGER IF NOT EXISTS document_chunk_delete AFTER DELETE ON document_chunks BEGIN
  INSERT INTO document_search(document_search,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
END;
CREATE TABLE IF NOT EXISTS job_document_sources(job_id TEXT NOT NULL REFERENCES jobs(id),document_id TEXT NOT NULL,chunk_id INTEGER NOT NULL,code TEXT NOT NULL,payload TEXT NOT NULL,cited INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(job_id,code));
''')
        # Additive migrations keep local data and action history intact.
        db.execute('INSERT OR IGNORE INTO account_state(user_id) SELECT id FROM users')
        columns={r['name'] for r in db.execute('PRAGMA table_info(actions)')}
        for name,definition in {'payload_hash':"TEXT DEFAULT ''",'job_id':'TEXT','account_label':"TEXT DEFAULT ''",'approved_by':'TEXT'}.items():
            if name not in columns:db.execute(f'ALTER TABLE actions ADD COLUMN {name} {definition}')
        columns={r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
        for name,definition in {'conversation_id':'TEXT REFERENCES conversations(id)','turn_sequence':'INTEGER','conversation_start':'INTEGER NOT NULL DEFAULT 0','context_info':"TEXT NOT NULL DEFAULT '{}'",'task_kind':"TEXT NOT NULL DEFAULT 'assistant'",'document_ids':"TEXT NOT NULL DEFAULT '[]'"}.items():
            if name not in columns:db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS conversation_turn_sequence ON jobs(conversation_id,turn_sequence) WHERE conversation_id IS NOT NULL')
        from . import notifications
        notifications.install(db)
        from . import agenda
        agenda.install(db)
def audit(workspace,user,event,detail=''):
    query('INSERT INTO audit VALUES(?,?,?,?,?,?)',(uid(),workspace,user,event,detail,now()))
