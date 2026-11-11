from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import zipfile

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from PIL import Image
except Exception:
    Image = None

try:
    from docx import Document as DocxDocument
    from docx.text.paragraph import Paragraph
    from docx.oxml.text.paragraph import CT_P
except Exception:
    DocxDocument = Paragraph = CT_P = None

try:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except Exception:
    Presentation = MSO_SHAPE_TYPE = None

try:
    from openpyxl import load_workbook
except Exception:
    load_workbook = None


@dataclass
class VisualAsset:
    asset_id: str
    source_locator: str
    breadcrumb: list[str]
    path: str
    kind: str
    mime_type: str
    sha256: str
    metadata: dict[str, Any]
    ocr_recommended: bool = True
    vision_recommended: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisualAnalysis:
    asset: VisualAsset
    ocr_text: str = ""
    ocr_provider: str | None = None
    vision_text: str = ""
    vision_provider: str | None = None
    warnings: list[str] | None = None


VISUAL_PROMPT = """你是企业知识库的视觉证据解析器。请完整理解图片，而不是只做 OCR。
把图片转换成适合 RAG 检索的中文 Markdown 证据，尽量保留原始语义和关系。

必须关注：
1. 图片/页面类型：照片、流程图、组织架构图、战略地图、表格、图表、时间线、矩阵、示意图、扫描文档等。
2. 所有可读文字、标题、标签、数字、单位、图例。
3. 空间与结构关系：层级、分组、箭头方向、先后顺序、分支条件、输入输出、上下游关系。
4. 图表：指标名称、系列、趋势、极值、对比关系；不要凭像素猜精确数字，看不清就明确说明。
5. 流程图：按顺序写出节点和箭头关系；有条件分支时写明条件。
6. 组织/框架图：写出父子层级、左右/上下分组和模块关系。
7. 图片若只是装饰性照片，要说明主体和与当前文档可能相关的可见信息，不要编造背景事实。
8. 对模糊、遮挡、无法确认的内容明确标为“不确定/不可辨认”。

输出格式：
## 视觉内容类型
...
## 可见文本与标签
...
## 结构/关系/流程
...
## 数据与关键事实
...
## 视觉语义摘要
...
## 不确定项
...
"""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_asset(asset_dir: Path, filename: str, data: bytes) -> tuple[Path, str]:
    digest = _sha256_bytes(data)
    ext = Path(filename).suffix.lower() or ".bin"
    out = asset_dir / f"{digest[:16]}{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_bytes(data)
    return out, digest


def _image_size(path: Path) -> tuple[int, int] | None:
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def _is_large_enough(path: Path, cfg: dict) -> bool:
    size = _image_size(path)
    if not size:
        return True
    w, h = size
    return w >= int(cfg.get("min_image_width", 120)) and h >= int(cfg.get("min_image_height", 80))


def _office_to_pdf(source: Path, out_dir: Path, timeout: int = 90) -> Path | None:
    exe = shutil.which("libreoffice") or shutil.which("soffice")
    if not exe:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(source)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except Exception:
        return None
    candidate = out_dir / f"{source.stem}.pdf"
    return candidate if candidate.exists() else None


def _render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int, page_indices: list[int] | None = None) -> list[tuple[int, Path]]:
    if fitz is None:
        return []
    result: list[tuple[int, Path]] = []
    doc = fitz.open(pdf_path)
    try:
        indices = page_indices if page_indices is not None else list(range(len(doc)))
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for index in indices:
            if index < 0 or index >= len(doc):
                continue
            page = doc[index]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out = out_dir / f"page-{index + 1:04d}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(out))
            result.append((index + 1, out))
    finally:
        doc.close()
    return result


def _pdf_visual_page_indices(path: Path, max_pages: int) -> list[int]:
    if fitz is None:
        return []
    indices: list[int] = []
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = (page.get_text("text") or "").strip()
            images = page.get_images(full=True)
            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []
            # Scans, images, charts/diagrams made from vector drawing primitives.
            if not text or images or len(drawings) >= 3:
                indices.append(i)
    finally:
        doc.close()
    return indices


