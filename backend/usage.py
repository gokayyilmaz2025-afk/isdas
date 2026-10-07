"""Admission budgets and receipts. Running requests CAN exceed reservations.

xAI returns aggregate per-request cost in 10^10 ticks/USD. A lost response does
not prove zero cost. Uncertain costs require an operator's billing reconciliation.
Calendar periods are UTC. This is not the customer payment/subscription system.
"""
import argparse, datetime, json, os
from decimal import Decimal
from . import db,billing
TICKS=10_000_000_000
class BudgetUnavailable(Exception):pass

def dollars_to_ticks(value):
    amount=Decimal(str(value))
    if not amount.is_finite() or amount<0 or amount>1_000_000:raise ValueError('Invalid budget amount')
    ticks=amount*TICKS
    if ticks!=ticks.to_integral_value():raise ValueError('Budget precision exceeds USD ticks')
    return int(ticks)

def period():return datetime.datetime.fromtimestamp(db.now(),datetime.timezone.utc).strftime('%Y-%m')

def reservation_for(agent_id,provider,task_kind='assistant'):
    variable,default='JOB_RESERVE_USD','0.25'
    if task_kind=='image':variable,default='IMAGE_JOB_RESERVE_USD','0.10'
    elif provider=='hermes':variable,default='HERMES_JOB_RESERVE_USD','2'
    elif agent_id in {'research','sales','seo'}:variable,default='RESEARCH_JOB_RESERVE_USD','1'
    result=dollars_to_ticks(os.getenv(variable,default))
    if result<=0:raise ValueError('Job reservation must be positive')
    return result

def _balance(conn,wid=None,billing_period=None):
    cycle=billing_period or period()
    args=(cycle,) if wid is None else (cycle,wid)
    where='period=?'+('' if wid is None else ' AND workspace_id=?')
    row=conn.execute(f'''SELECT
      COALESCE(SUM(CASE WHEN state='settled' THEN actual_ticks ELSE 0 END),0) AS spent,
      COALESCE(SUM(CASE WHEN state IN ('reserved','uncertain') THEN reserved_ticks ELSE 0 END),0) AS held
      FROM usage_ledger WHERE {where}''',args).fetchone()
    if wid is None:limit=dollars_to_ticks(os.getenv('GLOBAL_MONTHLY_BUDGET_USD','100'))
    else:
        record=conn.execute('SELECT monthly_ticks FROM usage_limits WHERE workspace_id=?',(wid,)).fetchone()
        limit=record['monthly_ticks'] if record else dollars_to_ticks(os.getenv('DEFAULT_MONTHLY_BUDGET_USD','10'))
    return {'limit_ticks':limit,'spent_ticks':row['spent'],'held_ticks':row['held'],'remaining_ticks':max(0,limit-row['spent']-row['held'])}

def summary(wid):
    with db.connection() as conn:
        conn.execute('BEGIN')
        cycle=period();result=_balance(conn,wid,cycle);result['period']=cycle
        result['unresolved_count']=conn.execute("SELECT COUNT(*) FROM usage_ledger WHERE workspace_id=? AND state='uncertain'",(wid,)).fetchone()[0]
        result['receipts']=[dict(r) for r in conn.execute('SELECT job_id,period,state,reserved_ticks,actual_ticks,provider,created FROM usage_ledger WHERE workspace_id=? ORDER BY created DESC LIMIT 100',(wid,))]
        result.update(ticks_per_usd=TICKS,scope='model_and_server_side_tools_only',running_request_may_exceed_reservation=True)
        return result

