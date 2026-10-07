"""Account-wide entitlements, separate from provider bills and payment collection.

Only an operator can grant a pilot. There is NO checkout, paid subscription or
automatic trial grant. A future payment adapter must verify payment server-side
before calling grant_period with a unique invoice/paid-period reference.
Credits are integer milli-points; 1 point represents $0.01 of metered model/tool
use at this policy version. Customer charges are capped at the job reservation;
provider overrun still remains on the raw usage ledger at the operator's cost.
"""
import argparse, json, math, os
from fastapi import Depends, HTTPException
from . import db

POLICY_VERSION='pilot-2026-10-04'
TICKS_PER_MILLI=100_000
PLANS={
    'pilot':{'label':'Deneme','credits':300,'workspaces':3,'proposed_usd':None},
    'personal':{'label':'Kişisel','credits':400,'workspaces':1,'proposed_usd':19},
    'business':{'label':'İşletme','credits':1200,'workspaces':3,'proposed_usd':49},
    'agency':{'label':'Ajans','credits':4000,'workspaces':5,'proposed_usd':149},
}
class EntitlementUnavailable(Exception):pass

def grant_period(account_id,plan_id,starts_at,ends_at,source,external_ref,evidence):
    """Immutable idempotent grant. Caller must already have verified its source."""
    if plan_id not in PLANS or source not in {'operator_pilot','verified_payment'}:raise ValueError('Unknown plan or source')
    if source=='operator_pilot' and plan_id!='pilot':raise ValueError('Operator pilot cannot create a paid plan')
    if source=='verified_payment' and plan_id=='pilot':raise ValueError('A pilot is not a payment')
    if not all(type(v) in {int,float} and math.isfinite(v) for v in [starts_at,ends_at]) or not 0<ends_at-starts_at<=366*86400:raise ValueError('Invalid period')
    if not isinstance(external_ref,str) or not 1<=len(external_ref)<=200 or len(evidence.strip())<10:raise ValueError('Verified reference and evidence required')
    plan=PLANS[plan_id]
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT * FROM entitlement_periods WHERE source=? AND external_ref=?',(source,external_ref)).fetchone()
        if existing:
            if any(existing[k]!=v for k,v in {'account_id':account_id,'plan_id':plan_id,'starts_at':starts_at,'ends_at':ends_at}.items()):raise ValueError('Reference reused with conflicting grant')
            return dict(existing) # Never replenish, un-revoke or modify a replay.
        if not conn.execute('SELECT 1 FROM users WHERE id=?',(account_id,)).fetchone():raise ValueError('Unknown account')
        if source=='operator_pilot' and conn.execute("SELECT 1 FROM entitlement_periods WHERE account_id=? AND source='operator_pilot'",(account_id,)).fetchone():raise ValueError('This account already received a pilot')
        if conn.execute('SELECT 1 FROM entitlement_periods WHERE account_id=? AND starts_at<? AND ends_at>?',(account_id,ends_at,starts_at)).fetchone():raise ValueError('Entitlement periods must not overlap, including revoked periods')
        pid=db.uid()
        conn.execute('INSERT INTO entitlement_periods VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,account_id,plan_id,POLICY_VERSION,plan['label'],starts_at,ends_at,plan['credits']*1000,plan['workspaces'],source,external_ref,0,evidence[:500],db.now()))
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),None,'operator' if source=='operator_pilot' else 'payment-adapter','entitlement.granted',json.dumps({'account_id':account_id,'period_id':pid,'source':source,'reference':external_ref}),db.now()))
        return dict(conn.execute('SELECT * FROM entitlement_periods WHERE id=?',(pid,)).fetchone())

def revoke(period_id,evidence):
    if len(evidence.strip())<10:raise ValueError('Evidence required')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM entitlement_periods WHERE id=?',(period_id,)).fetchone()
        if not row:raise ValueError('Unknown period')
        if row['revoked']:return
        conn.execute('UPDATE entitlement_periods SET revoked=1 WHERE id=?',(period_id,))
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),None,'operator','entitlement.revoked',json.dumps({'period_id':period_id,'evidence':evidence[:500]}),db.now()))

def account_for(conn,wid):
    row=conn.execute('SELECT owner_id FROM workspaces WHERE id=?',(wid,)).fetchone()
    if not row:raise EntitlementUnavailable('Çalışma alanı bulunamadı.')
    return row['owner_id']

