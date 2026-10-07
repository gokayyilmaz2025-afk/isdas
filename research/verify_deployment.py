"""Exercise the exact release archive with isolated local production-mode processes."""
import json,os,socket,subprocess,sys,tempfile,time,zipfile
from contextlib import nullcontext
from pathlib import Path
import httpx
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import ops
from deploy.init_config import create


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def launch(folder,database,p,env):
    return subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(p),'--workers','1','--no-access-log'],cwd=folder,env={**env,'DATABASE_PATH':str(database)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')


def ready(proc,p,status):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if proc.poll() is not None:raise RuntimeError('Isolated server exited during startup.')
        try:
            r=httpx.get(f'http://127.0.0.1:{p}/api/ready',timeout=1)
            if r.status_code==status:return
        except httpx.HTTPError:pass
        time.sleep(.1)
    raise RuntimeError('Isolated server did not reach the expected readiness state.')


def run(archive):
    archive=Path(archive).resolve();checks={};processes=[]
    env={**os.environ,'APP_ENV':'production','APP_ORIGIN':'https://release.example.test','PUBLIC_ORIGIN':'https://release.example.test','WORKER_ENABLED':'true','REGISTRATION_ENABLED':'false','ENCRYPTION_KEY':Fernet.generate_key().decode(),'XAI_API_KEY':'','HERMES_RUNTIME_MAP':str(ROOT/'tmp/no-test-runtime.json')}
    os.environ['BACKUP_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    # Retain the empty test DB and extracted release as drill evidence. Windows can
    # delay unmapping SQLite SHM after process exit; cleanup is not an acceptance test.
    with nullcontext(tempfile.mkdtemp(prefix='release-drill-',dir=ROOT/'tmp')) as temp_name:
        temp=Path(temp_name).resolve()
        assert temp.is_relative_to((ROOT/'tmp').resolve())
        folder=temp/'release';folder.mkdir()
        try:
            with zipfile.ZipFile(archive) as z:
                for name in z.namelist():
                    assert not name.startswith(('/','\\')) and '..' not in Path(name).parts
                    assert not any(part in {'data','research','.env','.venv','node_modules'} for part in Path(name).parts)
                z.extractall(folder)
            result=subprocess.run([sys.executable,'deploy/verify_release.py'],cwd=folder,capture_output=True,text=True,check=True)
            checks['archive_manifest_verified']=json.loads(result.stdout)['verified']
            if (folder/'backend/document_parser.py').exists():
                result=subprocess.run([sys.executable,'-m','backend.document_parser',str(ROOT/'tmp/knowledge-fixture.docx'),'docx'],cwd=folder,capture_output=True,text=True,encoding='utf-8',check=True)
                parsed=json.loads(result.stdout);assert parsed['ok'] and any('21 gündür' in s['text'] for s in parsed['sections'])
                checks['bundled_document_parser_runs']=True
            result=subprocess.run([sys.executable,'-I','backend/site_reader.py'],input='http://example.com/',cwd=folder,capture_output=True,text=True,encoding='utf-8',check=True,timeout=25,env={**env,'PYTHONIOENCODING':'utf-8'})
            assert json.loads(result.stdout)['ok'] is False
            checks['bundled_site_reader_runs_and_rejects_http_without_network']=True
            settings=temp/'private.env';create('release.example.test',settings)
            original=settings.read_bytes()
            try:create('release.example.test',settings)
            except FileExistsError:pass
            else:raise AssertionError('Config replaced original keys.')
            assert settings.read_bytes()==original;checks['config_keys_not_overwritten']=True
            settings.unlink() # unused test-only keys are not needed as evidence
            database=temp/'live.sqlite3';p=port();server=launch(folder,database,p,env);processes.append(server);ready(server,p,200)
            root=httpx.get(f'http://127.0.0.1:{p}/app');assert root.status_code==200 and 'type="module"' in root.text
            assert root.headers.get('x-robots-tag')=='noindex'
            for route in ['','kullanim/kisisel','kullanim/isletme','kullanim/ajans','isdaslar','baglantilar','paketler','yardim']:
                public=httpx.get(f'http://127.0.0.1:{p}/'+route);assert public.status_code==200 and 'og:title' in public.text
            assert httpx.get(f'http://127.0.0.1:{p}/unknown-public-route').status_code==404
            catalogue=httpx.get(f'http://127.0.0.1:{p}/api/public/catalog').json()
            assert catalogue['checkout_enabled'] is False and catalogue['registration_enabled'] is False
            assert len(catalogue['agents'])==14 and len(catalogue['plans'])==3
            checks['public_site_routes_metadata_catalogue_and_404']=True
            assert httpx.get(f'http://127.0.0.1:{p}/api/health').status_code==200
            options=httpx.get(f'http://127.0.0.1:{p}/api/auth/options').json()
            assert options=={'registration_enabled':False,'verification_required':True,'mail_available':False}
            assert httpx.get(f'http://127.0.0.1:{p}/api/account').status_code==401
            assert httpx.get(f'http://127.0.0.1:{p}/api/workspaces/none/members').status_code==401
            assert httpx.get(f'http://127.0.0.1:{p}/api/workspaces/none/client-reviews').status_code==401
            for route in ['/notifications','/workspaces/none/jobs/missing','/workspaces/none/actions/missing','/workspaces/none/client-reviews/missing']:
                assert httpx.get(f'http://127.0.0.1:{p}/api'+route).status_code==401
            assert httpx.post(f'http://127.0.0.1:{p}/api/notifications/read',headers={'Origin':env['APP_ORIGIN']},json={'ids':['0'*32]}).status_code==401
            checks['notification_and_exact_targets_require_authentication']=True
            for route in ['/agenda?day=2026-10-04','/agenda/tasks/missing','/agenda/plans/missing']:
                assert httpx.get(f'http://127.0.0.1:{p}/api/workspaces/none'+route).status_code==401
            checks['agenda_and_plan_routes_require_authentication']=True
            checks['membership_and_review_routes_require_auth']=True
            assert httpx.get(f'http://127.0.0.1:{p}/api/workspaces/none/onboarding').status_code==401
            for route,payload in [('/workspace-setup',{'name':'Unauthorized','kind':'personal','idempotency_key':'release-test-1234'}),('/workspaces/none/site-preview',{'url':'https://example.com/'})]:
                assert httpx.post(f'http://127.0.0.1:{p}/api'+route,headers={'Origin':env['APP_ORIGIN']},json=payload).status_code==401
            checks['guided_setup_and_site_reader_routes_require_auth']=True
            assert httpx.post(f'http://127.0.0.1:{p}/api/test/mailbox',headers={'Origin':env['APP_ORIGIN']}).status_code in {404,405}
            checks['account_routes_require_auth_and_test_mailbox_absent']=True
            assert httpx.post(f'http://127.0.0.1:{p}/api/auth/register',headers={'Origin':env['APP_ORIGIN']},json={'name':'Closed registration test','email':'closed@example.test','password':'synthetic-test-only'}).status_code==403
            checks['production_archive_serves_web_and_api']=True
            second=launch(folder,database,port(),env);processes.append(second)
            _,error=second.communicate(timeout=15)
            assert second.returncode!=0 and 'exactly one API worker' in error
            ready(server,p,200);checks['second_production_process_rejected']=True
            backup=temp/'verified.enc';ops.backup(database,backup);ops.verify(backup)
            candidate=temp/'restored.sqlite3';ops.restore_candidate(backup,candidate)
            restored_port=port();restored=launch(folder,candidate,restored_port,env);processes.append(restored);ready(restored,restored_port,503)
            assert httpx.get(f'http://127.0.0.1:{restored_port}/api/health').status_code==200
            assert httpx.post(f'http://127.0.0.1:{restored_port}/api/auth/register',headers={'Origin':env['APP_ORIGIN']},json={'name':'Held registration test','email':'held@example.test','password':'synthetic-test-only'}).status_code==503
            checks['restored_archive_stays_read_only']=True
        finally:
            for proc in processes:
                if proc.poll() is None:proc.terminate()
                proc.communicate(timeout=15)
    result={'passed':True,'checks':checks,'os':'Windows','live_provider_tested':False,'linux_systemd_caddy_tested':False,'off_server_backup_tested':False}
    result['archive']=archive.name
    (ROOT/'research/verification/deployment-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--archive',required=True)
    run(parser.parse_args().archive)
