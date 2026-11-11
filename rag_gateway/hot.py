from __future__ import annotations
from pathlib import Path
import hashlib, json, re, time
from .evidence import extract_document, extracted_markdown, chunk_extracted_document, SUPPORTED
from .multimodal import VisualEvidenceProcessor


def _terms(text: str) -> set[str]:
    s = text.lower()
    out = set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", s))
    for seq in re.findall(r"[\u4e00-\u9fff]{2,}", s):
        out.add(seq)
        for n in (2, 3):
            if len(seq) >= n:
                out.update(seq[i:i+n] for i in range(len(seq)-n+1))
    return out


def _score(query: str, text: str, title: str = "") -> float:
    q = _terms(query); t = _terms(text + "\n" + title)
    if not q: return 0.0
    overlap = len(q & t) / max(1, len(q))
    exact = 0.20 if query.strip() and query.strip().lower() in (text + " " + title).lower() else 0.0
    title_bonus = 0.12 if any(x in title.lower() for x in q if len(x) >= 2) else 0.0
    return min(1.0, overlap * 0.68 + exact + title_bonus)


def _compact_locators(locators: list[str], limit: int = 4) -> str:
    vals = [str(x) for x in locators if x]
    if len(vals) <= limit:
        return ", ".join(vals)
    return ", ".join(vals[:2]) + f", …(+{len(vals)-3}), " + vals[-1]


SUMMARY_PATTERNS = ("总结", "概括", "主要讲", "整体", "全文", "全部内容", "核心内容", "大意", "这份访谈", "这份报告")


VISUAL_QUERY_PATTERNS = (
    "图片", "图里", "图中", "这张图", "截图", "扫描", "流程图", "架构图", "组织图", "战略图",
    "图表", "曲线", "柱状图", "饼图", "表格截图", "箭头", "关系图", "示意图", "页面", "幻灯片",
)

def _visual_anchor(locator: str) -> str:
    locator = str(locator or "")
    m = re.match(r"^(pdf:page:\d+|pptx:slide:\d+|docx:render:page:\d+|xlsx:render:page:\d+)", locator)
    if m:
        return m.group(1)
    if ":image:" in locator:
        return locator.split(":image:", 1)[0]
    if ":shape:" in locator:
        return locator.split(":shape:", 1)[0]
    return locator

def _explicit_visual_numbers(query: str) -> set[int]:
    nums=set()
    for m in re.finditer(r"(?:第\s*)?(\d+)\s*(?:页|张|幻灯片|slide)", query, re.I):
        try: nums.add(int(m.group(1)))
        except Exception: pass
    return nums