def active_period(conn,account_id):
    at=db.now()
    return conn.execute('SELECT * FROM entitlement_periods WHERE account_id=? AND starts_at<=? AND ends_at>? AND revoked=0 ORDER BY starts_at DESC LIMIT 1',(account_id,at,at)).fetchone()

def balance(conn,period):
    row=conn.execute("SELECT COALESCE(SUM(CASE WHEN state='settled' THEN charged_milli ELSE 0 END),0) AS spent,COALESCE(SUM(CASE WHEN state IN ('reserved','uncertain') THEN reserved_milli ELSE 0 END),0) AS held FROM credit_ledger WHERE period_id=?",(period['id'],)).fetchone()
    return {'limit_milli':period['limit_milli'],'spent_milli':row['spent'],'held_milli':row['held'],'remaining_milli':max(0,period['limit_milli']-row['spent']-row['held'])}

def unresolved(conn,account_id):
    # Includes older periods; renewal or a different workspace must not bypass it.
    return conn.execute("SELECT COUNT(*) FROM usage_ledger u JOIN workspaces w ON w.id=u.workspace_id WHERE w.owner_id=? AND u.state='uncertain'",(account_id,)).fetchone()[0]+conn.execute("SELECT COUNT(*) FROM credit_ledger WHERE account_id=? AND state='uncertain' AND job_id NOT IN (SELECT job_id FROM usage_ledger WHERE state='uncertain')",(account_id,)).fetchone()[0]

def check_workspace_creation(conn,account_id):
    count=conn.execute('SELECT COUNT(*) FROM workspaces WHERE owner_id=?',(account_id,)).fetchone()[0]
    period=active_period(conn,account_id)
    limit=period['workspace_limit'] if period else 1 # One empty setup space, no AI use.
    if count>=limit:raise EntitlementUnavailable('Bu hesabın çalışma alanı sınırına ulaştın. Mevcut alanlarını kullanabilir veya paketini yönetebilirsin.')

def eligible(conn,wid,hold_ticks):
    account_id=account_for(conn,wid);period=active_period(conn,account_id)
    if not period:raise EntitlementUnavailable('Aktif kullanım hakkın yok. Paket ve kullanım ekranını kontrol et; kayıtların korunuyor.')
    if unresolved(conn,account_id):raise EntitlementUnavailable('Önceki bir işin kullanımı doğrulanıyor. Bu hesabın yeni işleri inceleme tamamlandığında açılacak.')
    hold=(hold_ticks+TICKS_PER_MILLI-1)//TICKS_PER_MILLI
    if balance(conn,period)['remaining_milli']<hold:raise EntitlementUnavailable('Kalan kullanım hakkın bu işi başlatmaya yetmiyor. Paket ve kullanım ekranından kontrol edebilirsin.')
    return account_id,period,hold

def reserve(conn,job,hold_ticks):
    existing=conn.execute('SELECT * FROM credit_ledger WHERE job_id=?',(job['id'],)).fetchone()
    if existing:
        if existing['workspace_id']!=job['workspace_id'] or existing['state']!='reserved':raise EntitlementUnavailable('Bu işin kullanım kaydı yeniden başlatılamaz.')
        period=active_period(conn,existing['account_id'])
        if not period or period['id']!=existing['period_id']:raise EntitlementUnavailable('İş sıradayken kullanım dönemi sona erdi. Yeni dönemde tekrar başlatabilirsin.')
        if unresolved(conn,existing['account_id']):raise EntitlementUnavailable('Önceki bir işin kullanımı doğrulanıyor. İnceleme tamamlanınca yeniden deneyebilirsin.')
        return
    account_id,period,hold=eligible(conn,job['workspace_id'],hold_ticks)
    conn.execute('INSERT INTO credit_ledger VALUES(?,?,?,?,?,?,?,?,?,?)',(job['id'],period['id'],account_id,job['workspace_id'],hold,None,TICKS_PER_MILLI,'reserved',db.now(),db.now()))

