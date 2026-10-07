import json
from test_accounts import account_client
from backend import db,billing,public_site

def test_anonymous_catalog_is_explicit_preview_and_uses_actual_plan_policy(account_client,monkeypatch):
    monkeypatch.setenv('XAI_API_KEY','DO-NOT-EXPOSE-PROVIDER-SECRET')
    monkeypatch.setenv('SMTP_PASSWORD','DO-NOT-EXPOSE-SMTP-SECRET')
    r=account_client.get('/api/public/catalog');assert r.status_code==200
    data=r.json();assert data['pricing_status']=='preview' and data['checkout_enabled'] is False
    assert data['pilot']['automatic_grant'] is False and data['pilot']['automatic_renewal'] is False
    assert data['registration_enabled'] is True
    for plan in data['plans']:
        source=billing.PLANS[plan['id']]
        assert plan['monthly_credits']==source['credits'] and plan['workspaces']==source['workspaces'] and plan['proposed_monthly_usd']==source['proposed_usd']
    assert len(data['agents'])==14
    assert all(set(a)=={'id','name','title','icon','color','description'} for a in data['agents'])
    assert 'prompt' not in r.text and 'SECRET' not in r.text
    assert db.query('SELECT count(*) AS n FROM users',one=True)['n']==0
    assert db.query('SELECT count(*) AS n FROM entitlement_periods',one=True)['n']==0

def test_catalog_registration_status_tracks_real_gate(account_client,monkeypatch):
    monkeypatch.setenv('REGISTRATION_ENABLED','false')
    assert account_client.get('/api/public/catalog').json()['registration_enabled'] is False

def test_public_pages_have_unique_server_metadata_and_unknown_path_is_404(account_client):
    titles=set()
    for path,(title,description) in public_site.PAGES.items():
        r=account_client.get('/'+path);assert r.status_code==200,path
        assert '<title>'+title+'</title>' in r.text
        assert 'property="og:title"' in r.text and 'property="og:description"' in r.text
        assert 'type="module"' in r.text
        titles.add(title)
    assert len(titles)==len(public_site.PAGES)
    assert account_client.get('/paketler/').status_code==200
    unknown=account_client.get('/sayfa-yok')
    assert unknown.status_code==404 and 'noindex' in unknown.text
    assert account_client.get('/api/does-not-exist').status_code==404
    assert account_client.get('/app').headers['x-robots-tag']=='noindex'
    assert account_client.get('/account/reset').headers['x-robots-tag']=='noindex'

def test_metadata_is_escaped_not_interpreted(tmp_path,monkeypatch):
    index=tmp_path/'index.html';index.write_text('<head><title>Old</title><meta name="description" content="Old"/></head>',encoding='utf-8')
    monkeypatch.setitem(public_site.PAGES,'test',('<script>Bad</script>','"/><script>bad</script>'))
    rendered=public_site.page_html(index,'test')
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered and '&quot;' in rendered
