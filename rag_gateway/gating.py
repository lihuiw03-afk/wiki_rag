from __future__ import annotations
from dataclasses import dataclass, asdict

RECENT_DOC = ("这份","该份","这篇","该文档","这个文件","该文件","附件","刚上传","刚才上传","里面","原文","全文","报告中","访谈中","PPT中","ppt中","文档中","文件中","继续分析","继续说","再分析","还有什么")
CROSS_CORPUS = ("对比","比较","结合历史","结合以前","结合之前","以往","过去项目","其他项目","其他客户","知识库","公司已有","历次","跨文档")
INTERNAL = ("公司内部","客户","项目","访谈","咨询方案","企业文化","管理层","知识库","价值观","文化","银行","集团","调研")
_DOC_REF = ("那份","这份","该份","某份")
_ORG_NOUN = ("企业","公司","集团","银行","机构","单位","工厂","研究院","设计院","大学")
NO_RAG = ("润色","改写","翻译","写邮件","写一封","代码","编程","计算","改语气","起标题","写文案")
RETRIEVAL_QUALIFIERS = ("根据知识库","结合知识库","参考知识库","根据内部资料","结合内部资料","参考内部资料","根据项目资料","结合历史","引用公司","核对事实","查资料")

@dataclass
class RouteDecision:
    route: str
    reason: str
    confidence: float
    attachment: str | None = None
    def to_dict(self): return asdict(self)


def choose_route(query: str, lexicon_terms: list[str], attachment: str | None = None, has_recent_hot: bool = False, requested: str = "auto") -> RouteDecision:
    if requested in {"direct","company-rag","hot-document","hot+company"}:
        return RouteDecision(requested,"explicit-route",1.0,attachment)
    q=query.strip()
    if attachment:
        if any(x in q for x in CROSS_CORPUS): return RouteDecision("hot+company","current attachment plus cross-corpus comparison",0.99,attachment)
        return RouteDecision("hot-document","current/recent attachment is the primary source",0.99,attachment)
    if has_recent_hot and any(x in q for x in RECENT_DOC):
        if any(x in q for x in CROSS_CORPUS): return RouteDecision("hot+company","recent document plus historical/company comparison",0.96,None)
        return RouteDecision("hot-document","follow-up refers to the recently uploaded document",0.96,None)
    # Pure transformation tasks should not retrieve merely because the supplied text
    # happens to mention a company/entity. Explicit retrieval qualifiers override this.
    if any(x in q for x in NO_RAG) and not any(x in q for x in RETRIEVAL_QUALIFIERS):
        return RouteDecision("direct","general generation/transformation intent with no retrieval qualifier",0.92,None)
    # 间接指代（无附件）："那份/这份…" 指知识库文档；"某/那家/这家 + 组织名词" 指库内组织。
    # Regression cases: indirect references must not be treated as direct generation.
    if any(x in q for x in _DOC_REF):
        return RouteDecision("company-rag","indirect document reference (那份/这份/该份/某份)",0.90,None)
    if ("某" in q or "那家" in q or "这家" in q) and any(x in q for x in _ORG_NOUN):
        return RouteDecision("company-rag","indirect organization reference (某/那家/这家 + 组织名词)",0.88,None)
    matches=[t for t in lexicon_terms if len(str(t))>=2 and str(t) in q][:4]
    if matches: return RouteDecision("company-rag",f"matched internal lexicon: {', '.join(map(str,matches))}",0.94,None)
    if any(x in q for x in INTERNAL): return RouteDecision("company-rag","internal company/project knowledge intent",0.86,None)
    return RouteDecision("direct","no strong internal-knowledge or attachment signal",0.72,None)