def _choose_hermes_vision_assets(root: Path, query: str, meta: dict, selected: list[dict], max_assets: int) -> tuple[list[dict], list[str]]:
    assets=list(meta.get("visual_assets") or [])
    if not assets or max_assets <= 0:
        return [], []
    reasons=[]
    explicit=_explicit_visual_numbers(query)
    visual_intent=any(x in query for x in VISUAL_QUERY_PATTERNS)
    selected_anchors=set()
    selected_has_visual_marker=False
    for h in selected:
        for loc in h.get("locators") or []:
            selected_anchors.add(_visual_anchor(str(loc)))
        kinds=set(h.get("kinds") or [])
        if kinds & {"visual_marker", "scan_marker"}:
            selected_has_visual_marker=True
    native_chars=int(meta.get("native_text_chars") or 0)
    if explicit: reasons.append("query-explicit-page-or-slide")
    if visual_intent: reasons.append("query-visual-intent")
    if selected_has_visual_marker: reasons.append("retrieved-chunk-contains-visual-gap")
    if bool(meta.get("needs_vision")) and native_chars < 400: reasons.append("document-mostly-visual-or-scan")
    broad_summary=any(x in query for x in SUMMARY_PATTERNS)
    if bool(meta.get("needs_vision")) and broad_summary: reasons.append("broad-summary-with-visual-content")
    required=bool(reasons)
    if not required:
        return [], []

    qterms=_terms(query)
    ranked=[]
    for pos,a in enumerate(assets):
        loc=str(a.get("source_locator") or "")
        anchor=_visual_anchor(loc)
        md=a.get("metadata") or {}
        number=md.get("page") or md.get("slide")
        score=0.0
        if explicit and isinstance(number, int) and number in explicit: score += 10.0
        if anchor and anchor in selected_anchors: score += 5.0
        crumb=" ".join(a.get("breadcrumb") or [])
        ats=_terms(crumb + " " + loc)
        if qterms: score += min(2.0, len(qterms & ats) * 0.3)
        if a.get("kind") == "standalone_image": score += 4.0
        if visual_intent: score += 0.5
        ranked.append((score, -pos, a))
    ranked.sort(reverse=True, key=lambda x:(x[0],x[1]))
    # For whole-document summaries without an explicit page target, sample visual
    # assets across the document instead of inspecting only the first few pages.
    if broad_summary and not explicit and not selected_anchors and len(ranked) > max_assets:
        ordered=[x[2] for x in sorted(ranked, key=lambda x:-x[1])]
        positions=sorted(set(round(i*(len(ordered)-1)/(max_assets-1)) for i in range(max_assets))) if max_assets > 1 else [0]
        chosen=[ordered[i] for i in positions]
    else:
        chosen=[a for _,_,a in ranked[:max_assets]]
    public=[]
    for a in chosen:
        rel=str(a.get("path") or "")
        ap=(root/rel).resolve() if rel else None
        public.append({
            "asset_id": a.get("asset_id"),
            "path": str(ap) if ap else "",
            "relative_path": rel,
            "source_locator": a.get("source_locator"),
            "breadcrumb": a.get("breadcrumb") or [],
            "kind": a.get("kind"),
            "mime_type": a.get("mime_type"),
            "metadata": a.get("metadata") or {},
        })
    return public, reasons


