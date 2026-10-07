"""Back up the idle local development DB before the additive onboarding schema."""
import argparse,datetime,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];database=ROOT/'data/isdas.sqlite3'
parser=argparse.ArgumentParser();parser.add_argument('--milestone',default='onboarding',choices=['onboarding','marketing','notifications','agenda']);args=parser.parse_args()
if not database.is_file():raise SystemExit('Expected local database is missing.')
with sqlite3.connect(database) as conn:
    jobs=conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    actions=conn.execute("SELECT count(*) FROM actions WHERE status='executing'").fetchone()[0]
    if jobs or actions:raise SystemExit('Active work: do not restart.')
    backup=ROOT/'data/backups'/('before-'+args.milestone+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
    if backup.exists():raise SystemExit('Backup already exists; do not overwrite.')
    with sqlite3.connect(backup) as target:
        conn.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Backup failed integrity check.')
result={'backup':str(backup.relative_to(ROOT)).replace('\\','/'),'active_jobs':jobs,'executing_actions':actions,'integrity':'ok'}
(ROOT/'research/verification'/(args.milestone+'-upgrade-backup.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
