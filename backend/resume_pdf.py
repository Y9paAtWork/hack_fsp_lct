"""A consistent, printable candidate resume built from the saved profile."""
from io import BytesIO
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer


FONT_DIR = Path(__file__).with_name('fonts')
BLUE = colors.HexColor('#3426A4')
INK = colors.HexColor('#1C1B25')
MUTED = colors.HexColor('#63636E')


def _register_fonts():
    if 'FSPDejaVu' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('FSPDejaVu', str(FONT_DIR / 'DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('FSPDejaVu-Bold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
        pdfmetrics.registerFontFamily('FSPDejaVu', normal='FSPDejaVu', bold='FSPDejaVu-Bold')


def _text(value):
    return escape(str(value or '').strip()).replace('\n', '<br/>')


def build_resume_pdf(profile, grades, specialization):
    """Return a PDF; no test answers, internal scores or editable JSON enter the document."""
    _register_fonts()
    selected = [specialization]
    if specialization not in profile.get('selected', []):
        raise ValueError('Специализация не найдена в профиле')
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=48, leftMargin=48,
                            topMargin=47, bottomMargin=48, title='Резюме FSP Career',
                            author='FSP Career')
    styles = {
        'name': ParagraphStyle('resume-name', fontName='FSPDejaVu-Bold', fontSize=22,
                               leading=27, textColor=INK, spaceAfter=6),
        'lead': ParagraphStyle('resume-lead', fontName='FSPDejaVu', fontSize=10,
                               leading=15, textColor=MUTED, spaceAfter=7),
        'section': ParagraphStyle('resume-section', fontName='FSPDejaVu-Bold', fontSize=12,
                                  leading=17, textColor=BLUE, spaceBefore=17, spaceAfter=7),
        'item': ParagraphStyle('resume-item', fontName='FSPDejaVu-Bold', fontSize=10,
                               leading=15, textColor=INK, spaceBefore=9, spaceAfter=3),
        'body': ParagraphStyle('resume-body', fontName='FSPDejaVu', fontSize=9,
                               leading=14, textColor=INK, spaceAfter=5),
        'meta': ParagraphStyle('resume-meta', fontName='FSPDejaVu', fontSize=8,
                               leading=12, textColor=MUTED, spaceAfter=4),
        'empty': ParagraphStyle('resume-empty', fontName='FSPDejaVu', fontSize=9,
                                leading=14, textColor=MUTED),
    }
    story = [Paragraph(_text(profile.get('name')) or 'Кандидат', styles['name'])]
    contact = [profile.get('email', '').strip()]
    if profile.get('fsp'):
        contact.append(f"FSP ID: {profile['fsp']}")
    story.append(Paragraph(' · '.join(_text(part) for part in contact if part), styles['lead']))
    if profile.get('salaryMin'):
        story.append(Paragraph(f"Ожидаемая зарплата: от {int(profile['salaryMin']):,} ₽/мес".replace(',', ' '), styles['lead']))
    story.append(HRFlowable(width='100%', thickness=1, color=BLUE, spaceAfter=9))

    stack = list(dict.fromkeys(tech for spec in selected for tech in profile.get('stacks', {}).get(spec, [])))
    if stack:
        story.append(Paragraph('Технологии', styles['section']))
        story.append(Paragraph(' · '.join(_text(tech) for tech in stack), styles['body']))

    for spec in selected:
        grade = grades.get(spec, {})
        current = grade.get('current_grade') or grade.get('declared_grade') or 'Junior'
        verified = grade.get('verified_grade')
        suffix = 'подтверждён' if verified == current else 'не подтверждён'
        story.append(Paragraph(f'{_text(spec)} · {_text(current)}', styles['section']))
        story.append(Paragraph(f'Грейд: {_text(current)} ({suffix})', styles['meta']))
        jobs = profile.get('jobs', {}).get(spec, [])
        projects = profile.get('ownProjects', {}).get(spec, [])
        if jobs:
            story.append(Paragraph('Опыт работы', styles['item']))
            for job in jobs:
                heading = ' · '.join(_text(x) for x in [job.get('role'), job.get('company')] if x)
                story.append(Paragraph(heading or 'Место работы', styles['item']))
                period = ' — '.join(_text(x) for x in [job.get('start'), job.get('end')] if x)
                if period:
                    story.append(Paragraph(period, styles['meta']))
                if job.get('description'):
                    story.append(Paragraph(_text(job['description']), styles['body']))
        if projects:
            story.append(Paragraph('Проекты', styles['item']))
            for project in projects:
                story.append(Paragraph(_text(project.get('name')) or 'Проект', styles['item']))
                if project.get('description'):
                    story.append(Paragraph(_text(project['description']), styles['body']))
                if project.get('tasks'):
                    story.append(Paragraph('<b>Задачи:</b> ' + _text(project['tasks']), styles['body']))
        if not jobs and not projects:
            story.append(Paragraph('Опыт и проекты пока не добавлены.', styles['empty']))
        story.append(Spacer(1, 6))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D9D7E8'))
        canvas.line(48, 36, A4[0] - 48, 36)
        canvas.setFont('FSPDejaVu', 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(48, 24, 'FSP CAREER · РЕЗЮМЕ')
        canvas.drawRightString(A4[0] - 48, 24, f'{document.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