def collect_visual_assets(source: Path, root: Path, cfg: dict, mode: str = "full") -> tuple[list[VisualAsset], list[str]]:
    """Materialize images/page renders that may contain retrievable visual evidence.

    Native text extraction remains separate. This stage creates durable image assets so OCR
    and VLM outputs can be cached and traced back to exact document locations.
    """
    mcfg = cfg.get("multimodal", {})
    render_cfg = mcfg.get("rendering", {})
    limits = mcfg.get("limits", {})
    assets_root = root / mcfg.get("assets_dir", "rag/assets")
    source_key = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:12]
    asset_dir = assets_root / source_key
    asset_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    assets: list[VisualAsset] = []
    max_assets = int(limits.get("max_visual_assets_per_document", 100))
    max_pages = int(limits.get("max_render_pages", 100))
    if mode == "hot":
        hot_cfg = mcfg.get("hot", {})
        max_assets = min(max_assets, int(hot_cfg.get("max_materialized_visual_assets", 24)))
        max_pages = min(max_pages, int(hot_cfg.get("max_render_pages", 12)))
    dpi = int(render_cfg.get("dpi", 160))

    def add(path: Path, locator: str, breadcrumb: list[str], kind: str, *, ocr=True, vision=True, **metadata):
        if len(assets) >= max_assets or not path.exists() or not _is_large_enough(path, limits):
            return
        data = path.read_bytes()
        saved, digest = _write_asset(asset_dir, path.name, data)
        mime = mimetypes.guess_type(saved.name)[0] or "application/octet-stream"
        assets.append(VisualAsset(
            asset_id=f"v{len(assets):05d}", source_locator=locator, breadcrumb=list(breadcrumb),
            path=saved.relative_to(root).as_posix(), kind=kind, mime_type=mime, sha256=digest,
            metadata=metadata, ocr_recommended=ocr, vision_recommended=vision,
        ))

    ext = source.suffix.lower()

    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        add(source, "image:file", [source.name], "standalone_image", ocr=True, vision=True)

    elif ext == ".pdf" and fitz is not None:
        indices = _pdf_visual_page_indices(source, max_pages)
        rendered = _render_pdf_pages(source, asset_dir / "rendered", dpi, indices)
        for page_no, img in rendered:
            # OCR is important on scan pages; harmlessly duplicates some native text on mixed pages.
            add(img, f"pdf:page:{page_no}", [f"PDF 第 {page_no} 页"], "pdf_visual_page",
                page=page_no, ocr=True, vision=True)

    elif ext == ".pptx":
        # Extract embedded pictures at their native resolution.
        if Presentation is not None:
            try:
                prs = Presentation(source)
                for si, slide in enumerate(prs.slides, 1):
                    title = ""
                    try:
                        if slide.shapes.title and slide.shapes.title.text:
                            title = slide.shapes.title.text.strip()
                    except Exception:
                        pass
                    crumb = [title or f"幻灯片 {si}"]
                    for sj, shape in enumerate(slide.shapes, 1):
                        if MSO_SHAPE_TYPE is not None and getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.PICTURE:
                            try:
                                image = shape.image
                                blob = image.blob
                                suffix = "." + (image.ext or "png")
                                tmp = asset_dir / f"pptx-slide-{si:04d}-picture-{sj:03d}{suffix}"
                                tmp.write_bytes(blob)
                                add(tmp, f"pptx:slide:{si}:shape:{sj}", crumb, "pptx_embedded_image",
                                    slide=si, shape=sj, shape_name=getattr(shape, "name", ""), ocr=True, vision=True)
                            except Exception as e:
                                warnings.append(f"PPT 第 {si} 页图片 {sj} 提取失败: {e}")
            except Exception as e:
                warnings.append(f"PPT 图片提取失败: {e}")
        # Render every slide when possible. This is what recovers arrows, grouping, SmartArt and layout.
        if bool(render_cfg.get("render_pptx_slides", True)):
            with tempfile.TemporaryDirectory() as td:
                pdf = _office_to_pdf(source, Path(td), int(render_cfg.get("office_timeout_seconds", 90)))
                if pdf:
                    for page_no, img in _render_pdf_pages(pdf, asset_dir / "slides", dpi, list(range(min(max_pages, 10_000)))):
                        add(img, f"pptx:slide:{page_no}", [f"幻灯片 {page_no}"], "pptx_slide_render",
                            slide=page_no, ocr=False, vision=True)
                else:
                    warnings.append("未发现 LibreOffice/soffice，无法把 PPT 整页渲染后识别 SmartArt、箭头和组合图形；内嵌图片仍会处理。")

    elif ext == ".docx":
        # Locate inline images at paragraph level whenever possible.
        if DocxDocument is not None:
            try:
                doc = DocxDocument(source)
                stack: list[tuple[int, str]] = []
                pi = 0
                for child in doc.element.body.iterchildren():
                    if CT_P is None or not isinstance(child, CT_P):
                        continue
                    pi += 1
                    p = Paragraph(child, doc)
                    text = p.text.strip()
                    style = (p.style.name or "") if p.style else ""
                    import re
                    m = re.search(r"heading\s*(\d+)", style, re.I)
                    if m and text:
                        level = max(1, min(6, int(m.group(1))))
                        while stack and stack[-1][0] >= level:
                            stack.pop()
                        stack.append((level, text))
                    try:
                        rids = child.xpath('.//a:blip/@r:embed')
                    except Exception:
                        rids = []
                    for ii, rid in enumerate(rids, 1):
                        try:
                            part = doc.part.related_parts[rid]
                            blob = part.blob
                            filename = Path(str(getattr(part, "partname", f"image-{pi}-{ii}.png"))).name
                            tmp = asset_dir / f"docx-p-{pi:05d}-image-{ii:02d}-{filename}"
                            tmp.write_bytes(blob)
                            add(tmp, f"docx:p:{pi}:image:{ii}", [x[1] for x in stack], "docx_embedded_image",
                                paragraph=pi, rel_id=rid, ocr=True, vision=True)
                        except Exception as e:
                            warnings.append(f"DOCX 段落 {pi} 图片提取失败: {e}")
            except Exception as e:
                warnings.append(f"DOCX 图片定位失败: {e}")
        if bool(render_cfg.get("render_docx_pages", True)):
            with tempfile.TemporaryDirectory() as td:
                pdf = _office_to_pdf(source, Path(td), int(render_cfg.get("office_timeout_seconds", 90)))
                if pdf:
                    pages = _render_pdf_pages(pdf, asset_dir / "pages", dpi, list(range(max_pages)))
                    for page_no, img in pages:
                        # Full-page vision catches drawing canvas / SmartArt not exposed by python-docx.
                        add(img, f"docx:render:page:{page_no}", [f"DOCX 渲染第 {page_no} 页"], "docx_page_render",
                            page=page_no, ocr=False, vision=True)
                else:
                    warnings.append("未发现 LibreOffice/soffice，DOCX 页面级视觉布局/SmartArt 无法渲染；内嵌图片仍会处理。")

    elif ext == ".xlsx":
        if load_workbook is not None:
            try:
                wb = load_workbook(source, read_only=False, data_only=False)
                for ws in wb.worksheets:
                    for ii, image in enumerate(getattr(ws, "_images", []) or [], 1):
                        try:
                            blob = image._data()
                            ext_guess = ".png"
                            tmp = asset_dir / f"xlsx-{ws.title}-image-{ii:03d}{ext_guess}"
                            tmp.write_bytes(blob)
                            anchor = getattr(image, "anchor", None)
                            add(tmp, f"xlsx:{ws.title}:image:{ii}", [ws.title, f"图片 {ii}"], "xlsx_embedded_image",
                                sheet=ws.title, image_index=ii, anchor=str(anchor), ocr=True, vision=True)
                        except Exception as e:
                            warnings.append(f"XLSX {ws.title} 图片 {ii} 提取失败: {e}")
                wb.close()
            except Exception as e:
                warnings.append(f"XLSX 图片提取失败: {e}")
        if bool(render_cfg.get("render_xlsx_pages", False)):
            with tempfile.TemporaryDirectory() as td:
                pdf = _office_to_pdf(source, Path(td), int(render_cfg.get("office_timeout_seconds", 90)))
                if pdf:
                    for page_no, img in _render_pdf_pages(pdf, asset_dir / "sheets", dpi, list(range(max_pages))):
                        add(img, f"xlsx:render:page:{page_no}", [f"XLSX 渲染第 {page_no} 页"], "xlsx_page_render",
                            page=page_no, ocr=False, vision=True)
                else:
                    warnings.append("未发现 LibreOffice/soffice，XLSX 工作表无法页面级渲染。")

    return assets, warnings