class HotDocumentStore:
    def __init__(self, root: Path, config: dict):
        self.root = root.resolve(); self.config = config
        hcfg = config.get("hot_document", {})
        self.hot_dir = self.root / hcfg.get("dir", "rag/hot")
        self.registry_path = self.hot_dir / "registry.json"
        self.hot_dir.mkdir(parents=True, exist_ok=True)
        self.visual_processor = VisualEvidenceProcessor(self.root, config, mode="hot")

    def _registry(self) -> dict:
        if not self.registry_path.exists(): return {"version": 1, "documents": {}}
        try: return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception: return {"version": 1, "documents": {}}

    def _save_registry(self, reg: dict):
        tmp = self.registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.registry_path)

    @staticmethod
    def _doc_id(raw_path: str, sha256: str) -> str:
        return hashlib.sha1((raw_path + "\0" + sha256).encode("utf-8")).hexdigest()[:16]

    def ingest(self, path: Path, profile: str | None = None) -> dict:
        path = path.resolve()
        if path.suffix.lower() not in SUPPORTED:
            return {"status": "unsupported", "path": str(path), "extension": path.suffix.lower()}
        doc = extract_document(path, visual_processor=self.visual_processor)
        sha = doc["sha256"]
        try: raw_rel = path.relative_to(self.root).as_posix()
        except ValueError: raw_rel = str(path)
        doc_id = self._doc_id(raw_rel, sha)
        d = self.hot_dir / doc_id; chunks_dir = d / "chunks"
        d.mkdir(parents=True, exist_ok=True); chunks_dir.mkdir(parents=True, exist_ok=True)
        normalized = extracted_markdown(doc)
        (d / "source.md").write_text(normalized, encoding="utf-8")
        hcfg = self.config.get("hot_document", {})
        chunks = chunk_extracted_document(doc, int(hcfg.get("chunk_chars", 3600)), int(hcfg.get("overlap_chars", 400)))
        for old in chunks_dir.glob("*.md"): old.unlink()
        chunk_meta = []
        for i, c in enumerate(chunks):
            p = chunks_dir / f"{i:04d}.md"
            crumb = " > ".join(c.get("breadcrumb") or []) or f"片段 {i+1}"
            p.write_text(f"# {path.name} · {crumb}\n\n{c['text'].strip()}\n", encoding="utf-8")
            chunk_meta.append({"index": i, "path": p.relative_to(self.hot_dir).as_posix(), "breadcrumb": c.get("breadcrumb") or [],
                               "locators": c.get("locators") or [], "block_ids": c.get("block_ids") or [], "kinds": c.get("kinds") or [], "chars": len(c.get("text") or "")})
        meta = {"doc_id": doc_id, "raw_path": raw_rel, "absolute_path": str(path), "filename": path.name, "sha256": sha,
                "profile": profile, "ingested_at": time.time(), "normalized_chars": len(normalized), "chunks": chunk_meta,
                "warnings": doc.get("warnings") or [], "needs_vision": bool(doc.get("needs_vision")), "block_count": len(doc.get("blocks") or []),
                "native_text_chars": sum(len(str(b.get("text") or "")) for b in (doc.get("blocks") or []) if b.get("kind") not in {"visual_marker","scan_marker","visual_ocr","visual_semantics","heading","page_heading","slide_heading","sheet_heading"}),
                "visual_analysis": doc.get("visual_analysis") or {}, "visual_asset_count": len(doc.get("visual_assets") or []),
                "visual_assets": doc.get("visual_assets") or []}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        reg = self._registry(); reg["documents"][doc_id] = meta; self._save_registry(reg)
        return {"status": "ready", **{k: meta[k] for k in ("doc_id","raw_path","filename","normalized_chars","needs_vision","block_count","visual_asset_count","visual_analysis")}, "chunk_count": len(chunks), "warnings": meta["warnings"]}

    def resolve(self, attachment: str | None = None, profile: str | None = None, max_age_seconds: int | None = None) -> dict | None:
        reg = self._registry(); docs = list(reg.get("documents", {}).values())
        if attachment:
            ap = str(Path(attachment).resolve())
            matches = [d for d in docs if d.get("absolute_path") == ap or d.get("raw_path") == attachment]
            if matches: return max(matches, key=lambda x: x.get("ingested_at",0))
            p = Path(attachment)
            if p.exists():
                self.ingest(p, profile=profile)
                return self.resolve(attachment, profile, max_age_seconds)
            return None
        if profile:
            docs = [d for d in docs if d.get("profile") == profile]
        if max_age_seconds is not None:
            cutoff = time.time() - max_age_seconds; docs = [d for d in docs if d.get("ingested_at",0) >= cutoff]
        return max(docs, key=lambda x: x.get("ingested_at",0)) if docs else None

    def search(self, query: str, attachment: str | None = None, profile: str | None = None) -> dict:
        hcfg = self.config.get("hot_document", {})
        meta = self.resolve(attachment, profile, int(hcfg.get("recent_ttl_seconds", 7200)))
        if not meta:
            return {"route": "hot-document", "status": "not-found", "context": "", "selected": [], "requires_vision": False}
        d = self.hot_dir / meta["doc_id"]
        full = (d / "source.md").read_text(encoding="utf-8", errors="replace")
        max_full = int(hcfg.get("full_document_chars", 42000))
        max_context = int(hcfg.get("max_context_chars", 36000))
        selected = []
        if len(full) <= max_full:
            selected = [{"index": None, "score": 1.0, "breadcrumb": ["全文"], "locators": [], "kinds": [], "text": full[:max_context], "reason": "hot-full-document"}]
        else:
            chunks=[]
            for cm in meta.get("chunks",[]):
                text=(self.hot_dir / cm["path"]).read_text(encoding="utf-8", errors="replace")
                title=" > ".join(cm.get("breadcrumb") or [])
                chunks.append({**cm,"text":text,"score":_score(query,text,title)})
            max_hits=int(hcfg.get("max_hits",10)); total_chars=sum(len(c["text"]) for c in chunks)
            # A newly uploaded single document has no cross-source noise. If all of its
            # structural chunks fit the hot context budget, use all of them in source
            # order: this maximizes recall and avoids unnecessary retrieval loss.
            if total_chars <= max_context:
                chosen=sorted(chunks,key=lambda x:x["index"])
            elif any(x in query for x in SUMMARY_PATTERNS):
                # Broad summary questions should cover the whole document, not only
                # lexical hotspots. Sample across the source while respecting budget.
                if len(chunks) <= max_hits: chosen=chunks
                else:
                    positions=sorted(set(round(i*(len(chunks)-1)/(max_hits-1)) for i in range(max_hits)))
                    chosen=[chunks[i] for i in positions]
            else:
                ranked=sorted(chunks,key=lambda x:x["score"], reverse=True)
                seed_limit=int(hcfg.get("seed_hits",6)); radius=int(hcfg.get("neighbor_radius",1)); indexes=[]
                for c in ranked[:seed_limit]:
                    if c["index"] not in indexes: indexes.append(c["index"])
                    for delta in range(1,radius+1):
                        for idx in (c["index"]-delta,c["index"]+delta):
                            if 0<=idx<len(chunks) and idx not in indexes:indexes.append(idx)
                by_index={c["index"]:c for c in chunks}; chosen=[by_index[i] for i in indexes if i in by_index]
                chosen.sort(key=lambda x:x["score"], reverse=True)
            selected=[];used=0
            for c in chosen[:max_hits]:
                if used>=max_context: break
                text=c["text"][:max_context-used];used+=len(text)
                selected.append({"index":c["index"],"score":c["score"],"breadcrumb":c.get("breadcrumb") or [],"locators":c.get("locators") or [],"kinds":c.get("kinds") or [],"text":text,"reason":"hot-structural-retrieval"})
        blocks=[]; public=[]
        for i,h in enumerate(selected,1):
            crumb=" > ".join(h.get("breadcrumb") or []) or "原始文档"
            loc=_compact_locators(h.get("locators") or [])
            blocks.append(f"[S{i}] score={h['score']:.3f} reason={h['reason']} type=hot-document\nsource={meta['raw_path']}\nlocation={crumb}"+(f" ({loc})" if loc else "")+f"\n\n{h['text']}")
            public.append({"citation":f"S{i}","source":meta["raw_path"],"chunk_index":h.get("index"),"breadcrumb":h.get("breadcrumb") or [],"locators":h.get("locators") or [],"kinds":h.get("kinds") or [],"score":h["score"],"reason":h["reason"]})
        hv_cfg=self.config.get("multimodal",{}).get("hot",{})
        hv_assets,hv_reasons=_choose_hermes_vision_assets(self.root,query,meta,selected,int(hv_cfg.get("max_hermes_vision_assets_per_query",4)))
        hermes_vision={
            "required": bool(hv_assets),
            "strategy": "hermes-native",
            "reasons": hv_reasons,
            "assets": hv_assets,
            "prompt": "请使用 Hermes 内置 Vision 查看这些视觉资产，并围绕当前用户问题提取：可见文字、表格/图表信息、流程箭头、层级/空间关系及不确定项。只描述图中可见或可推断自布局的内容，不补充外部事实。",
        }
        return {"route":"hot-document","status":"ready","document":{k:meta.get(k) for k in ("doc_id","raw_path","filename","sha256","needs_vision","warnings","normalized_chars","native_text_chars","visual_asset_count","visual_analysis")},
                "context":"\n\n---\n\n".join(blocks),"selected":public,"requires_vision":bool(hv_assets),"hermes_vision":hermes_vision,
                "answer_policy":"优先依据 [S#] 原始文档文本证据回答。若 hermes_vision.required=true，必须先用 Hermes 内置 Vision 查看返回的视觉资产，再把视觉观察与 [S#] 文本证据合并回答；Hot 路径禁止调用后台 Vision API。不要猜测未查看的图像内容。"}