def reserve(job,provider):
    hold=reservation_for(job['agent_id'],provider,job.get('task_kind','assistant'))
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        cycle=period()
        current=conn.execute('SELECT status,cancel_requested FROM jobs WHERE id=? AND workspace_id=?',(job['id'],job['workspace_id'])).fetchone()
        if not current or current['status']!='running':raise BudgetUnavailable('Bu iş yeniden başlatılamaz; mevcut durumunu kontrol et.')
        if current['cancel_requested']:return False
        if not conn.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=(SELECT user_id FROM jobs WHERE id=?) AND role IN ('owner','member')",(job['workspace_id'],job['id'])).fetchone():return False
        if conn.execute('SELECT 1 FROM usage_ledger WHERE job_id=?',(job['id'],)).fetchone():raise BudgetUnavailable('Bu iş için kullanım kaydı zaten var. Aynı iş tekrar ücretlendirilmedi.')
        if conn.execute("SELECT 1 FROM usage_ledger WHERE workspace_id=? AND state='uncertain' LIMIT 1",(job['workspace_id'],)).fetchone():raise BudgetUnavailable('Önceki bir işin kullanım tutarı doğrulanıyor. Yeni işler için destek incelemesi gerekiyor.')
        if _balance(conn,job['workspace_id'],cycle)['remaining_ticks']<hold:raise BudgetUnavailable('Bu ayın kullanım hakkı bu işi başlatmaya yetmiyor.')
        if _balance(conn,None,cycle)['remaining_ticks']<hold:raise BudgetUnavailable('Hizmetin bu dönemki çalışma kapasitesi doldu. Yeni iş şu anda başlatılamıyor.')
        billing.reserve(conn,job,hold)
        conn.execute('INSERT INTO usage_ledger(job_id,workspace_id,period,reserved_ticks,state,provider,created,updated) VALUES(?,?,?,?,?,?,?,?)',(job['id'],job['workspace_id'],cycle,hold,'reserved',provider,db.now(),db.now()))
        return True

def reported_ticks(usage,provider):
    # Hermes may return only its final call's usage. No aggregate assumption.
    if provider!='xai' or not isinstance(usage,dict):return None
    value=usage.get('cost_in_usd_ticks')
    if isinstance(value,str) and value.isascii() and value.isdigit() and len(value)<19:value=int(value)
    if type(value) is not int or not 0<=value<=1_000_000*TICKS:return None
    return value

def settle(conn,job_id,usage=None,provider='',request_id='',no_request=False):
    row=conn.execute('SELECT state FROM usage_ledger WHERE job_id=?',(job_id,)).fetchone()
    if not row:
        if no_request:billing.settle(conn,job_id,no_request=True)
        return
    if row['state'] not in {'reserved','uncertain'}:return
    ticks=0 if no_request else reported_ticks(usage,provider)
    state='released' if no_request else 'settled' if ticks is not None else 'uncertain'
    note='' if ticks is not None else 'Sağlayıcı tutarı doğrulanamadı; fatura eşleştirmesi gerekli.'
    conn.execute('UPDATE usage_ledger SET actual_ticks=?,state=?,request_id=?,note=?,updated=? WHERE job_id=?',(ticks,state,str(request_id or '')[:250],note,db.now(),job_id))
    billing.settle(conn,job_id,ticks,no_request)

def recover():
    billing.recover()
    db.query("UPDATE usage_ledger SET state='uncertain',note='Sunucu kesintisi sonrası fatura eşleştirmesi gerekli.',updated=? WHERE state='reserved'",(db.now(),))

def reconcile(job_id,cost_usd,reason):
    if len(reason.strip())<10:raise ValueError('A billing evidence reference is required')
    cost=dollars_to_ticks(cost_usd)
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT workspace_id,state FROM usage_ledger WHERE job_id=?',(job_id,)).fetchone()
        if not row or row['state']!='uncertain':raise ValueError('Only uncertain receipts can be reconciled')
        conn.execute("UPDATE usage_ledger SET actual_ticks=?,state='settled',note=?,updated=? WHERE job_id=?",(cost,reason[:500],db.now(),job_id))
        billing.settle(conn,job_id,cost)
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),row['workspace_id'],'operator','usage.reconciled',json.dumps({'job_id':job_id,'ticks':cost,'reason':reason[:500]}),db.now()))

if __name__=='__main__':
    from dotenv import load_dotenv
    load_dotenv();db.DB_PATH=os.getenv('DATABASE_PATH','data/isdas.sqlite3')
    parser=argparse.ArgumentParser(description='Operator-only reconciliation from verified provider billing')
    parser.add_argument('--job',required=True);parser.add_argument('--cost-usd',required=True);parser.add_argument('--evidence',required=True)
    args=parser.parse_args();db.init();reconcile(args.job,args.cost_usd,args.evidence)
    print('Receipt reconciled; audit entry saved.')
