"""Allowlisted own-server artifact: never includes .env, databases or research."""
import argparse,datetime,hashlib,json,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build(output):
    if not (ROOT/'frontend/dist/index.html').is_file():raise ValueError('Run the frontend production build first.')
    files=[ROOT/'requirements.txt']
    files+=list((ROOT/'backend').glob('*.py'))
    files+=[p for p in (ROOT/'frontend/dist').rglob('*') if p.is_file()]
    files+=[p for p in (ROOT/'deploy').iterdir() if p.is_file() and p.name!='build_release.py']
    files+=[ROOT/'output/Sunucu-Kurulumu-ve-Yedekleme.txt',ROOT/'output/Icerik-Studio-Kullanim-Notlari.txt',ROOT/'output/Google-Baglanti-Kurulumu.txt',ROOT/'output/Paket-ve-Kullanim-Isletim-Notlari.txt',ROOT/'output/Belgeler-ve-Kaynakli-Hafiza-Kullanim-Notlari.txt']
    entries=[]
    files.append(ROOT/'output/Hesap-ve-Eposta-Isletim-Notlari.txt')
    files.append(ROOT/'output/Ekip-ve-Musteri-Onayi-Kullanim-Notlari.txt')
    files.append(ROOT/'output/Baslangic-Rehberi-ve-Site-Okuma-Notlari.txt')
    files.append(ROOT/'output/Tanitim-Sitesi-ve-Kayit-Akisi-Notlari.txt')
    files.append(ROOT/'output/Bildirimler-ve-Sonuca-Gecis-Notlari.txt')
    files.append(ROOT/'output/Ajanda-Planlar-ve-Hatirlatmalar-Notlari.txt')
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):raise ValueError('Release input escaped its source root.')
        data=path.read_bytes();name=path.relative_to(ROOT).as_posix()
        entries.append((name,data))
    manifest={'format':1,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'deployment_tested_on_linux':False,'live_provider_tested':False,
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in entries}}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries:archive.writestr(name,data)
        archive.writestr('release-manifest.json',json.dumps(manifest,indent=2))
    with output.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    return {'artifact':str(output.resolve()),'sha256':digest,'files':len(entries),'bytes':output.stat().st_size}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    print(json.dumps(build(args.output)))