def settle(conn,job_id,ticks=None,no_request=False):
    row=conn.execute('SELECT * FROM credit_ledger WHERE job_id=?',(job_id,)).fetchone()
    if not row or row['state'] not in {'reserved','uncertain'}:return
    state='released' if no_request else 'uncertain' if ticks is None else 'settled'
    charge=0 if no_request else None if ticks is None else min(row['reserved_milli'],(ticks+row['ticks_per_milli']-1)//row['ticks_per_milli'])
    conn.execute('UPDATE credit_ledger SET state=?,charged_milli=?,updated=? WHERE job_id=?',(state,charge,db.now(),job_id))

def recover():
    db.query("UPDATE credit_ledger SET state='uncertain',updated=? WHERE state='reserved' AND job_id IN (SELECT id FROM jobs WHERE status='running')",(db.now(),))

def summary(wid,viewer_id):
    with db.connection() as conn:
        conn.execute('BEGIN')
        account_id=account_for(conn,wid);is_owner=account_id==viewer_id
        period=active_period(conn,account_id)
        latest=period or conn.execute('SELECT * FROM entitlement_periods WHERE account_id=? AND starts_at<=? ORDER BY starts_at DESC LIMIT 1',(account_id,db.now())).fetchone()
        result={'active':period is not None,'scope':'account','can_manage':is_owner,'checkout_available':False,'unit':'puan','milli_per_credit':1000,'policy_version':POLICY_VERSION,'period':None,'unresolved_count':unresolved(conn,account_id)}
        if latest:
            result['period']={k:latest[k] for k in ['label','plan_id','starts_at','ends_at','revoked','workspace_limit']}
            result.update(balance(conn,latest))
            result['period']['kind']='pilot' if latest['source']=='operator_pilot' else 'paid'
        else:result.update(limit_milli=0,spent_milli=0,held_milli=0,remaining_milli=0)
        if not period:result['remaining_milli']=0
        # Members can inspect their workspace receipts, never other client names/prompts.
        where,args=('c.account_id=?',(account_id,)) if is_owner else ('c.workspace_id=?',(wid,))
        result['receipts']=[dict(r) for r in conn.execute(f'SELECT c.job_id,c.state,c.reserved_milli,c.charged_milli,c.created,j.prompt,w.name AS workspace_name FROM credit_ledger c JOIN jobs j ON j.id=c.job_id JOIN workspaces w ON w.id=c.workspace_id WHERE {where} ORDER BY c.created DESC LIMIT 50',args)]
        result['workspace_count']=conn.execute('SELECT COUNT(*) FROM workspaces WHERE owner_id=?',(account_id,)).fetchone()[0] if is_owner else None
        return result

def register_routes(app,user,workspace):
    @app.get('/api/workspaces/{wid}/billing')
    def get_billing(wid,u=Depends(user)):
        workspace(wid,u);return summary(wid,u['id'])

    @app.get('/api/workspaces/{wid}/billing/quote')
    def quote(wid,agent_id:str='guide',u=Depends(user)):
        from . import providers,usage
        from .catalog import ROLES
        workspace(wid,u)
        if not any(r['id']==agent_id for r in ROLES) and not db.query('SELECT 1 FROM agents WHERE id=? AND workspace_id=?',(agent_id,wid),one=True):raise HTTPException(404,'İşdaş bulunamadı.')
        runtime=providers.available(wid);provider=runtime.get('provider','xai')
        hold=usage.reservation_for(agent_id,provider)
        with db.connection() as conn:
            conn.execute('BEGIN')
            reason='';allowed=False
            try:
                eligible(conn,wid,hold);allowed=runtime['ready']
                if not allowed:reason='Asistan bağlantısı henüz hazır değil.'
            except EntitlementUnavailable as e:reason=str(e)
        return {'max_milli':(hold+TICKS_PER_MILLI-1)//TICKS_PER_MILLI,'allowed':allowed,'reason':reason,'milli_per_credit':1000}

if __name__=='__main__':
    from dotenv import load_dotenv
    load_dotenv();db.DB_PATH=os.getenv('DATABASE_PATH','data/isdas.sqlite3');db.init()
    parser=argparse.ArgumentParser(description='Operator-only pilot entitlements; never records a payment')
    sub=parser.add_subparsers(dest='command',required=True)
    grant=sub.add_parser('pilot');grant.add_argument('--account',required=True);grant.add_argument('--evidence',required=True)
    cancel=sub.add_parser('revoke');cancel.add_argument('--period',required=True);cancel.add_argument('--evidence',required=True)
    args=parser.parse_args()
    if args.command=='pilot':
        prior=db.query("SELECT * FROM entitlement_periods WHERE account_id=? AND source='operator_pilot'",(args.account,),one=True)
        if prior:print('Pilot already exists; no allowance was replenished. Period:',prior['id'])
        else:
            start=db.now();record=grant_period(args.account,'pilot',start,start+14*86400,'operator_pilot','pilot:'+args.account,args.evidence);print('Pilot granted. Period:',record['id'])
    else:revoke(args.period,args.evidence);print('Future use disabled; history preserved.')
