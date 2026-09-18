#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markdown -> PDF via reportlab + CID font (STSong-Light) for CJK support.
Works without external Chinese TTF (uses Adobe CID built-in).
"""
import re, os, sys, pathlib
import markdown
from html.parser import HTMLParser

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, KeepTogether, HRFlowable, Preformatted, XPreformatted)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.fonts import tt2ps

# Register CJK fonts
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))

# Also try to use built-in Helvetica for fallback ascii within Paragraph, but we will use CID for all
CJK_FONT = 'STSong-Light'
CJK_FONT_BOLD = 'STSong-Light'  # no bold variant, use same but larger/darker
HEISEI = 'HeiseiKakuGo-W5'

COLORS = {
    'primary': HexColor('#1f4e79'),
    'accent': HexColor('#2e75b6'),
    'gray_dark': HexColor('#333333'),
    'gray': HexColor('#666666'),
    'gray_light': HexColor('#f2f2f2'),
    'border': HexColor('#d9d9d9'),
    'code_bg': HexColor('#f6f8fa'),
    'quote_bg': HexColor('#f0f4f8'),
    'table_header': HexColor('#1f4e79'),
    'success': HexColor('#548235'),
}

PAGE_W, PAGE_H = A4

def header_footer(canvas, doc):
    canvas.saveState()
    # header line
    canvas.setStrokeColor(COLORS['border'])
    canvas.setLineWidth(0.5)
    canvas.line(20*mm, PAGE_H - 15*mm, PAGE_W - 20*mm, PAGE_H - 15*mm)
    canvas.setFont(CJK_FONT, 7)
    canvas.setFillColor(COLORS['gray'])
    # doc.title is set by us
    title = getattr(doc, 'pdf_title', 'NILM 负荷辨识')
    canvas.drawString(20*mm, PAGE_H - 12*mm, title)
    # date
    canvas.drawRightString(PAGE_W - 20*mm, PAGE_H - 12*mm, "2026-09-18  ·  arena/01a0896c-nilm-new")
    # footer
    canvas.setStrokeColor(COLORS['border'])
    canvas.line(20*mm, 15*mm, PAGE_W - 20*mm, 15*mm)
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(COLORS['gray'])
    canvas.drawCentredString(PAGE_W/2, 11*mm, f"- {doc.page} -")
    # small notice
    canvas.setFont(CJK_FONT, 6)
    canvas.setFillColor(COLORS['gray'])
    canvas.drawString(20*mm, 9*mm, "BOOTSTRAP v2.3  ·  内部资料")
    canvas.restoreState()

def build_styles():
    styles = getSampleStyleSheet()
    # Override
    s_title = ParagraphStyle('TitleCJK', parent=styles['Title'],
        fontName=CJK_FONT, fontSize=22, leading=26, textColor=COLORS['primary'],
        alignment=TA_CENTER, spaceAfter=6*mm)
    s_subtitle = ParagraphStyle('SubtitleCJK', parent=styles['Normal'],
        fontName=CJK_FONT, fontSize=9, leading=12, textColor=COLORS['gray'],
        alignment=TA_CENTER, spaceAfter=8*mm)
    s_h1 = ParagraphStyle('H1', parent=styles['Heading1'],
        fontName=CJK_FONT, fontSize=16, leading=20, textColor=COLORS['primary'],
        spaceBefore=8*mm, spaceAfter=4*mm, keepWithNext=True,
        borderPadding=(0,0,2,0))
    s_h2 = ParagraphStyle('H2', parent=styles['Heading2'],
        fontName=CJK_FONT, fontSize=13, leading=17, textColor=COLORS['accent'],
        spaceBefore=6*mm, spaceAfter=3*mm, keepWithNext=True)
    s_h3 = ParagraphStyle('H3', parent=styles['Heading3'],
        fontName=CJK_FONT, fontSize=11, leading=15, textColor=COLORS['gray_dark'],
        spaceBefore=5*mm, spaceAfter=2*mm, keepWithNext=True)
    s_h4 = ParagraphStyle('H4', parent=styles['Heading4'],
        fontName=CJK_FONT, fontSize=10, leading=14, textColor=COLORS['gray_dark'],
        spaceBefore=4*mm, spaceAfter=2*mm)
    s_normal = ParagraphStyle('NormalCJK', parent=styles['Normal'],
        fontName=CJK_FONT, fontSize=8.5, leading=13, textColor=COLORS['gray_dark'],
        alignment=TA_JUSTIFY, spaceAfter=2*mm, wordWrap='CJK')
    s_bullet = ParagraphStyle('Bullet', parent=s_normal,
        leftIndent=10*mm, bulletIndent=5*mm, spaceAfter=1.2*mm)
    s_code = ParagraphStyle('Code', parent=styles['Code'],
        fontName=CJK_FONT, fontSize=7, leading=10, textColor=HexColor('#24292e'),
        backColor=COLORS['code_bg'], borderPadding=(4,4,4,6),
        spaceBefore=2*mm, spaceAfter=2*mm)
    s_quote = ParagraphStyle('Quote', parent=s_normal,
        leftIndent=6*mm, rightIndent=4*mm, borderPadding=(4,6,4,8),
        backColor=COLORS['quote_bg'], borderColor=COLORS['accent'],
        borderWidth=0, borderPaddingTop=3, spaceBefore=2*mm, spaceAfter=2*mm,
        textColor=HexColor('#334155'))
    s_table_cell = ParagraphStyle('TableCell', parent=styles['Normal'],
        fontName=CJK_FONT, fontSize=6.5, leading=9, textColor=COLORS['gray_dark'],
        alignment=TA_LEFT)
    s_table_header = ParagraphStyle('TableHeader', parent=s_table_cell,
        fontName=CJK_FONT, fontSize=6.5, leading=9, textColor=white,
        alignment=TA_CENTER)
    s_caption = ParagraphStyle('Caption', parent=styles['Normal'],
        fontName=CJK_FONT, fontSize=7, leading=10, textColor=COLORS['gray'],
        alignment=TA_CENTER, spaceBefore=1*mm, spaceAfter=3*mm)
    return {
        'title': s_title, 'subtitle': s_subtitle,
        'h1': s_h1, 'h2': s_h2, 'h3': s_h3, 'h4': s_h4,
        'normal': s_normal, 'bullet': s_bullet, 'code': s_code, 'quote': s_quote,
        'table_cell': s_table_cell, 'table_header': s_table_header, 'caption': s_caption
    }

STYLES = build_styles()

# HTML parser to convert html -> flowables

class MDHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.flowables = []
        self.current_tag_stack = []
        self.current_data = ""
        self.in_table = False
        self.table_rows = []  # list of list of cell html
        self.current_row = []
        self.current_cell = ""
        self.in_cell = False
        self.cell_is_header = False
        self.in_code_block = False
        self.code_content = ""
        self.in_blockquote = False
        self.blockquote_content = ""
        self.in_list = False
        self.list_type = None  # 'ul' or 'ol'
        self.list_items = []
        self.in_li = False
        self.li_content = ""
        self.in_heading = False
        self.heading_level = 0
        self.heading_content = ""
        self.in_paragraph = False
        self.paragraph_content = ""
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.current_tag_stack.append(tag)
        if tag in ('h1','h2','h3','h4','h5','h6'):
            self.in_heading = True
            self.heading_level = int(tag[1])
            self.heading_content = ""
        elif tag == 'p':
            self.in_paragraph = True
            self.paragraph_content = ""
        elif tag == 'blockquote':
            self.in_blockquote = True
            self.blockquote_content = ""
        elif tag in ('ul','ol'):
            self.in_list = True
            self.list_type = tag
            self.list_items = []
        elif tag == 'li':
            self.in_li = True
            self.li_content = ""
        elif tag == 'table':
            self.in_table = True
            self.table_rows = []
        elif tag == 'tr':
            self.current_row = []
        elif tag in ('th','td'):
            self.in_cell = True
            self.current_cell = ""
            self.cell_is_header = (tag == 'th')
        elif tag in ('pre','code'):
            # markdown fenced_code generates <pre><code>...
            if tag == 'pre':
                self.in_pre = True
                self.code_content = ""
            # inline code will be handled as text within paragraph
            if tag == 'code' and not self.in_pre:
                # inline code -> wrap with font tag later
                self.current_data = ""
        elif tag == 'hr':
            self.flowables.append(HRFlowable(width="100%", thickness=0.5, lineCap='round', color=COLORS['border'], spaceBefore=3*mm, spaceAfter=3*mm))
        elif tag == 'br':
            if self.in_paragraph:
                self.paragraph_content += "<br/>"
            elif self.in_li:
                self.li_content += "<br/>"
            elif self.in_heading:
                self.heading_content += "<br/>"

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.current_tag_stack and self.current_tag_stack[-1] == tag:
            self.current_tag_stack.pop()
        if tag in ('h1','h2','h3','h4','h5','h6'):
            if self.in_heading:
                text = self.heading_content.strip()
                # Clean html tags inside heading: keep <code> etc but convert to font
                # For headings, strip inline html to plain
                # Use Paragraph style per level
                if text:
                    # remove stray <code> wrappers but keep text
                    # Already markdown may have <code> inside heading - keep as <font>
                    # Simplify: strip any remaining tags except br
                    level = self.heading_level
                    if level == 1:
                        style = STYLES['h1']
                    elif level == 2:
                        style = STYLES['h2']
                    elif level == 3:
                        style = STYLES['h3']
                    else:
                        style = STYLES['h4']
                    # For h1, add a horizontal line after via HR
                    # Ensure text is escaped for Paragraph? We keep html inline like <b>, <i>, <code>
                    # Convert <code> to <font face> style
                    text = re.sub(r'<code>(.*?)</code>', r'<font color="#0f172a" backColor="#f1f5f9">\1</font>', text)
                    # paragraph will parse <b>, <i>, <br/>
                    p = Paragraph(text, style)
                    self.flowables.append(p)
                    if level == 1:
                        self.flowables.append(HRFlowable(width="100%", thickness=0.6, color=COLORS['primary'], spaceBefore=1*mm, spaceAfter=3*mm))
                self.in_heading = False
                self.heading_content = ""
        elif tag == 'p':
            if self.in_paragraph:
                txt = self.paragraph_content.strip()
                if txt:
                    # If inside blockquote, accumulate to quote instead of direct
                    if self.in_blockquote:
                        self.blockquote_content += txt + "<br/><br/>"
                    else:
                        # handle inline code already via <code> tags in handle_data? markdown generates <code> for inline
                        # But our parser captures inline <code> as separate tag; handle_data will have added to paragraph_content with font wrapping?
                        # Actually for inline code inside p, the HTML is <code>xxx</code>; our handle_starttag for code inside p not in_pre goes to current_data path, but we didn't handle.
                        # We need to capture inline code content. Our current_data handling via handle_data will add to paragraph_content via else clause? Need to check.
                        # For inline code, we have <code> tag start, then data, then </code>. We should wrap that data with font tag.
                        # Simplistic: after markdown conversion, inline code appears as <code>xxx</code> within p's innerHTML, but HTMLParser will call handle_starttag 'code', then handle_data, then handle_endtag 'code'.
                        # Our handle_data currently checks in_code? Let's handle inline code specially.
                        # Currently we treat in_paragraph and in_li etc, but inline code not in_pre, we need to handle separately.
                        # We'll handle inline code via a flag in_inline_code
                        p = Paragraph(txt, STYLES['normal'])
                        self.flowables.append(p)
                self.in_paragraph = False
                self.paragraph_content = ""
        elif tag == 'blockquote':
            if self.in_blockquote:
                txt = self.blockquote_content.strip()
                if txt:
                    # Remove trailing <br/>
                    txt = re.sub(r'(<br\s*/?>)+$', '', txt)
                    p = Paragraph(txt, STYLES['quote'])
                    # Add left border via Table wrapping
                    t = Table([[Paragraph('<font color="#2e75b6">▎</font>', STYLES['quote']), p]], colWidths=[6*mm, 160*mm])
                    t.setStyle(TableStyle([
                        ('VALIGN', (0,0), (-1,-1), 'TOP'),
                        ('LEFTPADDING', (0,0), (0,0), 0),
                        ('RIGHTPADDING', (0,0), (0,0), 0),
                        ('TOPPADDING', (0,0), (-1,-1), 2),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
                    ]))
                    self.flowables.append(t)
                    self.flowables.append(Spacer(1, 1*mm))
                self.in_blockquote = False
                self.blockquote_content = ""
        elif tag == 'li':
            if self.in_li:
                txt = self.li_content.strip()
                if txt:
                    self.list_items.append(txt)
                self.in_li = False
                self.li_content = ""
        elif tag in ('ul','ol'):
            if self.in_list:
                # flush list items
                for idx, item in enumerate(self.list_items):
                    bullet = '•' if self.list_type == 'ul' else f"{idx+1}."
                    # Use Paragraph with bulletText
                    p = Paragraph(item, STYLES['bullet'])
                    # Use custom bullet via Paragraph's bulletText
                    p.bulletText = bullet
                    self.flowables.append(p)
                self.flowables.append(Spacer(1, 1*mm))
                self.in_list = False
                self.list_items = []
                self.list_type = None
        elif tag in ('th','td'):
            if self.in_cell:
                cell_html = self.current_cell.strip()
                # keep inline formatting
                self.current_row.append((cell_html, self.cell_is_header))
                self.in_cell = False
                self.current_cell = ""
                self.cell_is_header = False
        elif tag == 'tr':
            if self.in_table:
                self.table_rows.append(self.current_row)
                self.current_row = []
        elif tag == 'table':
            if self.in_table:
                # build Table flowable
                if self.table_rows:
                    # Convert each cell html to Paragraph
                    data = []
                    for r_idx, row in enumerate(self.table_rows):
                        prow = []
                        for cell_html, is_header in row:
                            # Clean cell html: wrap code tags etc
                            cell_html = re.sub(r'<code>(.*?)</code>', r'<font color="#0f172a">\1</font>', cell_html)
                            # Empty cells handling
                            if not cell_html:
                                cell_html = "—"
                            style = STYLES['table_header'] if (r_idx == 0 or is_header) else STYLES['table_cell']
                            # For header, use white text; for normal use dark
                            prow.append(Paragraph(cell_html, style))
                        data.append(prow)
                    # calculate col widths: distribute page width
                    avail = PAGE_W - 40*mm  # left+right 20mm each
                    ncols = len(data[0]) if data else 1
                    # Try to estimate widths: equal or based on max length?
                    # For tables with many columns (like 4-6), equal width; for 2-col give 30/70
                    if ncols == 2:
                        col_ws = [avail*0.28, avail*0.72]
                    elif ncols == 3:
                        col_ws = [avail*0.22, avail*0.38, avail*0.40]
                    else:
                        col_ws = [avail / ncols] * ncols
                    # Adjust for overflow: reportlab will wrap
                    t = Table(data, colWidths=col_ws, repeatRows=1)
                    # style
                    tbl_style = [
                        ('GRID', (0,0), (-1,-1), 0.4, COLORS['border']),
                        ('BACKGROUND', (0,0), (-1,0), COLORS['table_header']),
                        ('TEXTCOLOR', (0,0), (-1,0), white),
                        ('ALIGN', (0,0), (-1,0), 'CENTER'),
                        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                        ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, HexColor('#f8fafc')]),
                        ('TOPPADDING', (0,0), (-1,-1), 3),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                        ('LEFTPADDING', (0,0), (-1,-1), 4),
                        ('RIGHTPADDING', (0,0), (-1,-1), 4),
                    ]
                    t.setStyle(TableStyle(tbl_style))
                    self.flowables.append(KeepTogether(t))
                    self.flowables.append(Spacer(1, 2*mm))
                self.in_table = False
                self.table_rows = []
        elif tag == 'pre':
            if self.in_pre:
                code = self.code_content
                # Preserve leading/trailing
                code = code.strip('\n')
                if code:
                    # Use Preformatted with style; need to handle long lines wrapping via XPreformatted
                    # Use XPreformatted for wrapping
                    try:
                        pf = XPreformatted(code, STYLES['code'], dedent=0)
                    except:
                        pf = Preformatted(code, STYLES['code'])
                    # Wrap in table for background
                    t = Table([[pf]], colWidths=[PAGE_W - 40*mm])
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,-1), COLORS['code_bg']),
                        ('BOX', (0,0), (-1,-1), 0.4, COLORS['border']),
                        ('TOPPADDING', (0,0), (-1,-1), 4),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                        ('LEFTPADDING', (0,0), (-1,-1), 4),
                        ('RIGHTPADDING', (0,0), (-1,-1), 4),
                    ]))
                    self.flowables.append(t)
                    self.flowables.append(Spacer(1, 2*mm))
                self.in_pre = False
                self.code_content = ""
        elif tag == 'code' and not self.in_pre:
            # end of inline code - nothing to do (handled via data)
            pass

    def handle_data(self, data):
        # data may be entity like &lt; etc. keep as is
        if not data:
            return
        # unescape minimal?
        # data may contain whitespace
        if self.in_heading:
            self.heading_content += data
        elif self.in_cell:
            self.current_cell += data
        elif self.in_pre:
            self.code_content += data
        elif self.in_li:
            self.li_content += data
        elif self.in_paragraph:
            # Check if parent is inline code? Look at stack
            if self.current_tag_stack and self.current_tag_stack[-1] == 'code' and not self.in_pre:
                # inline code -> wrap
                # Use monospace styling via font tag with background-ish
                escaped = data.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                self.paragraph_content += f'<font face="{CJK_FONT}" color="#0f172a" backColor="#f1f5f9">{escaped}</font>'
            else:
                # Also handle strong/em via tags: b, strong, em, i, a will add <b> etc wrapper? Actually HTMLParser will have already handled starttag for strong etc.
                # For data inside <strong> we need to wrap with <b>. Check stack for strong/b or em/i
                # We can check if stack contains strong/b/em/i/a/code
                # So wrap data accordingly
                stack_b = any(t in ('strong','b') for t in self.current_tag_stack)
                stack_i = any(t in ('em','i') for t in self.current_tag_stack)
                stack_code = any(t == 'code' for t in self.current_tag_stack) and not self.in_pre
                txt = data
                # Escape for paragraph
                txt = txt.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                if stack_code:
                    txt = f'<font face="{CJK_FONT}" color="#0f172a" backColor="#f1f5f9">{txt}</font>'
                if stack_b:
                    txt = f"<b>{txt}</b>"
                if stack_i:
                    txt = f"<i>{txt}</i>"
                # Handle links: if inside <a>, wrap with color
                if any(t == 'a' for t in self.current_tag_stack):
                    txt = f'<font color="#2e75b6">{txt}</font>'
                self.paragraph_content += txt
        elif self.in_blockquote:
            # blockquote may contain p inside? Already handled via paragraph path, but handle_data outside paragraph inside blockquote?
            # Rare, but capture
            txt = data.strip()
            if txt:
                self.blockquote_content += txt + " "
        else:
            # outside any specific, maybe stray text
            txt = data.strip()
            if txt:
                # create a paragraph if not empty
                # Could be text between elements
                self.flowables.append(Paragraph(txt, STYLES['normal']))

    def handle_entityref(self, name):
        # Convert &lt; etc to chars; will appear as data? Simplified: add as text
        ent = f"&{name};"
        self.handle_data(ent)

def md_to_flowables(md_text):
    # Use markdown to convert to html
    html = markdown.markdown(md_text, extensions=['tables','fenced_code','sane_lists','smarty'])
    # The markdown library wraps code blocks as <pre><code class="language-...">...</code></pre>
    # Also inline code as <code>...</code>
    parser = MDHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.flowables

def build_pdf(md_path, pdf_path, title, subtitle):
    md_text = pathlib.Path(md_path).read_text(encoding='utf-8')
    flow = md_to_flowables(md_text)

    # Add cover page manually
    cover = []
    # Top spacing
    cover.append(Spacer(1, 30*mm))
    # Title
    cover.append(Paragraph(title, STYLES['title']))
    cover.append(Spacer(1, 4*mm))
    cover.append(Paragraph(subtitle, STYLES['subtitle']))
    cover.append(Spacer(1, 6*mm))
    cover.append(HRFlowable(width="60%", thickness=1, color=COLORS['primary'], spaceAfter=6*mm, spaceBefore=0, hAlign='CENTER'))
    # Meta box
    meta_html = f"""
    <b>版本</b>：v1.1 详细版 / v3.0 配置字典（2026-09-18）<br/>
    <b>对齐</b>：REPORT v1.1 · NILM_DATA_DICT v0.2.10 · REPORT_TEST 18专题 · base_optimal lag5<br/>
    <b>分支</b>：arena/01a0896c-nilm-new · BOOTSTRAP v2.3<br/>
    <b>生产判定</b>：Conditional Ready（4户 infer F1 0.984/0.887/0.968/0.984，800 PAUSED）<br/>
    <b>读者</b>：无算法背景的工程师 / 运维 / 交付 / 现场实施<br/>
    """
    cover.append(Paragraph(meta_html.replace('\n','<br/>'), STYLES['normal']))
    cover.append(Spacer(1, 10*mm))
    # Quick TOC placeholder
    cover.append(Paragraph('<b>本文档目录</b>：见正文页眉及章内小节；建议配合 CONFIG_GUIDE 精确字典与 TUNING_GUIDE 人话 SOP 联动阅读。', STYLES['quote']))
    cover.append(Spacer(1, 12*mm))
    # Footer note
    cover.append(Paragraph('本 PDF 由 docs/generate_pdfs.py 自动生成（reportlab + STSong-Light CID，无需外置字体）<br/>源码：TUNING_GUIDE.md / docs/CONFIG_GUIDE.md ｜ 生成时间：2026-09-18', STYLES['caption']))
    cover.append(PageBreak())

    # Combine cover + content
    story = cover + flow

    # Add final page note
    story.append(PageBreak())
    story.append(Paragraph("附：版本与维护说明", STYLES['h1']))
    story.append(Paragraph("""
    本手册与 <b>docs/CONFIG_GUIDE.md</b> 联动维护：<b>TUNING_GUIDE</b> 为人话 SOP（为什么 + 怎么验），<b>CONFIG_GUIDE</b> 为精确字典（每个键干什么）。
    配置或方法论变更时两册同步重写对账（BOOTSTRAP 收尾§2）。<br/>
    稳定结论溯源：<b>REPORT.md v1.1</b> 8条稳定结论、<b>REPORT_TEST.md</b> 18专题、<b>NILM_DATA_DICT.md v0.2.10</b>。<br/>
    疑问先查 TUNING §9 战史 与 §10 FAQ，再查 REPORT_TEST 审计。<br/>
    800 设备 OQ-16 暂停（分路不在 p1/p2/p3，待 p4 重新定 target）。
    """, STYLES['normal']))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("— 完 —", STYLES['caption']))

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=16*mm,
        title=title, author="NILM_AC")
    doc.pdf_title = title
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(f"Generated {pdf_path}  ({os.path.getsize(pdf_path)/1024:.1f} KB)")

if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parent.parent
    # TUNING_GUIDE.pdf
    build_pdf(root / "TUNING_GUIDE.md", root / "docs" / "TUNING_GUIDE.pdf",
        title="工商业负荷辨识调参运维手册（人话详细版）",
        subtitle="TUNING_GUIDE v1.1  ·  新设备 0→1 八步流水线 · 两阈解耦 · 4模型择优 · 7项放行")
    # CONFIG_GUIDE.pdf
    build_pdf(root / "docs" / "CONFIG_GUIDE.md", root / "docs" / "CONFIG_GUIDE.pdf",
        title="工商业负荷辨识配置文件字典（技术精确版）",
        subtitle="CONFIG_GUIDE v3.0  ·  default.yaml + base_optimal.yaml + 用户 JSON · 含模型与训练效率指南")
