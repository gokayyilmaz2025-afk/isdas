"""Verify a release already extracted into its own directory. No mutations."""
import hashlib,json
from pathlib import Path

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'release-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format')!=1:raise SystemExit('Unsupported release manifest.')
    for name,digest in manifest['files'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():raise SystemExit('Missing or invalid release path: '+name)
        with path.open('rb') as file:actual=hashlib.file_digest(file,'sha256').hexdigest()
        if actual!=digest:raise SystemExit('Release checksum mismatch: '+name)
    print(json.dumps({'verified':True,'files':len(manifest['files'])}))
