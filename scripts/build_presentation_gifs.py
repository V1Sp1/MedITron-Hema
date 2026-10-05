"""Assemble presentation GIFs from screenshots recorded through the real UI.

No report values are generated or modified here. Captures use fictional panels.
"""
from pathlib import Path
import json

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'deliverables/presentation'
SHOTS = OUT / 'frames'
FONT = ROOT / 'frontend/src/assets/fonts/IBMPlexSans-Regular.ttf'
SIZE = (1600, 900)
INK, MINT, BG = '#103648', '#0DEACF', '#E3EEF1'


def step(file, title, body, seconds=3, crop=None):
    return dict(file=file, title=title, body=body, seconds=seconds, crop=crop)


PATIENT = [
    step('00-home.jpg', 'Выберите роль', 'Нажмите «Я пациент», чтобы получить объяснение анализов простым языком.', 3),
    step('patient-01-consent.jpg', 'Начните сеанс', 'Для демонстрации выбраны вымышленные данные. Подтвердите условия исследовательской работы.', 3, (270, 190, 800, 725)),
    step('patient-02-form.jpg', 'Выберите способ ввода', 'Здесь показан ручной ввод. Также доступны загрузка PDF и импорт CSV / XLSX.', 3, (90, 385, 973, 903)),
    step('patient-03-cbc.jpg', 'Введите показатели', 'Укажите пол, возраст и результаты общего анализа крови. Единицы подписаны у полей.', 4, (92, 70, 973, 903)),
    step('patient-04-iron.jpg', 'Дополните анализы', 'Во вкладке «Железо» добавьте ферритин и TSAT, если они измерены. Неизвестные значения оставьте пустыми.', 4, (92, 170, 973, 903)),
    step('patient-04b-submit.jpg', 'Получите результат', 'Прокрутите форму вниз и нажмите «Получить результат». Выполняется расчёт на локальном сервере.', 3),
    step('patient-05-result.jpg', 'Прочитайте объяснение', 'Отчёт показывает Hb относительно порога и предполагаемое состояние. Измеренный ферритин отображается отдельно.', 4, (120, 175, 940, 900)),
    step('patient-06-next.jpg', 'Посмотрите следующие шаги', 'Проверьте полноту анализов и список вопросов для обсуждения с врачом.', 4, (120, 0, 940, 900)),
    step('patient-07-export.jpg', 'Сохраните отчёт', 'Нажмите «Сохранить PDF». Отчёт можно взять с собой на консультацию.', 4, (120, 0, 940, 903)),
]

DOCTOR = [
    step('00-home.jpg', 'Выберите роль', 'Нажмите «Я врач», чтобы открыть расширенный исследовательский отчёт.', 3),
    step('doctor-01-login.jpg', 'Войдите в систему', 'Используйте учётную запись, выданную администратором организации. Пароль в демонстрацию не включён.', 3, (265, 170, 800, 780)),
    step('doctor-02-consent.jpg', 'Начните сеанс', 'Подтвердите вид данных и условия. В этой демонстрации используются только вымышленные анализы.', 3, (270, 190, 800, 725)),
    step('doctor-03-import.jpg', 'Загрузите таблицу', 'Выберите вкладку «CSV / XLSX» и файл с лабораторными показателями в единицах проекта.', 3, (92, 250, 973, 903)),
    step('doctor-04-loaded.jpg', 'Выберите запись', 'Импортируется одна запись. Здесь выбрана первая из 15 вымышленных панелей.', 3, (120, 230, 940, 903)),
    step('doctor-05-review.jpg', 'Проверьте значения', 'Перейдите к форме. Сверьте показатели, единицы и полноту: в этом примере заполнены 35 из 35 анализов.', 3, (92, 0, 973, 903)),
    step('doctor-05b-submit.jpg', 'Выполните скрининг', 'Нажмите «Выполнить скрининг». Расчёт выполняет рабочая модель локального сервера.', 3, (120, 0, 940, 790)),
    step('doctor-06-result.jpg', 'Оцените общий вывод', 'Отчёт объединяет правило Hb, предположение модели, измеренный ферритин и полноту панели.', 4, (120, 175, 940, 900)),
    step('doctor-07-deficits.jpg', 'Изучите дефициты', 'Пять отдельных оценок позволяют видеть предполагаемые сопутствующие дефициты.', 4, (120, 0, 940, 850)),
    step('doctor-08-classes.jpg', 'Сравните классы', 'При анемии доступны оценки 12 классов. Scores 0–1 не являются проверенными вероятностями заболеваний.', 4, (120, 100, 940, 850)),
    step('doctor-09-tactics.jpg', 'Проверьте основания', 'Сопоставьте рекомендации, лабораторные данные и ограничения результата с клиническим контекстом.', 4, (120, 70, 940, 850)),
    step('doctor-10-export.jpg', 'Сохраните отчёт', 'Экспортируйте PDF. Для дальнейшей обработки также доступен JSON.', 4, (120, 0, 940, 903)),
]


