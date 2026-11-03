#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
做东西的文件格式（11-02 v1.17.4，小明：「他目前好像就会生成html文件 我不希望这个被写死」）
  以前做东西只认网页 / 代码：「帮我写一个docx总结」被当成网页，交了个 .html 还说「docx我生不出来」。
  现在她写内容（Markdown / 表格），这里按要的格式转成真文件；只用标准库，不用另外装库：
    .docx  Word：标题、段落、列表、粗体 / 斜体 / 行内代码、表格、引用、分隔线
    .xlsx  Excel：Markdown 表格或 CSV；「## 表名」分成几张表；数字存成数字
    .csv   表格（带 BOM，Excel 直接打开不乱码）
    .md / .txt  原样
    .pdf   先排成网页，再用这台电脑上的 Edge / Chrome（无界面）打印成 PDF；
           找不到浏览器 / 打印失败 → 换成 .docx，并说明原因（让她跟对方说一声）
    .pptx  装了 python-pptx 就做真的幻灯片；没装 → 每页一张的 PDF；再不行 → .docx

  build(ext, text, folder, base, title="", browser="") → (Path, 实际扩展名, 说明 或 "")
  available(browser="") → 这台电脑现在能做的格式（给她看 别再说「生不出来」）
  NAMES     扩展名 → 叫法