class PaddleOCRBackend:
    name = "paddleocr"

    def __init__(self, cfg: dict):
        from paddleocr import PaddleOCR
        kwargs: dict[str, Any] = {
            "lang": cfg.get("lang", "ch"),
            "use_doc_orientation_classify": bool(cfg.get("use_doc_orientation_classify", True)),
            "use_doc_unwarping": bool(cfg.get("use_doc_unwarping", True)),
            "use_textline_orientation": bool(cfg.get("use_textline_orientation", True)),
        }
        # Windows 兼容：paddlepaddle 3.3.x 在 CPU 上存在 PIR+oneDNN 崩溃（ConvertPirAttribute2RuntimeAttribute）。
        # 环境变量 FLAGS_use_mkldnn 无效（PaddleX 忽略之），必须经 enable_mkldnn=False 走标准 CPU 内核。
        kwargs["enable_mkldnn"] = bool(cfg.get("enable_mkldnn", False))
        if cfg.get("text_detection_model_name"):
            kwargs["text_detection_model_name"] = cfg["text_detection_model_name"]
        if cfg.get("text_recognition_model_name"):
            kwargs["text_recognition_model_name"] = cfg["text_recognition_model_name"]
        if cfg.get("engine"):
            kwargs["engine"] = cfg["engine"]
        if cfg.get("device"):
            kwargs["device"] = cfg["device"]
        self.model = PaddleOCR(**kwargs)
        self.min_score = float(cfg.get("min_score", 0.45))

    def recognize(self, image_path: Path) -> str:
        lines: list[str] = []
        for result in self.model.predict(str(image_path)):
            data = getattr(result, "json", None)
            if callable(data):
                data = data()
            if not isinstance(data, dict):
                continue
            body = data.get("res", data)
            raw_texts = body.get("rec_texts")
            raw_scores = body.get("rec_scores")
            raw_boxes = body.get("rec_boxes")
            texts = list(raw_texts) if raw_texts is not None else []
            scores = list(raw_scores) if raw_scores is not None else []
            boxes = list(raw_boxes) if raw_boxes is not None else []
            rows = []
            for i, text in enumerate(texts):
                text = str(text or "").strip()
                if not text:
                    continue
                score = float(scores[i]) if i < len(scores) else 1.0
                if score < self.min_score:
                    continue
                box = boxes[i] if i < len(boxes) else None
                try:
                    y = float(box[1])
                    x = float(box[0])
                except Exception:
                    y, x = float(i), 0.0
                rows.append((y, x, text))
            rows.sort(key=lambda t: (t[0], t[1]))
            lines.extend(t[2] for t in rows)
        return "\n".join(lines).strip()


