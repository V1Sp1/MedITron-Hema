"""Server-generated research reports. No network, source PDFs or external renderer."""
import io
import json
import zipfile
from threading import Lock
from xml.sax.saxutils import escape

from .features import FEATURES, ROOT
from .privacy import USE_NOTICE

PDF_LOCK = Lock()


def report_pdf(report):
    # Registration/subsetting uses ReportLab global font state. Bound concurrency.
    with PDF_LOCK:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        name = 'HemaPlex'
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, ROOT/'frontend/src/assets/fonts/IBMPlexSans-Regular.ttf'))
        stream = io.BytesIO()
        styles = {
            'body': ParagraphStyle('body', fontName=name, fontSize=10, leading=14,
                                   spaceAfter=7, textColor=colors.HexColor('#103648'), splitLongWords=True),
            'heading': ParagraphStyle('heading', fontName=name, fontSize=14, leading=19,
                                      spaceBefore=14, spaceAfter=9, keepWithNext=True,
                                      textColor=colors.HexColor('#19796b')),
            'title': ParagraphStyle('title', fontName=name, fontSize=22, leading=29, spaceAfter=14),
            'cell': ParagraphStyle('cell', fontName=name, fontSize=9, leading=12,
                                   textColor=colors.HexColor('#103648'), splitLongWords=True),
        }
        flow = []
        def text(value, kind='body'):
            flow.append(Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[kind]))
        def section(title):
            text(title, 'heading')
        role = report['audience']
        text('Hema. / '+('Для врача' if role == 'doctor' else 'Для пациента'), 'title')
        text('by NeuroNiXxx / Исследовательский отчёт')
        text(USE_NOTICE)
        text('ID запроса: '+report['requestId'])
        section('Оценка по гемоглобину')
        text('Hb ниже порога анемии.' if report['anemia'] else 'Hb не ниже порога анемии.')
        text(f"Hb: {report['hemoglobin']} г/л; порог: {report['threshold']} г/л. Отсутствие анемии не исключает дефицит.")
        section('Предполагаемое состояние')
        prediction = report.get('prediction') or {}
        text(prediction.get('label', 'Причина не оценена.'))
        text(prediction.get('hiddenDeficitLabel', 'Дефициты не оценены.'))
        ferritin = report.get('ferritinScreening') or {}
        if ferritin:
            section('Отдельная оценка ферритина')
            if ferritin.get('status') == 'observed':
                text(f"Ферритин измерен: {ferritin['observedValue']} {ferritin['unit']}. Модель не подменяет анализ.")
            else:
                text(ferritin.get('message', 'Отдельный прогноз недоступен.'))
                if ferritin.get('status') == 'research_prediction':
                    text('Модель прогнозирует ферритин ниже 15 µg/L. Проверенная группа: женщины 18-49 лет; беременность исключалась.')
                if role == 'doctor' and ferritin.get('status') == 'research_prediction':
                    text(f"Исследовательская оценка: {ferritin['score']:.3f}; порог: {ferritin['decisionThreshold']:.3f}. Это не вероятность диагноза.")
            if ferritin.get('limitation'):
                text(ferritin['limitation'])
        for title, key in [('Что удалось оценить', 'recommendationConclusions')]:
            if report.get(key):
                section(title)
                for item in report[key]:text(item['text'])
        section('Полнота данных')
        text(report['dataSufficiency']['summary'])
        for item in report.get('insufficientData', {}).get('items', []):text(item['text'])
        if role == 'doctor':
            groups = [('Оценки модели по дефицитам', report.get('deficiencyScores', []))]
            if report['anemia']:
                groups.append(('Оценки модели по классам', report.get('anemiaScores', [])))
            for title, items in groups:
                section(title)
                if items:
                    text('Оценки 0–1 не являются проверенной вероятностью заболевания.')
                    for item in items:text(f"{item['label']}: {item['score']:.3f}")
                else:
                    text('Оценки подавлены из-за недостатка анализов.' if report['dataSufficiency']['level'] == 'insufficient'
                         else 'Модель не подключена; доступно только правило Hb.')
        section('Следующие шаги')
        for i, item in enumerate(report.get('recommendations', []), 1):text(f"{i}. {item['text']}")
        section('Ограничения')
        for warning in report.get('warnings', []):text(warning)
        section('Исходные показатели')
        if report.get('observationSummary'):
            source = report['observationSummary']
            text(f"PDF в пакете: {source['documentCount']}; извлечено измерений: {source['measurementCount']}.")
            text('Даты анализов в пакете: '+(', '.join(source['collectedDates']) or 'не определены')+
                 f". Без даты: {source['undatedMeasurements']}.")
            text(source['notice'])
        values = report['inputs']
        text(f"Возраст: {values['age_years']}; пол: {'женский' if values['sex']=='F' else 'мужской'}. Пустые показатели не считаются нормальными.")
        rows = [[Paragraph(escape(s), styles['cell']) for s in ('Показатель', 'Значение', 'Единица')]]
        for key, feature in FEATURES.items():
            if key in {'age_years', 'sex'}:continue
            rows.append([Paragraph(escape(str(value)), styles['cell']) for value in
                         (feature['description'], 'не указан' if values[key] is None else values[key], feature['unit'])])
        table = Table(rows, colWidths=[295, 80, 120], repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#eaf5f1')),
            ('LINEBELOW', (0,0), (-1,-1), .3, colors.HexColor('#dce4e5')),
            ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4)]))
        flow.extend([table, Spacer(1,10)])
        section('Происхождение')
        text('Модель: '+str(report.get('modelVersion') or 'отключена')+'. Фразы: '+report['recommendationVersion'])
        if ferritin.get('modelVersion'):text('Модель ферритина: '+ferritin['modelVersion'])
        text('Словарь единиц: '+report['unitDictionaryVersion'])
        text('Дата расчёта (UTC): '+report['createdAt'])
        def footer(canvas, document):
            canvas.setFont(name, 8)
            canvas.setFillColor(colors.HexColor('#506568'))
            canvas.drawString(46,25,'Hema / Исследовательский режим')
            canvas.drawRightString(A4[0]-46,25,str(document.page))
        document = SimpleDocTemplate(stream, pagesize=A4, rightMargin=46, leftMargin=46,
            topMargin=46, bottomMargin=46, title='Hema - research report', author='NeuroNiXxx',
            allowSplitting=True)
        document.build(flow, onFirstPage=footer, onLaterPages=footer)
        return stream.getvalue()


def encode_reports(reports, output_format):
    payload = {'schemaVersion': 'laboratory-reports-v1', 'requestId': reports[0]['requestId'],
               'researchOnly': True, 'clinicalUseEnabled': False, 'stored': False, 'reports': reports}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    if output_format == 'json':
        return encoded, 'application/json', 'reports.json'
    if output_format == 'pdf':
        return report_pdf(reports[0]), 'application/pdf', 'report-'+reports[0]['audience']+'.pdf'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('reports.json', encoded)
        for report in reports:
            archive.writestr('report-'+report['audience']+'.pdf', report_pdf(report))
    return stream.getvalue(), 'application/zip', 'reports.zip'