"""
import csv
import html as _html
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

NAMES = {".docx": "Word 文档", ".pdf": "PDF", ".xlsx": "Excel 表格", ".csv": "CSV 表格",
         ".pptx": "PPT 幻灯片", ".md": "Markdown", ".txt": "文本文件"}
EXTS = set(NAMES)
PDF_TIMEOUT = 90


# ───────────── Markdown → 块 ─────────────
_H = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LI = re.compile(r"^(\s*)(?:[-*+•·]\s+|(\d{1,3})(?:[.)]\s+|、\s*))(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|—{2,})\s*$")
_TSEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def blocks(text):
    """→ [("h", 级, 字) / ("p", 字) / ("li", 缩进级, 编号 or None, 字) / ("table", 行列表) / ("quote", 字) / ("hr",) / ("code", 字)]"""
    out, para, code = [], [], None
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def flush():
        if para:
            out.append(("p", "\n".join(para)))
            para.clear()

    i = 0
    while i < len(lines):
        ln = lines[i]
        if code is not None:                       # 文档里套的代码段（按说不会有 有就原样当等宽字）
            if ln.strip().startswith("```"):
                out.append(("code", "\n".join(code)))
                code = None
            else:
                code.append(ln)
            i += 1
            continue
        s = ln.strip()
        if s.startswith("```"):
            flush()
            code = []
            i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        m = _H.match(s)
        if m:
            flush()
            out.append(("h", len(m.group(1)), m.group(2)))
            i += 1
            continue
        if _HR.match(s) and not para:
            out.append(("hr",))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and _TSEP.match(lines[i + 1]):
            flush()
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append(("table", rows))
            continue
        if s.startswith("|") and s.endswith("|") and len(s) > 2 and i + 1 < len(lines) \
                and lines[i + 1].strip().startswith("|") and lines[i + 1].strip().endswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _TSEP.match(lines[i]):
                    rows.append(_cells(lines[i]))
                i += 1
            out.append(("table", rows))
            continue
        m = _LI.match(ln)
        if m:
            flush()
            lvl = min(len(m.group(1).replace("\t", "  ")) // 2, 3)
            out.append(("li", lvl, m.group(2), m.group(3)))
            i += 1
            continue
        if s.startswith(">"):
            flush()
            q = [s.lstrip(">").strip()]
            i += 1
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(("quote", "\n".join(q)))
            continue
        para.append(s)
        i += 1
    if code is not None:
        out.append(("code", "\n".join(code)))
    flush()
    return out


_INLINE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|(?<![*\\])\*(?![\s*])(.+?)(?<![\s*\\])\*(?!\*)|\[([^\]]+)\]\((\S+?)\)")


def runs(text):
    """一行字 → [(字, {"b","i","code"})]"""
    out, pos = [], 0
    t = str(text or "")
    for m in _INLINE.finditer(t):
        if m.start() > pos:
            out.append((t[pos:m.start()], set()))
        if m.group(1) or m.group(2):
            out.append((m.group(1) or m.group(2), {"b"}))
        elif m.group(3):
            out.append((m.group(3), {"code"}))
        elif m.group(4):
            out.append((m.group(4), {"i"}))
        else:
            out.append((f"{m.group(5)}（{m.group(6)}）", set()))
        pos = m.end()
    if pos < len(t):
        out.append((t[pos:], set()))
    return [(x, f) for x, f in out if x]


def plain(text):
    return "".join(x for x, _ in runs(text))


def title_of(text, dflt=""):
    for b in blocks(text):
        if b[0] == "h":
            return plain(b[2])[:60]
    return dflt


# ───────────── .docx（标准库拼 OOXML）─────────────
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _x(s):
    return _html.escape(_BAD_XML.sub("", str(s or "")), quote=False)


def _xa(s):
    return _html.escape(_BAD_XML.sub("", str(s or "")), quote=True)


def _w_runs(text, base=None):
    xml = []
    for seg, fl in runs(text):
        fl = set(fl) | set(base or ())
        rpr = ""
        if "b" in fl:
            rpr += "<w:b/><w:bCs/>"
        if "i" in fl:
            rpr += "<w:i/><w:iCs/>"
        if "code" in fl:
            rpr += ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
                    '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>')
        parts = seg.replace("\t", "    ").split("\n")
        for k, p in enumerate(parts):
            br = "<w:br/>" if k else ""
            xml.append(f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}{br}'
                       f'<w:t xml:space="preserve">{_x(p)}</w:t></w:r>')
    return "".join(xml)


def _w_p(text, style=None, ppr="", base=None):
    pp = (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr
    return f'<w:p>{"<w:pPr>" + pp + "</w:pPr>" if pp else ""}{_w_runs(text, base)}</w:p>'


def _w_table(rows):
    n = max(len(r) for r in rows)
    rows = [list(r) + [""] * (n - len(r)) for r in rows]
    width = 9000 // max(n, 1)
    bd = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                 for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    xml = [f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{bd}</w:tblBorders>'
           '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>'
           '<w:tblLook w:val="04A0"/></w:tblPr><w:tblGrid>' + f'<w:gridCol w:w="{width}"/>' * n + "</w:tblGrid>"]
    for k, r in enumerate(rows):
        head = k == 0
        xml.append("<w:tr>" + ('<w:trPr><w:tblHeader/></w:trPr>' if head else ""))
        for c in r:
            shd = '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>' if head else ""
            xml.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>'
                       + _w_p(c.replace("<br>", "\n"), ppr='<w:spacing w:after="0"/>', base={"b"} if head else None)
                       + "</w:tc>")
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


_DOCX_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W_NS}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="312" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="240"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="567"/><w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="BFBFBF"/></w:pBdr></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="CodeBlock"><w:name w:val="Code Block"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:style>
</w:styles>"""


def _zip(path, files):
    tmp = Path(str(path) + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    os.replace(tmp, path)


def _core(title):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{_x(title)}</dc:title><dc:creator>fatfish-bot</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')


_APP = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>fatfish</Application></Properties>')
_RELS_ROOT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{main}"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
              '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
              '</Relationships>')
_CT_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>')


