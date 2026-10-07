"""Single-process deployment and read-only recovery mode."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.fernet import Fernet


class DatabaseLease:
    """OS-held lease; a crashed process releases it without stale PID removal."""
    def __init__(self, database):
        self.path = Path(str(Path(database).resolve()) + '.process.lock')
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            raise RuntimeError('This database already has a serving process. Run exactly one API worker.') from None
        self.file = handle
        return self

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def __enter__(self): return self.acquire()
    def __exit__(self, *args): self.close()


def production_checks(database, static):
    if os.getenv('APP_ENV', 'development') != 'production': return
    for name in ('APP_ORIGIN', 'PUBLIC_ORIGIN'):
        value = os.getenv(name, '')
        parsed = urlsplit(value)
        try: parsed.port
        except ValueError: raise RuntimeError(name + ' has an invalid port.') from None
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise RuntimeError(name + ' must be an HTTPS origin without credentials, path or query.')
    if os.environ['APP_ORIGIN'].rstrip('/') != os.environ['PUBLIC_ORIGIN'].rstrip('/'):
        raise RuntimeError('The production web and API must use the same origin.')
    if not Path(database).is_absolute(): raise RuntimeError('Production DATABASE_PATH must be absolute.')
    try: Fernet(os.getenv('ENCRYPTION_KEY', '').encode())
    except (ValueError, TypeError): raise RuntimeError('A valid ENCRYPTION_KEY is required in production.') from None
    if not (Path(static) / 'index.html').is_file(): raise RuntimeError('Build the frontend before starting production.')
    if os.getenv('WORKER_ENABLED', 'true') != 'true':
        raise RuntimeError('Production requires its single queue worker. Restores stay read-only via their database hold.')
    from . import accounts,mailer
    if accounts.registration_enabled() and (not accounts.required() or not mailer.available()):
        raise RuntimeError('Public production registration requires verified email and configured TLS account mail.')
    if os.getenv('SMTP_HOST') and (not mailer.available() or os.getenv('MAIL_WORKER_ENABLED','true')!='true'):
        raise RuntimeError('Configured production account mail requires valid settings and its worker.')
    from .usage import dollars_to_ticks
    for name,default in {'DEFAULT_MONTHLY_BUDGET_USD':'10','GLOBAL_MONTHLY_BUDGET_USD':'100','JOB_RESERVE_USD':'0.25','IMAGE_JOB_RESERVE_USD':'0.10','RESEARCH_JOB_RESERVE_USD':'1','HERMES_JOB_RESERVE_USD':'2'}.items():
        try:
            amount=dollars_to_ticks(os.getenv(name,default))
            if name.endswith('RESERVE_USD') and amount==0:raise ValueError()
        except Exception:raise RuntimeError(name+' must be a valid budget amount.') from None


def recovery_hold(conn):
    return conn.execute("SELECT 1 FROM system_state WHERE key='recovery_hold'").fetchone() is not None


def is_held():
    from . import db
    with db.connection() as conn: return recovery_hold(conn)
