from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


def make_pdf(pages, *, font="STSong-Light", **kwargs):
    if font == "STSong-Light" and font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    stream = BytesIO()
    style = ParagraphStyle("body", fontName=font, fontSize=12, leading=22, wordWrap="CJK")
    story = []
    for number, text in enumerate(pages):
        if number:
            story.append(PageBreak())
        for line in text.splitlines():
            story.append(Paragraph(escape(line), style) if line else Spacer(1, 10))
    SimpleDocTemplate(stream, title=pages[0].splitlines()[0], **kwargs).build(story)
    return stream.getvalue()