def to_docx(text, path, title=""):
    bl = blocks(text)
    hs = [b for b in bl if b[0] == "h"]
    has_title = bool(hs) and hs[0][1] == 1 and sum(1 for b in hs if b[1] == 1) == 1    # 只有开头一个「# 」→ 当文档标题
    body, first_h = [], True
    for b in bl:
        k = b[0]
        if k == "h":
            lvl = b[1]
            if has_title and first_h and lvl == 1:
                body.append(_w_p(b[2], "Title"))
            else:
                body.append(_w_p(b[2], f"Heading{min(max(lvl - 1 if has_title else lvl, 1), 3)}"))
            first_h = False
        elif k == "p":
            body.append(_w_p(b[1]))
        elif k == "li":
            lvl, num, t = b[1], b[2], b[3]
            mark = f"{num}." if num else "•"
            left, hang = 420 + lvl * 420, 300
            body.append(f'<w:p><w:pPr><w:spacing w:after="60"/><w:ind w:left="{left}" w:hanging="{hang}"/></w:pPr>'
                        f'<w:r><w:t xml:space="preserve">{_x(mark)} </w:t></w:r>{_w_runs(t)}</w:p>')
        elif k == "table":
            body.append(_w_table(b[1]))
            body.append('<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')
        elif k == "quote":
            body.append(_w_p(b[1], "Quote"))
        elif k == "hr":
            body.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="BFBFBF"/></w:pBdr></w:pPr></w:p>')
        elif k == "code":
            for ln in (b[1].split("\n") or [""]):
                body.append(f'<w:p><w:pPr><w:pStyle w:val="CodeBlock"/></w:pPr>'
                            f'<w:r><w:t xml:space="preserve">{_x(ln.replace(chr(9), "    "))}</w:t></w:r></w:p>')
    if not body:
        body.append(_w_p(""))
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{W_NS}"><w:body>'
           + "".join(body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
           '<w:pgMar w:top="1418" w:right="1304" w:bottom="1418" w:left="1304" w:header="851" w:footer="851" w:gutter="0"/>'
           '</w:sectPr></w:body></w:document>')
    ct = (_CT_HEAD + '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>')
    _zip(path, [("[Content_Types].xml", ct), ("_rels/.rels", _RELS_ROOT.format(main="word/document.xml")),
                ("word/document.xml", doc), ("word/styles.xml", _DOCX_STYLES),
                ("word/_rels/document.xml.rels", rels), ("docProps/core.xml", _core(title or title_of(text))),
                ("docProps/app.xml", _APP)])


# ───────────── 表格：Markdown 表格 / CSV → 几张表 ─────────────
_NUM = re.compile(r"^-?(?:0|[1-9]\d{0,14})(?:\.\d+)?$")


def sheets(text):
    """→ [(表名, 行列表)]：「## 表名」分表；表里是 Markdown 表格就按表格读，不然按 CSV / 制表符读"""
    out, name, buf = [], "", []

    def flush():
        rows = []
        lines = [x for x in buf if x.strip()]
        if any(x.strip().startswith("|") for x in lines):
            for b in blocks("\n".join(buf)):
                if b[0] == "table":
                    rows += b[1] if not rows else b[1][1:] if b[1][:1] == rows[:1] else b[1]
        elif lines:
            sample = "\n".join(lines)
            delim = "\t" if sample.count("\t") > sample.count(",") else ","
            rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(sample), delimiter=delim)]
        rows = [[plain(c) for c in r] for r in rows if any(str(c).strip() for c in r)]
        if rows:
            out.append((name, rows))
        buf.clear()

    for ln in str(text or "").replace("\r\n", "\n").split("\n"):
        m = _H.match(ln.strip())
        if m:
            flush()
            name = plain(m.group(2))
            continue
        buf.append(ln)
    flush()
    return out


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _wide(s):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def _sheet_name(name, used, k):
    n = re.sub(r"[\[\]:*?/\\]", "", str(name or "")).strip().strip("'")[:31] or f"表{k}"
    base, i = n, 2
    while n.lower() in used:
        n = f"{base[:28]}-{i}"
        i += 1
    used.add(n.lower())
    return n