class TesseractBackend:
    name = "tesseract"

    def __init__(self, cfg: dict):
        if not shutil.which("tesseract"):
            raise RuntimeError("tesseract executable not found")
        self.lang = cfg.get("tesseract_lang", "chi_sim+eng")
        self.psm = str(cfg.get("tesseract_psm", 6))

    def recognize(self, image_path: Path) -> str:
        cmd = ["tesseract", str(image_path), "stdout", "-l", self.lang, "--psm", self.psm]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode("utf-8", errors="replace")[-1000:])
        return p.stdout.decode("utf-8", errors="replace").strip()


class ModelAPIVisionBackend:
    """Remote multimodal model API using an OpenAI-compatible chat-completions schema.

    This backend is intentionally for background/full ingestion. Hot-document visual
    understanding is delegated to Hermes native Vision and never waits on this API.
    """
    name = "model-api"

    def __init__(self, cfg: dict):
        self.base_url = os.environ.get(cfg.get("base_url_env", "COMPANY_WIKI_VISION_BASE_URL"), cfg.get("base_url", "")).rstrip("/")
        self.model = os.environ.get(cfg.get("model_env", "COMPANY_WIKI_VISION_MODEL"), cfg.get("model", ""))
        self.api_key = os.environ.get(cfg.get("api_key_env", "COMPANY_WIKI_VISION_API_KEY"), cfg.get("api_key", ""))
        if not self.base_url:
            raise RuntimeError("vision model API base_url is not configured")
        if not self.model:
            raise RuntimeError("vision model API model is not configured")
        self.timeout = int(cfg.get("timeout_seconds", 90))
        self.max_tokens = int(cfg.get("max_tokens", 1400))
        self.prompt = cfg.get("prompt") or VISUAL_PROMPT
        if bool(cfg.get("probe_on_start", True)):
            req = urllib.request.Request(self.base_url + "/models", headers={"Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(req, timeout=float(cfg.get("probe_timeout_seconds", 1.5))) as resp:
                    if getattr(resp, "status", 200) >= 400:
                        raise RuntimeError(f"vision API probe returned {resp.status}")
            except Exception as e:
                raise RuntimeError(f"vision API unavailable at {self.base_url}: {e}") from e

    def analyze(self, image_path: Path, context: str = "") -> str:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"
        prompt = self.prompt
        if context:
            prompt += "\n\n文档上下文（仅用于帮助定位，不可替代图片事实）：\n" + context[:3000]
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            raise RuntimeError(f"vision HTTP {e.code}: {detail[-1200:]}") from e
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("vision response has no choices")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            content = "\n".join(parts)
        return str(content or "").strip()


# Backward-compatible import name for older deployments.
OpenAICompatibleVisionBackend = ModelAPIVisionBackend

class VisualEvidenceProcessor:
    """OCR + VLM enrichment with durable asset/cache storage.

    It intentionally keeps OCR and Vision separate:
    - OCR preserves visible text from scans/screenshots/images.
    - Vision preserves relationships, layout, arrows, chart meaning and other non-text semantics.
    Both become EvidenceBlocks and therefore enter rag/qmd/evidence and QMD's SQLite index.
    """

    def __init__(self, root: Path, config: dict, mode: str = "full"):
        self.root = root.resolve()
        self.config = config
        self.mode = mode
        self.mcfg = config.get("multimodal", {})
        self.enabled = bool(self.mcfg.get("enabled", True))
        self.cache_dir = self.root / self.mcfg.get("cache_dir", "rag/visual_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ocr = None
        self._vision = None
        self._ocr_error: str | None = None
        self._vision_error: str | None = None

    def _ocr_backend(self):
        if self._ocr is not None or self._ocr_error:
            return self._ocr
        cfg = self.mcfg.get("ocr", {})
        if not bool(cfg.get("enabled", True)):
            self._ocr_error = "disabled"
            return None
        provider = str(cfg.get("provider", "auto")).lower()
        errors = []
        if provider in {"auto", "paddleocr"}:
            try:
                self._ocr = PaddleOCRBackend(cfg)
                return self._ocr
            except Exception as e:
                errors.append(f"PaddleOCR: {e}")
                if provider == "paddleocr":
                    self._ocr_error = "; ".join(errors)
                    return None
        if provider in {"auto", "tesseract"}:
            try:
                self._ocr = TesseractBackend(cfg)
                return self._ocr
            except Exception as e:
                errors.append(f"Tesseract: {e}")
        self._ocr_error = "; ".join(errors) or "no OCR backend"
        return None

    def _vision_backend(self):
        if self._vision is not None or self._vision_error:
            return self._vision
        cfg = self.mcfg.get("vision", {})
        if not bool(cfg.get("enabled", True)):
            self._vision_error = "disabled"
            return None
        provider = str(cfg.get("provider", "model_api")).lower()
        try:
            if provider in {"model_api", "model-api", "openai_compatible", "openai-compatible"}:
                self._vision = ModelAPIVisionBackend(cfg)
                return self._vision
            self._vision_error = f"unsupported vision provider: {provider}"
        except Exception as e:
            self._vision_error = str(e)
        return None

    def _cache_key(self, asset: VisualAsset) -> Path:
        ocr_cfg = self.mcfg.get("ocr", {})
        vision_cfg = self.mcfg.get("vision", {})
        identity = json.dumps({
            "asset_sha256": asset.sha256,
            "ocr": {k: ocr_cfg.get(k) for k in ("provider", "lang", "min_score", "engine", "device")},
            "vision": {k: vision_cfg.get(k) for k in ("provider", "model", "max_tokens")},
        }, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def analyze_asset(self, asset: VisualAsset, context: str = "") -> VisualAnalysis:
        cache = self._cache_key(asset)
        cached: dict[str, Any] = {}
        if cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                cached = {}
        warnings: list[str] = []
        image = self.root / asset.path
        ocr_text = str(cached.get("ocr_text") or "")
        ocr_provider = cached.get("ocr_provider")
        vision_text = str(cached.get("vision_text") or "")
        vision_provider = cached.get("vision_provider")

        # Partial caches are intentionally retried. If OCR worked yesterday while the VLM
        # was offline, bringing the VLM online later enriches the same asset without forcing
        # a full cache purge.
        if asset.ocr_recommended and not ocr_text:
            ocr = self._ocr_backend()
            if ocr:
                try:
                    ocr_text = ocr.recognize(image)
                    ocr_provider = ocr.name
                except Exception as e:
                    warnings.append(f"OCR 失败: {e}")
            else:
                warnings.append(f"OCR 不可用: {self._ocr_error}")
        if asset.vision_recommended and not vision_text:
            vision = self._vision_backend()
            if vision:
                try:
                    vision_text = vision.analyze(image, context=context)
                    vision_provider = vision.name
                except Exception as e:
                    warnings.append(f"Vision 失败: {e}")
            else:
                warnings.append(f"Vision 不可用: {self._vision_error}")

        # Cache successful/partial successful analysis only. Pure infrastructure failures
        # must not become sticky: a later run should retry after OCR/VLM services are installed.
        if ocr_text or vision_text:
            data = {
                "asset": asset.to_dict(), "ocr_text": ocr_text, "ocr_provider": ocr_provider,
                "vision_text": vision_text, "vision_provider": vision_provider, "warnings": warnings,
            }
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cache)
        return VisualAnalysis(asset=asset, ocr_text=ocr_text, ocr_provider=ocr_provider,
                              vision_text=vision_text, vision_provider=vision_provider, warnings=warnings)

    def enrich(self, doc: dict, source: Path) -> dict:
        if not self.enabled:
            return doc
        assets, asset_warnings = collect_visual_assets(source, self.root, self.config, mode=self.mode)
        blocks = list(doc.get("blocks") or [])
        warnings = list(doc.get("warnings") or []) + asset_warnings
        analyses: list[VisualAnalysis] = []
        hot_cfg = self.mcfg.get("hot", {})
        hot_hermes_vision = self.mode == "hot" and str(hot_cfg.get("vision_strategy", "hermes_native")).lower() in {"hermes", "hermes_native", "hermes-native"}
        max_analysis = int(self.mcfg.get("limits", {}).get("max_analyzed_assets_per_document", 80))
        if self.mode == "hot":
            max_analysis = min(max_analysis, int(hot_cfg.get("max_eager_visual_assets", 8)))

        # Hot path never waits on OCR/Vision API when Hermes-native Vision is selected.
        # It only materializes page/slide/image assets with provenance; rag_ask.py then
        # returns the relevant assets for the Hermes Skill to inspect natively.
        if not hot_hermes_vision:
            for asset in assets[:max_analysis]:
                crumb = " > ".join(asset.breadcrumb)
                context = f"文件：{source.name}；位置：{asset.source_locator}；章节：{crumb}。"
                analyses.append(self.analyze_asset(asset, context))

        # Add OCR and semantic blocks. block_id is reassigned below for stable sequence.
        additions: list[dict[str, Any]] = []
        for a in analyses:
            common = {
                "visual_asset": a.asset.path,
                "visual_asset_sha256": a.asset.sha256,
                "visual_kind": a.asset.kind,
                "visual_source_locator": a.asset.source_locator,
            }
            if a.ocr_text:
                additions.append({
                    "block_id": "", "kind": "visual_ocr",
                    "text": "## OCR 识别文本\n\n" + a.ocr_text,
                    "breadcrumb": a.asset.breadcrumb + ["OCR"],
                    "locator": a.asset.source_locator + ":ocr",
                    "metadata": {**common, "ocr_provider": a.ocr_provider},
                })
            if a.vision_text:
                additions.append({
                    "block_id": "", "kind": "visual_semantics",
                    "text": "## 视觉语义解析\n\n" + a.vision_text,
                    "breadcrumb": a.asset.breadcrumb + ["视觉语义"],
                    "locator": a.asset.source_locator + ":vision",
                    "metadata": {**common, "vision_provider": a.vision_provider},
                })
            for w in a.warnings or []:
                warnings.append(f"{a.asset.source_locator}: {w}")

        # Insert additions after the last native block that belongs to the same page/slide/paragraph.
        def anchor(locator: str) -> str:
            for suffix in (":ocr", ":vision"):
                if locator.endswith(suffix):
                    locator = locator[:-len(suffix)]
            # Embedded image anchors are more specific than native blocks; reduce to parent location.
            if ":image:" in locator:
                return locator.split(":image:", 1)[0]
            if ":shape:" in locator:
                return locator.split(":shape:", 1)[0]
            return locator

        grouped: dict[str, list[dict[str, Any]]] = {}
        loose: list[dict[str, Any]] = []
        for b in additions:
            a = anchor(str(b.get("locator") or ""))
            if a:
                grouped.setdefault(a, []).append(b)
            else:
                loose.append(b)

        merged: list[dict[str, Any]] = []
        used: set[str] = set()
        for i, b in enumerate(blocks):
            merged.append(b)
            loc = str(b.get("locator") or "")
            # Append after last matching native block for each anchor.
            for a, vals in grouped.items():
                if a in used or not loc.startswith(a):
                    continue
                later_match = any(str(n.get("locator") or "").startswith(a) for n in blocks[i+1:])
                if not later_match:
                    merged.extend(vals); used.add(a)
        for a, vals in grouped.items():
            if a not in used:
                merged.extend(vals)
        merged.extend(loose)
        for i, b in enumerate(merged):
            b["block_id"] = f"b{i:05d}"

        if hot_hermes_vision:
            pending = len(assets)
            unresolved_vision = sum(1 for a in assets if a.vision_recommended)
            unresolved_ocr = sum(1 for a in assets if a.ocr_recommended)
        else:
            pending = max(0, len(assets) - len(analyses))
            unresolved_vision = pending + sum(1 for a in analyses if a.asset.vision_recommended and not a.vision_text)
            unresolved_ocr = pending + sum(1 for a in analyses if a.asset.ocr_recommended and not a.ocr_text)
        doc = dict(doc)
        doc["blocks"] = merged
        doc["warnings"] = list(dict.fromkeys(warnings))
        doc["needs_vision"] = unresolved_vision > 0
        doc["visual_assets"] = [a.to_dict() for a in assets]
        doc["visual_analysis"] = {
            "asset_count": len(assets),
            "analyzed_asset_count": len(analyses),
            "pending_asset_count": pending,
            "unresolved_vision_asset_count": unresolved_vision,
            "unresolved_ocr_asset_count": unresolved_ocr,
            "ocr_block_count": sum(1 for b in merged if b.get("kind") == "visual_ocr"),
            "vision_block_count": sum(1 for b in merged if b.get("kind") == "visual_semantics"),
            "ocr_backend": getattr(self._ocr, "name", None),
            "vision_backend": "hermes-native-deferred" if hot_hermes_vision else getattr(self._vision, "name", None),
            "strategy": "hermes-native-deferred" if hot_hermes_vision else "background-ocr-plus-model-api",
            "hermes_deferred_asset_count": len(assets) if hot_hermes_vision else 0,
        }
        return doc
