"""Encode recorded UI states and synchronized pointer events as window GIFs."""
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'deliverables/presentation'
RECORDINGS = OUT / 'cursor-recording'


def cursor_sprite():
    scale = 4
    sprite = Image.new('RGBA', (36*scale, 45*scale))
    d = ImageDraw.Draw(sprite)
    shape = [(2, 2), (2, 28), (9, 22), (14, 34), (20, 31), (14, 20), (25, 20)]
    points = [(x*scale, y*scale) for x, y in shape]
    d.polygon([(x+2*scale, y+2*scale) for x, y in points], fill=(0, 0, 0, 75))
    d.polygon(points, fill='white', outline='#152932', width=scale)
    return sprite.resize((36, 45), Image.Resampling.LANCZOS)


CURSOR = cursor_sprite()


def paint(base, point, ripple=None):
    frame = base.copy().convert('RGBA')
    x = max(4, min(frame.width-29, point[0]))
    y = max(4, min(frame.height-36, point[1]))
    if ripple is not None:
        overlay = Image.new('RGBA', frame.size)
        d = ImageDraw.Draw(overlay)
        r = 11 + 15*ripple
        d.ellipse((x-r, y-r, x+r, y+r), fill=(13, 234, 207, 45), outline=(0, 133, 124, 190), width=2)
        frame = Image.alpha_composite(frame, overlay)
    frame.alpha_composite(CURSOR, (round(x)-2, round(y)-2))
    return frame.convert('RGB')


def encode(name):
    events = json.loads((RECORDINGS / f'{name}-events.json').read_text())
    paths = list(dict.fromkeys(e['file'] for e in events))
    screens = {p: Image.open(RECORDINGS/p).convert('RGB') for p in paths}
    sizes = set(im.size for im in screens.values())
    assert len(sizes) == 1, sizes
    size = next(iter(sizes))
    # A shared palette keeps stationary UI pixels stable across pointer frames.
    palette_sheet = Image.new('RGB', (160*10, 90*math.ceil(len(paths)/10)))
    for i, path in enumerate(paths):
        palette_sheet.paste(screens[path].resize((160, 90)), ((i%10)*160, (i//10)*90))
    palette_sheet.paste(Image.new('RGB', (8, 8), '#0DEACF'), (0, 0))
    palette = palette_sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames, durations = [], []

    def append(frame, ms):
        frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
        durations.append(ms)

    for event in events:
        base = screens[event['file']]
        ms = round(event['ms']/10)*10
        if event['type'] == 'move':
            count = max(2, math.ceil(ms/40))
            slice_ms = (ms//(count*10))*10
            for i in range(count):
                t = (i+1)/count
                t = t*t*(3-2*t)
                point = [event['from'][j] + (event['mouse'][j]-event['from'][j])*t for j in range(2)]
                append(paint(base, point), slice_ms if i<count-1 else ms-slice_ms*(count-1))
        elif event['type'] == 'click':
            append(paint(base, event['mouse'], 0), ms//20*10)
            append(paint(base, event['mouse'], 1), ms-ms//20*10)
        else:
            append(paint(base, event['mouse']), ms)
    path = OUT / f'hema_{name}_cursor.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=1)
    # Verify complete decoding, loop flag, format and total duration.
    with Image.open(path) as gif:
        total = 0
        for i in range(gif.n_frames):
            gif.seek(i)
            gif.convert('RGB').load()
            total += gif.info['duration']
        assert gif.size == size and gif.info.get('loop') == 0
        assert total == sum(durations), (total, sum(durations))
        manifest = dict(file=path.name, size=list(gif.size), frames=gif.n_frames,
                        seconds=total/1000, bytes=path.stat().st_size,
                        recorded_ui_states=len(paths), pointer='synchronized overlay')
    # A preview from the input phase shows the typing and the cursor.
    candidates = [e for e in events if e['type']=='click']
    preview = candidates[min(8, len(candidates)-1)]
    paint(screens[preview['file']], preview['mouse'], .5).save(OUT/f'hema_{name}_cursor_preview.png')
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    (OUT/f'hema_{name}_cursor_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    import sys
    for name in sys.argv[1:] or ['patient', 'doctor']:
        encode(name)