def to_xlsx(text, path, title=""):
    data = sheets(text) or [("", [[plain(x)] for x in str(text or "").split("\n") if x.strip()] or [[""]])]
    used, names, xml_sheets = set(), [], []
    for k, (nm, rows) in enumerate(data, 1):
        names.append(_sheet_name(nm, used, k))
        ncol = max(len(r) for r in rows)
        widths = [min(max([_wide(r[c]) if c < len(r) else 0 for r in rows] + [4]) + 2, 60) for c in range(ncol)]
        cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>' for c, w in enumerate(widths))
        sd = []
        for ri, r in enumerate(rows, 1):
            cells = []
            for ci, v in enumerate(r):
                ref = f"{_col(ci)}{ri}"
                v = str(v).strip()
                if v == "":
                    continue
                st = ' s="1"' if ri == 1 else ""
                if ri > 1 and _NUM.match(v.replace(",", "")) and len(v.replace(",", "")) <= 15:
                    cells.append(f'<c r="{ref}"{st}><v>{v.replace(",", "")}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{_x(v)}</t></is></c>')
            sd.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
        pane = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                'state="frozen"/></sheetView></sheetViews>') if len(rows) > 1 else ""
        xml_sheets.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                          f'{pane}<cols>{cols}</cols><sheetData>{"".join(sd)}</sheetData></worksheet>')
    n = len(xml_sheets)
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{_xa(nm)}" sheetId="{i}" r:id="rId{i}"/>' for i, nm in enumerate(names, 1))
          + "</sheets></workbook>")
    wrels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                       f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
             + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
               'Target="styles.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="等线"/><family val="2"/></font>'
              '<font><b/><sz val="11"/><name val="等线"/><family val="2"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    ct = (_CT_HEAD + '<Override PartName="/xl/workbook.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, n + 1)) + "</Types>")
    files = [("[Content_Types].xml", ct), ("_rels/.rels", _RELS_ROOT.format(main="xl/workbook.xml")),
             ("xl/workbook.xml", wb), ("xl/_rels/workbook.xml.rels", wrels), ("xl/styles.xml", styles),
             ("docProps/core.xml", _core(title or names[0])), ("docProps/app.xml", _APP)]
    files += [(f"xl/worksheets/sheet{i}.xml", x) for i, x in enumerate(xml_sheets, 1)]
    _zip(path, files)


def to_csv(text, path):
    data = sheets(text)
    if data:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for k, (nm, rows) in enumerate(data):
            if len(data) > 1:
                if k:
                    w.writerow([])
                w.writerow([f"## {nm or f'表{k + 1}'}"])
            w.writerows(rows)
        s = buf.getvalue()
    else:
        s = str(text or "")
    Path(path).write_text(s, encoding="utf-8-sig", newline="")


# ───────────── 网页 → PDF（Edge / Chrome 无界面打印）─────────────
_CSS = ("@page{size:A4;margin:18mm 16mm}"
        "body{font-family:'Microsoft YaHei','PingFang SC','Noto Sans CJK SC','Noto Sans SC',sans-serif;"
        "font-size:11pt;line-height:1.65;color:#222;margin:0}"
        "h1{font-size:20pt;text-align:center;margin:0 0 14pt}h2{font-size:15pt;margin:16pt 0 6pt}"
        "h3{font-size:12.5pt;margin:12pt 0 4pt}p{margin:0 0 7pt}ul,ol{margin:0 0 7pt;padding-left:22pt}li{margin:2pt 0}"
        "table{border-collapse:collapse;width:100%;margin:6pt 0 10pt}th,td{border:1px solid #999;padding:4pt 6pt;"
        "vertical-align:top}th{background:#f2f2f2}blockquote{margin:6pt 0;padding-left:10pt;border-left:3pt solid #ccc;"
        "color:#555}code,pre{font-family:Consolas,monospace;background:#f2f2f2}pre{padding:6pt;white-space:pre-wrap}"
        "hr{border:0;border-top:1px solid #ccc;margin:10pt 0}")
_SLIDE_CSS = ("@page{size:13.333in 7.5in;margin:0}"
              "body{font-family:'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif;margin:0;color:#222}"
              ".s{width:13.333in;height:7.5in;box-sizing:border-box;padding:.7in .9in;page-break-after:always;overflow:hidden}"
              ".s:last-child{page-break-after:auto}.s h1{font-size:40pt;margin:2in 0 .2in;text-align:center}"
              ".s h2{font-size:30pt;margin:0 0 .3in;border-bottom:3px solid #2f5597;padding-bottom:.1in}"
              ".s li,.s p{font-size:20pt;line-height:1.5;margin:.08in 0}"
              "table{border-collapse:collapse}th,td{border:1px solid #999;padding:4pt 8pt;font-size:16pt}")


def _h_inline(text):
    out = []
    for seg, fl in runs(text):
        s = _html.escape(seg).replace("\n", "<br>")
        if "code" in fl:
            s = f"<code>{s}</code>"
        if "i" in fl:
            s = f"<em>{s}</em>"
        if "b" in fl:
            s = f"<strong>{s}</strong>"
        out.append(s)
    return "".join(out)


