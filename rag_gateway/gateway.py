from __future__ import annotations
from pathlib import Path
import json, re, threading, time
from .config import load_config
from .gating import choose_route
from .hot import HotDocumentStore


def _shift_citations(context: str, offset: int) -> str:
    return re.sub(r"\[S(\d+)\]", lambda m: f"[S{int(m.group(1))+offset}]", context)


class CompanyKnowledgeGateway:
    """Unified Agent-facing gate: direct vs hot-document vs formal Company RAG."""
    def __init__(self, root: Path):
        self.root=root.resolve(); self.config=load_config(self.root)
        lex_path=self.root/self.config["project"].get("lexicon","rag/lexicon.json")
        try:self.lexicon=json.loads(lex_path.read_text(encoding="utf-8"))
        except Exception:self.lexicon={"terms":[]}
        self.hot=HotDocumentStore(self.root,self.config)
        self._local=threading.local()

    def _company(self):
        """One CompanyRAG per worker thread (manifest/lexicon loaded once).

        The sidecar reuses a fixed pool of threads; a shared instance would race on
        _last_rerank_error, so each thread keeps its own copy.
        """
        rag=getattr(self._local,"company",None)
        if rag is None:
            from .search import CompanyRAG
            rag=CompanyRAG(self.root)
            self._local.company=rag
        return rag

    def ask(self, query: str, profile: str|None=None, attachment: str|None=None, route: str="auto", mode: str="auto", context_policy: str="auto", hyde: str|None=None) -> dict:
        started=time.perf_counter()
        # The sidecar keeps thread-local CompanyRAG instances warm. Refresh their
        # manifest/lexicon before routing so newly compiled entity terms are visible
        # without a service restart. No CompanyRAG is created for direct/hot-only
        # clients that never touch the formal index.
        cached_company = getattr(self._local, "company", None)
        if cached_company is not None:
            cached_company.reload_if_stale()
            self.lexicon = cached_company.lexicon
        recent=self.hot.resolve(profile=profile,max_age_seconds=int(self.config.get("hot_document",{}).get("recent_ttl_seconds",7200)))
        decision=choose_route(query,self.lexicon.get("terms",[]),attachment=attachment,has_recent_hot=bool(recent),requested=route)
        if decision.route=="direct":
            return {"query":query,"route":"direct","route_decision":decision.to_dict(),"profile":profile,"elapsed_ms":round((time.perf_counter()-started)*1000,1),"context":"","selected":[],"answer_policy":"无需知识库检索；按普通 Hermes 能力回答。"}
        if decision.route=="hot-document":
            hot=self.hot.search(query,attachment=attachment,profile=profile)
            hot.update({"query":query,"route_decision":decision.to_dict(),"profile":profile,"elapsed_ms":round((time.perf_counter()-started)*1000,1)})
            return hot
        # Lazy import keeps hot/direct routing independent from QMD availability.
        company_rag = self._company()
        company_rag.reload_if_stale()
        self.lexicon = company_rag.lexicon
        company=company_rag.search(query,mode=mode,profile=profile,hyde=hyde,context_policy=context_policy)
        if decision.route=="company-rag":
            company["route"]="company-rag"; company["route_decision"]=decision.to_dict(); return company
        hot=self.hot.search(query,attachment=attachment,profile=profile)
        hot_count=len(hot.get("selected",[])); company_context=_shift_citations(company.get("context",""),hot_count)
        selected=list(hot.get("selected",[]))
        for i,item in enumerate(company.get("selected",[]),hot_count+1):
            x=dict(item); x["citation"]=f"S{i}"; selected.append(x)
        return {"query":query,"route":"hot+company","route_decision":decision.to_dict(),"profile":profile,
                "mode":company.get("mode"),"context_policy":company.get("context_policy"),
                "elapsed_ms":round((time.perf_counter()-started)*1000,1),
                "context":"\n\n--- CURRENT UPLOAD ---\n\n"+hot.get("context","")+"\n\n--- COMPANY KNOWLEDGE ---\n\n"+company_context,
                "selected":selected,"requires_vision":hot.get("requires_vision",False),"hermes_vision":hot.get("hermes_vision") or {"required":False,"strategy":"hermes-native","assets":[]},
                "answer_policy":"同时依据当前上传原文与历史 Company Wiki 证据回答；若 hermes_vision.required=true，先使用 Hermes 内置 Vision 查看指定视觉资产；当前文件事实优先引用其 [S#]，视觉观察需明确来自当前附件；发生冲突时显式说明来源和时间。"}
