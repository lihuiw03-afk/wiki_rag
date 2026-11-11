from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import csv, hashlib, io, json, re

try:
    from docx import Document as DocxDocument
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
except Exception:
    DocxDocument = Paragraph = Table = CT_P = CT_Tbl = None
try:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except Exception:
    Presentation = MSO_SHAPE_TYPE = None
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None
try:
    import fitz  # optional PyMuPDF
except Exception:
    fitz = None
try:
    from openpyxl import load_workbook
except Exception:
    load_workbook = None

SUPPORTED = {".docx", ".pptx", ".pdf", ".md", ".txt", ".csv", ".xlsx", ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class EvidenceBlock:
    block_id: str
    kind: str
    text: str
    breadcrumb: list[str]
    locator: str
    metadata: dict

    def to_dict(self):
        return asdict(self)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_sidecar(path: Path) -> dict:
    meta = path.with_name(path.name + ".meta.json")
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _add(blocks, kind, text, breadcrumb, locator, **metadata):
    text = str(text or "").strip()
    if text:
        blocks.append(EvidenceBlock(f"b{len(blocks):05d}", kind, text, list(breadcrumb), locator, metadata))


def _table_md(rows):
    rows = [[str(c or "").replace("\n", " ").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [(r + [""] * width)[:width] for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(out)


def _docx(path):
    if DocxDocument is None:
        return [], ["python-docx 未安装"], False
    doc = DocxDocument(path); blocks=[]; warnings=[]; stack=[]; pi=ti=0
    for si, section in enumerate(doc.sections, 1):
        for label, container in (("页眉", section.header), ("页脚", section.footer)):
            txt = "\n".join(p.text.strip() for p in container.paragraphs if p.text.strip())
            _add(blocks, "header_footer", txt, [label], f"docx:section:{si}:{label}")
    for child in doc.element.body.iterchildren():
        if CT_P is not None and isinstance(child, CT_P):
            pi += 1; p=Paragraph(child, doc); text=p.text.strip()
            if not text: continue
            style=(p.style.name or "") if p.style else ""
            m=re.search(r"heading\s*(\d+)", style, re.I)
            if m:
                level=max(1,min(6,int(m.group(1))))
                while stack and stack[-1][0] >= level: stack.pop()
                stack.append((level,text))
                _add(blocks,"heading","#"*level+" "+text,[x[1] for x in stack],f"docx:p:{pi}",style=style)
            else:
                prefix="- " if "list" in style.lower() else ""
                _add(blocks,"paragraph",prefix+text,[x[1] for x in stack],f"docx:p:{pi}",style=style)
        elif CT_Tbl is not None and isinstance(child, CT_Tbl):
            ti += 1; table=Table(child, doc)
            rows=[[c.text for c in r.cells] for r in table.rows]
            crumb=[x[1] for x in stack]+[f"表格 {ti}"]
            _add(blocks,"table",f"## 表格 {ti}\n\n{_table_md(rows)}",crumb,f"docx:table:{ti}",rows=len(rows))
    warnings.append("DOCX 脚注、批注与修订信息可能不完整；原始文原始文件仍应作为最终事实源。")
    return blocks,warnings,False


def _pptx(path):
    if Presentation is None:
        return [], ["python-pptx 未安装"], False
    prs=Presentation(path); blocks=[]; warnings=[]; needs_vision=False
    for i,slide in enumerate(prs.slides,1):
        title=""
        try:
            if slide.shapes.title and slide.shapes.title.text: title=slide.shapes.title.text.strip()
        except Exception: pass
        slide_name=title or f"幻灯片 {i}"
        _add(blocks,"slide_heading",f"# 幻灯片 {i}：{slide_name}" if title else f"# 幻灯片 {i}",[slide_name],f"pptx:slide:{i}")
        shapes=sorted(list(slide.shapes),key=lambda s:(getattr(s,"top",0),getattr(s,"left",0)))
        for j,shape in enumerate(shapes,1):
            locator=f"pptx:slide:{i}:shape:{j}"; crumb=[slide_name]
            if getattr(shape,"has_table",False):
                rows=[[c.text for c in r.cells] for r in shape.table.rows]
                _add(blocks,"table","## 表格\n\n"+_table_md(rows),crumb+["表格"],locator,shape_name=getattr(shape,"name","")); continue
            if getattr(shape,"has_chart",False):
                lines=["## 图表数据"]
                try:
                    for series in shape.chart.series:
                        lines.append(f"- 系列 {getattr(series,'name','')}: {list(series.values)}")
                    _add(blocks,"chart","\n".join(lines),crumb+["图表"],locator,shape_name=getattr(shape,"name",""))
                except Exception:
                    warnings.append(f"PPT 第 {i} 页图表存在但无法完整提取数据。")
                continue
            text=(getattr(shape,"text","") or "").strip()
            if text and text != title:
                _add(blocks,"textbox",text,crumb,locator,shape_name=getattr(shape,"name",""),left=getattr(shape,"left",None),top=getattr(shape,"top",None))
            if MSO_SHAPE_TYPE is not None and getattr(shape,"shape_type",None)==MSO_SHAPE_TYPE.PICTURE:
                needs_vision=True; _add(blocks,"visual_marker",f"[图片对象：{getattr(shape,'name','Picture')}；需要 Vision 才能恢复视觉描述]",crumb,locator)
        try: notes=slide.notes_slide.notes_text_frame.text.strip()
        except Exception: notes=""
        if notes: _add(blocks,"speaker_notes","## 演讲者备注\n\n"+notes,[slide_name,"演讲者备注"],f"pptx:slide:{i}:notes")
    if needs_vision: warnings.append("PPT 含图片/视觉对象；文本/表格/可读取图表信息已保留，视觉语义需要 Vision。")
    return blocks,warnings,needs_vision


def _pdf(path):
    blocks=[]; warnings=[]; needs_vision=False
    if fitz is not None:
        try:
            doc=fitz.open(path)
            for i,page in enumerate(doc,1):
                crumb=[f"PDF 第 {i} 页"]; _add(blocks,"page_heading",f"# PDF 第 {i} 页",crumb,f"pdf:page:{i}")
                page_has_text=False
                for j,b in enumerate(sorted(page.get_text("blocks") or [],key=lambda x:(x[1],x[0])),1):
                    text=str(b[4] or "").strip()
                    if text:
                        page_has_text=True; _add(blocks,"pdf_text_block",text,crumb,f"pdf:page:{i}:block:{j}",bbox=[b[0],b[1],b[2],b[3]])
                images=page.get_images(full=True)
                if images:
                    needs_vision=True; _add(blocks,"visual_marker",f"[本页包含 {len(images)} 个图片对象；需要 Vision 才能恢复视觉描述]",crumb,f"pdf:page:{i}:images")
                if not page_has_text:
                    needs_vision=True; _add(blocks,"scan_marker","[扫描/图片型页面：无文本层，需要 Vision/OCR。]",crumb,f"pdf:page:{i}:scan")
            doc.close()
            if needs_vision: warnings.append("PDF 存在图片/扫描页面；文本块已保留，视觉内容需 Vision/OCR。")
            return blocks,warnings,needs_vision
        except Exception as e:
            warnings.append(f"PyMuPDF 提取失败，回退 pypdf: {e}")
    if PdfReader is None: return [], warnings+["pypdf 未安装"], False
    try: reader=PdfReader(str(path))
    except Exception as e: return [],warnings+[f"PDF 读取失败: {e}"],False
    for i,page in enumerate(reader.pages,1):
        crumb=[f"PDF 第 {i} 页"]; _add(blocks,"page_heading",f"# PDF 第 {i} 页",crumb,f"pdf:page:{i}")
        try: text=(page.extract_text() or "").strip()
        except Exception: text=""
        if text: _add(blocks,"pdf_page_text",text,crumb,f"pdf:page:{i}:text")
        else:
            needs_vision=True; _add(blocks,"scan_marker","[扫描/图片型页面：无文本层，需要 Vision/OCR。]",crumb,f"pdf:page:{i}:scan")
    if needs_vision: warnings.append("PDF 存在无文本层页面，需要 Vision/OCR。")
    return blocks,warnings,needs_vision


def _xlsx(path):
    if load_workbook is None: return [],["openpyxl 未安装"],False
    wf=load_workbook(path,read_only=True,data_only=False)
    try: wv=load_workbook(path,read_only=True,data_only=True)
    except Exception: wv=None
    blocks=[]; warnings=[]
    for ws in wf.worksheets:
        _add(blocks,"sheet_heading",f"# 工作表：{ws.title}",[ws.title],f"xlsx:sheet:{ws.title}")
        vws=wv[ws.title] if wv and ws.title in wv.sheetnames else None
        for r_idx,row in enumerate(ws.iter_rows(),1):
            cells=[]
            for c_idx,cell in enumerate(row,1):
                if cell.value is None: continue
                value=cell.value; cached=vws.cell(r_idx,c_idx).value if vws else None
                if isinstance(value,str) and value.startswith("="):
                    cells.append(f"{cell.coordinate}={value}"+(f" [cached={cached}]" if cached is not None else ""))
                else: cells.append(f"{cell.coordinate}={value}")
            if cells: _add(blocks,"sheet_row"," | ".join(cells),[ws.title,f"第 {r_idx} 行"],f"xlsx:{ws.title}:row:{r_idx}")
    wf.close(); wv and wv.close()
    warnings.append("XLSX 保留非空单元格、公式与可用缓存值；图表/图片/格式视觉语义需额外解析。")
    return blocks,warnings,False


def _text(path):
    text=path.read_text(encoding="utf-8",errors="replace")
    if path.suffix.lower()==".csv":
        blocks=[]
        for i,row in enumerate(csv.reader(io.StringIO(text)),1): _add(blocks,"csv_row"," | ".join(row),[f"第 {i} 行"],f"csv:row:{i}")
        return blocks,[],False
    blocks=[]; stack=[]; buf=[]; start_line=1; lines=text.splitlines()
    def flush(end_line):
        nonlocal buf
        payload="\n".join(buf).strip("\n")
        if payload.strip(): _add(blocks,"text",payload,[x[1] for x in stack],f"text:lines:{start_line}-{end_line}")
        buf=[]
    for i,line in enumerate(lines,1):
        m=re.match(r"^(#{1,6})\s+(.+)$",line)
        if m:
            if buf: flush(i-1)
            level,title=len(m.group(1)),m.group(2).strip()
            while stack and stack[-1][0]>=level: stack.pop()
            stack.append((level,title)); _add(blocks,"heading",line,[x[1] for x in stack],f"text:line:{i}"); start_line=i+1
        else:
            if not buf: start_line=i
            buf.append(line)
    if buf: flush(len(lines))
    return blocks,[],False



def _image(path):
    blocks=[]
    _add(blocks,"visual_marker",f"[图片文件：{path.name}；OCR/Vision 解析结果将在多模态证据阶段写入。]",[path.name],"image:file")
    return blocks,[],True

def extract_document(path: Path, visual_processor=None) -> dict:
    path=path.resolve(); ext=path.suffix.lower()
    if ext==".docx": blocks,warnings,needs_vision=_docx(path)
    elif ext==".pptx": blocks,warnings,needs_vision=_pptx(path)
    elif ext==".pdf": blocks,warnings,needs_vision=_pdf(path)
    elif ext==".xlsx": blocks,warnings,needs_vision=_xlsx(path)
    elif ext in {".png",".jpg",".jpeg",".webp",".bmp",".tif",".tiff"}: blocks,warnings,needs_vision=_image(path)
    elif ext in {".md",".txt",".csv",".json",".yaml",".yml"}: blocks,warnings,needs_vision=_text(path)
    else: blocks,warnings,needs_vision=[],[f"unsupported extension: {ext}"],False
    doc = {"source_path":str(path),"sha256":sha256_file(path),"extension":ext,"blocks":[b.to_dict() for b in blocks],"warnings":warnings,"needs_vision":needs_vision}
    if visual_processor is not None:
        doc = visual_processor.enrich(doc, path)
    return doc


def extracted_markdown(doc: dict) -> str:
    out=[]
    for b in doc.get("blocks",[]):
        meta = b.get("metadata") or {}
        asset = meta.get("visual_asset")
        extra = f"; visual_asset: {asset}" if asset else ""
        out.append(f"<!-- source_locator: {b.get('locator') or ''}; kind: {b.get('kind') or ''}; breadcrumb: {' > '.join(b.get('breadcrumb') or [])}{extra} -->")
        out.append(str(b.get("text") or "")); out.append("")
    if doc.get("warnings"):
        out.append("## 提取说明"); out.extend(f"- {w}" for w in doc["warnings"])
    return "\n".join(out).strip()+"\n"


def _split_lossless(text,max_chars,overlap):
    if len(text)<=max_chars:return [text]
    parts=[];start=0
    while start<len(text):
        target=min(len(text),start+max_chars);end=target
        if target<len(text):
            floor=start+max_chars//2;best=max(text.rfind("\n\n",floor,target),text.rfind("\n",floor,target),text.rfind("。",floor,target))
            if best>start:end=best+1
        parts.append(text[start:end])
        if end>=len(text):break
        start=max(start+1,end-overlap)
    return parts


def chunk_extracted_document(doc: dict,max_chars=3200,overlap=350):
    expanded=[]
    for b in doc.get("blocks",[]):
        pieces=_split_lossless(str(b.get("text") or ""),max_chars,overlap)
        for piece in pieces:
            meta = b.get("metadata") or {}
            expanded.append({"block_id":b.get("block_id"),"kind":b.get("kind"),"text":piece,"breadcrumb":list(b.get("breadcrumb") or []),"locator":b.get("locator"),"visual_asset":meta.get("visual_asset"),"visual_kind":meta.get("visual_kind")})
    chunks=[];buf=[];size=0
    def flush():
        nonlocal buf,size
        if not buf:return
        text="\n\n".join(x["text"] for x in buf).strip();crumb=next((x["breadcrumb"] for x in reversed(buf) if x["breadcrumb"]),[])
        chunks.append({"breadcrumb":crumb,"text":text,"block_ids":[x["block_id"] for x in buf if x.get("block_id")],"locators":[x["locator"] for x in buf if x.get("locator")],"kinds":list(dict.fromkeys(x["kind"] for x in buf if x.get("kind"))),"visual_assets":list(dict.fromkeys(x.get("visual_asset") for x in buf if x.get("visual_asset"))),"visual_kinds":list(dict.fromkeys(x.get("visual_kind") for x in buf if x.get("visual_kind")))})
        buf=[];size=0
    for item in expanded:
        n=len(item["text"])+2
        if buf and size+n>max_chars:
            prior="\n\n".join(x["text"] for x in buf);locs=[x["locator"] for x in buf if x.get("locator")];flush()
            if overlap and prior:
                buf.append({"block_id":None,"kind":"overlap","text":"[上文连续片段] "+prior[-overlap:],"breadcrumb":item.get("breadcrumb") or [],"locator":locs[-1] if locs else None});size=len(buf[0]["text"])
        buf.append(item);size+=n
    flush();return chunks

# Compatibility helpers.
def extract_text(path: Path)->str:return extracted_markdown(extract_document(path))
def chunk_markdown(text: str,max_chars=3200,overlap=350):
    return chunk_extracted_document({"blocks":[{"block_id":"b00000","kind":"markdown","text":text,"breadcrumb":[],"locator":"text:all"}]},max_chars,overlap)