def _h_blocks(bl):
    out, lst = [], None
    for b in bl:
        k = b[0]
        if k != "li" and lst:
            out.append(f"</{lst}>")
            lst = None
        if k == "h":
            out.append(f"<h{b[1]}>{_h_inline(b[2])}</h{b[1]}>")
        elif k == "p":
            out.append(f"<p>{_h_inline(b[1])}</p>")
        elif k == "li":
            tag = "ol" if b[2] else "ul"
            if lst != tag:
                if lst:
                    out.append(f"</{lst}>")
                out.append(f"<{tag}>")
                lst = tag
            out.append(f'<li style="margin-left:{b[1] * 18}pt">{_h_inline(b[3])}</li>' if b[1] else f"<li>{_h_inline(b[3])}</li>")
        elif k == "table":
            rows = b[1]
            out.append("<table><tr>" + "".join(f"<th>{_h_inline(c)}</th>" for c in rows[0]) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_h_inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                       + "</table>")
        elif k == "quote":
            out.append(f"<blockquote>{_h_inline(b[1])}</blockquote>")
        elif k == "hr":
            out.append("<hr>")
        elif k == "code":
            out.append(f"<pre>{_html.escape(b[1])}</pre>")
    if lst:
        out.append(f"</{lst}>")
    return "".join(out)


def to_html(text, title="", slides=False):
    bl = blocks(text)
    t = _html.escape(title or title_of(text) or "文档")
    if not slides:
        return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>{t}</title>'
                f"<style>{_CSS}</style></head><body>{_h_blocks(bl)}</body></html>")
    pages, cur = [], []
    for b in bl:
        if b[0] == "h" and b[1] <= 2 and cur:
            pages.append(cur)
            cur = []
        cur.append(b)
    if cur:
        pages.append(cur)
    body = "".join(f'<div class="s">{_h_blocks(p)}</div>' for p in pages)
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>{t}</title>'
            f"<style>{_SLIDE_CSS}</style></head><body>{body}</body></html>")


def find_browser(prefer=""):
    """这台电脑上的 Edge / Chrome（打印 PDF 用）；config make.pdf_browser 可以指定"""
    cands = [prefer] if prefer else []
    if os.name == "nt":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for name in ("msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def html_to_pdf(html, out, browser, timeout=PDF_TIMEOUT):
    """无界面打印：临时文件夹放网页和一份单独的浏览器配置（不碰电脑上正在用的 Edge）；打印完删掉。
    浏览器打完了有时不退出 → 看着 PDF 写完（连着两次一样大、结尾是 %%EOF）就关掉它"""
    out = Path(out)
    tmp = Path(tempfile.mkdtemp(prefix="fatfish_pdf_"))
    p = None
    try:
        src = tmp / "doc.html"
        src.write_text(html, encoding="utf-8")
        dst = tmp / "out.pdf"                              # 先打到英文名 再挪过去（文件名里有中文也不怕）
        cmd = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-background-networking", "--disable-component-update", "--disable-sync",
               "--disable-default-apps", f"--user-data-dir={tmp / 'profile'}", "--no-pdf-header-footer",
               "--print-to-pdf-no-header", f"--print-to-pdf={dst}", src.as_uri()]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")                  # Linux 上用 root 跑时 Chrome 要这个
        flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW：别闪黑框
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        t0, last, same = time.time(), -1, 0
        while time.time() - t0 < timeout:
            done = p.poll() is not None
            if dst.exists():
                size = dst.stat().st_size
                same = same + 1 if size == last and size > 500 else 0
                last = size
                if done or same >= 2:
                    break
            elif done:
                break
            time.sleep(0.5)
        data = dst.read_bytes() if dst.exists() else b""
        if not (len(data) > 500 and data[:5] == b"%PDF-" and b"%%EOF" in data[-1024:]):
            return False
        shutil.move(str(dst), str(out))
        return True
    except Exception:
        return False
    finally:
        if p is not None and p.poll() is None:
            try:
                p.kill()
                p.wait(5)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def to_pptx(text, path, title=""):
    from pptx import Presentation          # 没装就 ImportError → 换别的
    from pptx.util import Pt
    prs = Presentation()
    bl = blocks(text)
    pages, cur = [], []
    for b in bl:
        if b[0] == "h" and b[1] <= 2 and cur:
            pages.append(cur)
            cur = []
        cur.append(b)
    if cur:
        pages.append(cur)
    for k, pg in enumerate(pages):
        head = next((plain(b[2]) for b in pg if b[0] == "h"), "")
        rest = [b for b in pg if b[0] != "h"]
        if k == 0 and pg and pg[0][0] == "h" and pg[0][1] == 1 and len(rest) <= 2:
            s = prs.slides.add_slide(prs.slide_layouts[0])
            s.shapes.title.text = head
            if rest and len(s.placeholders) > 1:
                s.placeholders[1].text = "\n".join(plain(b[1] if b[0] in ("p", "quote") else b[-1]) for b in rest
                                                   if b[0] in ("p", "quote", "li"))
            continue
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = head
        tf = s.placeholders[1].text_frame
        first = True
        for b in rest:
            if b[0] == "table":
                lines = [" | ".join(r) for r in b[1]]
            elif b[0] == "li":
                lines = [plain(b[3])]
            elif b[0] in ("p", "quote", "code"):
                lines = [plain(x) for x in str(b[1]).split("\n") if x.strip()]
            else:
                continue
            for ln in lines:
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                p.text = ln
                p.level = b[1] if b[0] == "li" else 0
                for r in p.runs:
                    r.font.size = Pt(20)
    prs.core_properties.title = title or title_of(text)
    prs.save(str(path))


