import sys, pathlib, json, subprocess, math
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.research-deps'))
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
folder = ROOT / 'research' / 'videos'
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
for item in json.loads((folder / 'manifest.json').read_text(encoding='utf-8')):
    code = item['shortCode']
    video = folder / f'{code}.mp4'
    if not video.exists(): continue
    out = folder / code
    out.mkdir(exist_ok=True)
    duration = item.get('videoDuration', 60)
    interval = max(2, duration / 15)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(video), '-vf', f'fps=1/{interval},scale=300:-1', '-frames:v', '16', str(out/'frame-%02d.jpg')],check=True)
    files = sorted(out.glob('frame-*.jpg'))
    frames = [Image.open(f).convert('RGB') for f in files]
    w, h = 316, max(f.height for f in frames)+45
    sheet = Image.new('RGB', (w*4, h*math.ceil(len(frames)/4)), '#eeeeee')
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(frames):
        x, y = i%4*w, i//4*h
        sheet.paste(frame,(x+8,y+32))
        draw.text((x+8,y+6), f'{code} ~{int((i+.5)*interval)}s',font=font,fill='black')
    sheet.save(out/'contact.jpg',quality=90)
    audio=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(video),'-vn','-ac','1','-ar','16000',str(folder/f'{code}.wav')],capture_output=True)
    print(json.dumps({'code':code,'frames':len(frames),'audio_available':audio.returncode==0,'contact':str(out/'contact.jpg')}),flush=True)