def font(size):
    return ImageFont.truetype(str(FONT), size)


def paragraph(draw, text, xy, width, size=28, color=INK):
    x, y = xy
    line = ''
    for word in text.split():
        candidate = f'{line} {word}'.strip()
        if draw.textlength(candidate, font=font(size)) > width and line:
            draw.text((x, y), line, font=font(size), fill=color)
            y += size + 10
            line = word
        else:
            line = candidate
    draw.text((x, y), line, font=font(size), fill=color)
    return y + size + 10


def render(role, steps, index, progress=1):
    spec = steps[index]
    frame = Image.new('RGB', SIZE, BG)
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, 1600, 76), fill=INK)
    draw.text((30, 16), 'Hema.', fill=MINT, font=font(39))
    draw.text((180, 25), f'Демонстрация · {role}', fill='white', font=font(27))
    draw.rounded_rectangle((1170, 17, 1572, 58), radius=18, fill='#1E465B')
    draw.text((1190, 24), 'Вымышленные анализы', font=font(24), fill=MINT)

    # Actual browser screenshot, proportionally fitted; no UI text or values edited.
    screen = Image.open(SHOTS / spec['file']).convert('RGB')
    if spec['crop']:
        screen = screen.crop(spec['crop'])
    screen.thumbnail((1075, 740), Image.Resampling.LANCZOS)
    sx, sy = 28 + (1075-screen.width)//2, 106 + (740-screen.height)//2
    draw.rounded_rectangle((23, 101, 1108, 851), radius=18, fill='#cddfe4')
    draw.rounded_rectangle((sx-3, sy-3, sx+screen.width+3, sy+screen.height+3), radius=3, fill='white')
    frame.paste(screen, (sx, sy))

    draw.rounded_rectangle((1130, 106, 1574, 846), radius=18, fill='white')
    draw.text((1160, 140), f'ШАГ {index+1:02} / {len(steps):02}', font=font(22), fill='#477D79')
    y = paragraph(draw, spec['title'], (1160, 198), 378, 40)
    y = paragraph(draw, spec['body'], (1160, y+28), 378, 28)
    draw.line((1160, 672, 1538, 672), fill=BG, width=2)
    paragraph(draw, 'Реальный интерфейс и расчёт модели на тестовых данных.', (1160, 698), 368, 23, '#58717B')

    # A gently moving progress indicator makes the pace visible without flashing.
    total = len(steps)
    segment = 378/total
    for j in range(total):
        draw.rounded_rectangle((1160+j*segment, 806, 1160+(j+1)*segment-5, 813), radius=3, fill=BG)
        if j < index:
            draw.rounded_rectangle((1160+j*segment, 806, 1160+(j+1)*segment-5, 813), radius=3, fill='#477D79')
    draw.rounded_rectangle((1160+index*segment, 806, 1160+index*segment+(segment-5)*progress, 813), radius=3, fill=MINT)
    draw.text((30, 867), 'Исследовательский прототип · Не использовать для диагностики или назначения лечения', font=font(20), fill=INK)
    return frame


def build(role, name, steps):
    keyframes = [render(role, steps, i) for i in range(len(steps))]
    samples = Image.new('RGB', (400*len(steps), 225))
    for i, frame in enumerate(keyframes):
        samples.paste(frame.resize((400, 225)), (400*i, 0))
    palette = samples.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames, durations = [], []
    for i, spec in enumerate(steps):
        for tick in range(spec['seconds']):
            frame = render(role, steps, i, (tick+1)/spec['seconds'])
            frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
            durations.append(1000)
    path = OUT / f'hema_{name}_demo.gif'
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=1)
    keyframes[0].save(OUT / f'hema_{name}_poster.png')
    keyframes[-1].save(OUT / f'hema_{name}_final.png')
    # Verify the encoded artifact, including every decoded frame.
    with Image.open(path) as gif:
        duration = 0
        for i in range(gif.n_frames):
            gif.seek(i)
            gif.convert('RGB').load()
            duration += gif.info['duration']
        assert gif.size == SIZE and gif.info.get('loop') == 0
        assert duration == sum(durations)
        record = dict(file=path.name, size=list(SIZE), frames=gif.n_frames,
                      duration_seconds=duration/1000, bytes=path.stat().st_size,
                      loop='infinite', role=role, steps=steps)
    print(f'{path.name}: {duration/1000:g} с, {path.stat().st_size/1024/1024:.2f} МиБ')
    return record


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    records = [build('Для пациента', 'patient', PATIENT), build('Для врача', 'doctor', DOCTOR)]
    (OUT / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')