# ───────────── 入口 ─────────────
def available(browser=""):
    """这台电脑现在能直接做的格式"""
    out = [".docx", ".xlsx", ".csv", ".md", ".txt"]
    b = find_browser(browser)
    if b:
        out.append(".pdf")
    try:
        import pptx  # noqa: F401
        out.append(".pptx")
    except Exception:
        if b:
            out.append(".pptx")            # 没装 python-pptx：做成每页一张的 PDF（会说明）
    return out


def _unique(folder, base, ext):
    p = Path(folder) / f"{base}{ext}"
    i = 2
    while p.exists():
        p = Path(folder) / f"{base}-{i}{ext}"
        i += 1
    return p


def build(ext, text, folder, base, title="", browser=""):
    """→ (Path, 实际扩展名, 说明)；说明不空 = 换了格式（要让她跟对方说一声）"""
    ext = ext if ext in EXTS else ".docx"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", str(base or "")).strip().strip(".") or "文档"
    text = str(text or "").strip("\n")
    title = title or title_of(text) or base
    if ext in (".md", ".txt"):
        p = _unique(folder, base, ext)
        p.write_text(text.rstrip() + "\n", encoding="utf-8")
        return p, ext, ""
    if ext == ".csv":
        p = _unique(folder, base, ext)
        to_csv(text, p)
        return p, ext, ""
    if ext == ".xlsx":
        p = _unique(folder, base, ext)
        to_xlsx(text, p, title)
        return p, ext, ""
    note = ""
    if ext == ".pptx":
        p = _unique(folder, base, ".pptx")
        try:
            to_pptx(text, p, title)
            return p, ".pptx", ""
        except ImportError:
            note = "这台电脑没装做 PPT 的库"
        except Exception as e:
            note = f"做 PPT 出错（{type(e).__name__}）"
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        b = find_browser(browser)
        if b:
            p = _unique(folder, base, ".pdf")
            if html_to_pdf(to_html(text, title, slides=True), p, b):
                return p, ".pdf", note + "，做成了每页一张的 PDF"
        p = _unique(folder, base, ".docx")
        to_docx(text, p, title)
        return p, ".docx", note + "，做成了 Word 文档"
    if ext == ".pdf":
        b = find_browser(browser)
        if b:
            p = _unique(folder, base, ".pdf")
            if html_to_pdf(to_html(text, title), p, b):
                return p, ".pdf", ""
            note = "PDF 打印失败"
        else:
            note = "这台电脑上没找到能打印 PDF 的浏览器"
        p = _unique(folder, base, ".docx")
        to_docx(text, p, title)
        return p, ".docx", note + "，换成了 Word 文档（Word 里能另存为 PDF）"
    p = _unique(folder, base, ".docx")
    to_docx(text, p, title)
    return p, ".docx", ""
