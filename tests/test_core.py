from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "rag" / "manifest.json"
requires_manifest = unittest.skipUnless(
    MANIFEST.exists(), "requires local rag/manifest.json"
)

from rag_gateway.frontmatter import parse_frontmatter_text
from rag_gateway.markdown_tree import parse_markdown_tree
from rag_gateway.router import choose_mode, choose_context_policy, build_typed_searches
from rag_gateway.search import CompanyRAG
from rag_gateway.qmd_client import QMDHit


class FakeQMD:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        collection = (kwargs.get("collections") or [""])[0]
        return list(self.responses.get(collection, []))


class TestCore(unittest.TestCase):
    def test_frontmatter(self):
        meta, body = parse_frontmatter_text('---\ntitle: 测试\ntype: entity\n---\n# 测试\n正文')
        self.assertEqual(meta['title'], '测试')
        self.assertIn('正文', body)

    def test_chapter_tree(self):
        md = '# A\nintro\n## B\nb\n### C\nc\n## D\nd\n'
        nodes = parse_markdown_tree(md, 'wiki/entities/a.md', 'A')
        self.assertEqual(len(nodes), 4)
        by_title = {n.title: n for n in nodes}
        self.assertEqual(by_title['B'].parent_id, by_title['A'].node_id)
        self.assertEqual(by_title['C'].parent_id, by_title['B'].node_id)
        self.assertEqual(by_title['D'].parent_id, by_title['A'].node_id)

    def test_router(self):
        self.assertEqual(choose_mode('示例公司A是什么'), 'fast')
        self.assertEqual(choose_mode('综合比较示例公司A和示例制造的企业文化差异'), 'deep')
        self.assertEqual(choose_mode('示例公司A有哪些文化特点'), 'balanced')

    def test_context_policy(self):
        self.assertEqual(choose_context_policy('综合不同管理层访谈的共同观点'), 'diversity')
        self.assertEqual(choose_context_policy('详细总结这份资料中提到的全部问题'), 'source-focused')
        self.assertEqual(choose_context_policy('示例公司A有哪些文化特点'), 'balanced')

    def test_typed_queries(self):
        q = build_typed_searches('示例公司A文化', True, ['示例公司A', '文化通俗化'], None)
        self.assertEqual(q[0]['type'], 'lex')
        self.assertEqual(q[1]['type'], 'vec')
        self.assertIn('示例公司A', q[0]['query'])

    @requires_manifest
    def test_manifest_integrity(self):
        manifest = json.loads((ROOT / 'rag/manifest.json').read_text(encoding='utf-8'))
        self.assertGreater(len(manifest['nodes']), 100)
        for page, nid in manifest['page_roots'].items():
            self.assertIn(nid, manifest['nodes'])

    @requires_manifest
    def test_context_source_focused_allows_many_same_source(self):
        rag = CompanyRAG(ROOT)
        raw, ids = next((r, ids) for r, ids in rag.manifest['raw_chunks'].items() if len(ids) >= 6)
        hits = [rag._to_hit(nid, 0.95 - i * 0.03, 'qmd') for i, nid in enumerate(ids[:6])]
        _, selected_source, stats_source = rag._compose_context(hits, 'source-focused')
        _, selected_div, stats_div = rag._compose_context(hits, 'diversity')
        same_source_count = sum(
            1 for h in selected_source
            if rag.manifest['nodes'][h.node_id].get('raw_path') == raw
        )
        div_count = sum(
            1 for h in selected_div
            if rag.manifest['nodes'][h.node_id].get('raw_path') == raw
        )
        self.assertGreaterEqual(same_source_count, 5)
        self.assertLessEqual(div_count, 2)
        sf_cfg = rag.config['context']['policies']['source-focused']['max_hits_per_raw_source']
        div_cfg = rag.config['context']['policies']['diversity']['max_hits_per_raw_source']
        self.assertEqual(stats_source['max_hits_per_raw_source'], sf_cfg)
        self.assertEqual(stats_div['max_hits_per_raw_source'], div_cfg)

    @requires_manifest
    def test_context_stats_truncated_flag(self):
        rag = CompanyRAG(ROOT)
        raw, ids = next((r, ids) for r, ids in rag.manifest['raw_chunks'].items() if len(ids) >= 3)
        hits = [rag._to_hit(nid, 0.95 - i * 0.03, 'qmd') for i, nid in enumerate(ids[:4])]
        # diversity: no neighbor expansion -> deterministic selection; a full budget
        # must NOT be flagged as truncated
        _, final_ok, stats_ok = rag._compose_context(hits, 'diversity')
        self.assertFalse(stats_ok['truncated'])
        self.assertEqual(stats_ok['dropped_hits'], 0)
        self.assertEqual(stats_ok['selected_hits'], len(final_ok))
        # Shrink the budget by hand -> edge cut AND dropped blocks must both be flagged
        policy = rag.config['context']['policies']['diversity']
        original = policy['max_chars']
        try:
            policy['max_chars'] = 1200
            ctx, final_small, stats_small = rag._compose_context(hits, 'diversity')
        finally:
            policy['max_chars'] = original
        self.assertTrue(stats_small['truncated'])
        self.assertGreaterEqual(stats_small['dropped_hits'], 1)
        self.assertIn('[截断]', ctx)

    @requires_manifest
    def test_graph_expansion_selects_relevant_section_not_root(self):
        rag = CompanyRAG(ROOT)
        seed_page = target_page = None
        for page, targets in rag.graph.get('edges', {}).items():
            for target in targets:
                nodes = rag.manifest.get('page_nodes', {}).get(target) or []
                if len(nodes) >= 2 and page in rag.manifest.get('page_roots', {}):
                    seed_page, target_page = page, target
                    break
            if seed_page:
                break
        self.assertIsNotNone(seed_page)
        seed_id = rag.manifest['page_roots'][seed_page]
        target_nodes = rag.manifest['page_nodes'][target_page]
        target_id = next((nid for nid in target_nodes if nid != rag.manifest['page_roots'].get(target_page)), target_nodes[-1])
        target_node = rag.manifest['nodes'][target_id]
        fake_uri = f"qmd://company-core/{target_node['projection']}"
        rag.qmd = FakeQMD({'company-core': [QMDHit(file=fake_uri, title=target_node['title'], score=0.96)]})
        hits = [rag._to_hit(seed_id, 0.92, 'qmd')]
        searches = build_typed_searches('相关问题', True, [], None)
        augmented = rag._augment(hits, 'balanced', '相关问题', searches)
        graph_hits = [h for h in augmented if h.reason == 'wiki-graph-section' and h.node_id == target_id]
        self.assertTrue(graph_hits)
        self.assertNotEqual(target_id, rag.manifest['page_roots'].get(target_page))

    @requires_manifest
    def test_source_support_uses_restricted_best_chunk_not_first(self):
        rag = CompanyRAG(ROOT)
        rag.graph = {'edges': {}, 'incoming': {}}
        chosen = None
        for nid, n in rag.manifest['nodes'].items():
            if n.get('collection') != 'company-core':
                continue
            for logical in n.get('raw_sources') or []:
                actual = rag.manifest.get('raw_alias_to_actual', {}).get(logical, logical)
                ids = rag.manifest.get('raw_chunks', {}).get(actual) or []
                if len(ids) >= 3:
                    chosen = nid, logical, actual, ids
                    break
            if chosen:
                break
        self.assertIsNotNone(chosen)
        core_id, _, _, ids = chosen
        target_id = ids[2]
        target_node = rag.manifest['nodes'][target_id]
        fake_uri = f"qmd://company-evidence/{target_node['projection']}"
        rag.qmd = FakeQMD({'company-evidence': [QMDHit(file=fake_uri, title=target_node['title'], score=0.94)]})
        hits = [rag._to_hit(core_id, 0.91, 'qmd')]
        searches = build_typed_searches('具体证据', True, [], None)
        augmented = rag._augment(hits, 'balanced', '具体证据', searches)
        support = [h for h in augmented if h.reason == 'source-evidence-restricted']
        self.assertTrue(support)
        self.assertEqual(support[0].node_id, target_id)
        self.assertNotEqual(support[0].node_id, ids[0])


if __name__ == '__main__':
    unittest.main()

class TestV22(unittest.TestCase):
    def test_retrieval_gate(self):
        from rag_gateway.gating import choose_route
        self.assertEqual(choose_route('这份文件主要讲什么', [], attachment='/tmp/a.docx').route, 'hot-document')
        self.assertEqual(choose_route('把这份文件和以前项目对比', [], attachment='/tmp/a.docx').route, 'hot+company')
        self.assertEqual(choose_route('示例公司A有哪些文化问题', ['示例公司A']).route, 'company-rag')
        self.assertEqual(choose_route('帮我润色这句话', []).route, 'direct')
        # route regression: indirect reference without attachment -> company-rag
        self.assertEqual(choose_route('那家以示例名称命名的银行，在页面上对应的英文标识是什么？', []).route, 'company-rag')
        self.assertEqual(choose_route('那份由示例顾问提出的示例评估模型，示例维度如何协同是什么意思？', []).route, 'company-rag')
        self.assertEqual(choose_route('某示例企业那份文化建设采购文件里，预算上限是多少？', []).route, 'company-rag')
        self.assertEqual(choose_route('那家做示例零部件的制造企业目前面临哪些经营短板？', []).route, 'company-rag')
        self.assertEqual(choose_route('这份文件主要讲什么', []).route, 'company-rag')

    def test_lossless_structural_chunking(self):
        from rag_gateway.evidence import chunk_extracted_document
        doc = {'blocks': [
            {'block_id': 'b1', 'kind': 'paragraph', 'text': '第一段唯一内容ABC。', 'breadcrumb': ['A'], 'locator': 'p1'},
            {'block_id': 'b2', 'kind': 'table', 'text': '|字段|值|\n|---|---|\n|X|唯一表格XYZ|', 'breadcrumb': ['A','表格'], 'locator': 't1'},
            {'block_id': 'b3', 'kind': 'paragraph', 'text': '第三段唯一内容DEF。', 'breadcrumb': ['B'], 'locator': 'p3'},
        ]}
        chunks = chunk_extracted_document(doc, max_chars=40, overlap=8)
        joined = '\n'.join(c['text'] for c in chunks)
        self.assertIn('第一段唯一内容ABC', joined)
        self.assertIn('唯一表格XYZ', joined)
        self.assertIn('第三段唯一内容DEF', joined)
        self.assertTrue(any('t1' in c.get('locators', []) for c in chunks))

    @requires_manifest
    def test_projection_has_extracted_provenance(self):
        manifest = json.loads((ROOT / 'rag/manifest.json').read_text(encoding='utf-8'))
        self.assertGreater(len(manifest.get('raw_extracted', {})), 0)
        sample = next(iter(manifest['raw_extracted'].values()))
        self.assertIn('path', sample)
        self.assertTrue((ROOT / 'rag/extracted' / sample['path']).exists())
        evid = next(n for n in manifest['nodes'].values() if n.get('collection') == 'company-evidence')
        self.assertIn('source_locators', evid)


class TestV22Hot(unittest.TestCase):
    def test_entity_mention_does_not_force_rag_for_pure_rewrite(self):
        from rag_gateway.gating import choose_route
        d = choose_route('帮我润色这段示例公司A项目介绍', ['示例公司A'])
        self.assertEqual(d.route, 'direct')

    def test_hot_document_small_text_uses_full_context(self):
        import tempfile
        from rag_gateway.hot import HotDocumentStore
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / 'raw'; raw.mkdir()
            f = raw / 'a.txt'; f.write_text('第一条：示例要求一。\n第二条：示例要求二。', encoding='utf-8')
            cfg = {'hot_document': {'dir': 'rag/hot', 'full_document_chars': 42000, 'max_context_chars': 36000,
                                    'chunk_chars': 3600, 'overlap_chars': 400, 'seed_hits': 6,
                                    'neighbor_radius': 1, 'max_hits': 10, 'recent_ttl_seconds': 7200}}
            store = HotDocumentStore(root, cfg)
            store.ingest(f, profile='u')
            r = store.search('这份文件有什么要求', attachment=str(f), profile='u')
            self.assertEqual(r['status'], 'ready')
            self.assertEqual(r['selected'][0]['reason'], 'hot-full-document')
            self.assertIn('示例要求一', r['context'])
            self.assertIn('示例要求二', r['context'])

class TestV24EntityLegs(unittest.TestCase):
    def test_entity_mentions_and_aux_legs(self):
        from rag_gateway.router import build_typed_searches, entity_mentions
        names = ["示例公司A", "示例制造集团", "示例重工", "示例智造"]
        q = "示例公司A和示例制造在示例升级项目上的做法有什么异同？"
        m = entity_mentions(q, names)
        self.assertIn("示例公司A", m)
        self.assertIn("示例制造集团", m)  # "示例制造" 前缀命中
        s = build_typed_searches(q, True, ["企业文化升级"], entity_names=names)
        vec_queries = [x["query"] for x in s if x["type"] == "vec"]
        self.assertIn("示例公司A", vec_queries)
        self.assertIn("示例制造集团", vec_queries)
        # 单实体不追加
        s1 = build_typed_searches("示例公司A的企业文化是什么", True, ["示例公司A"], entity_names=names)
        self.assertEqual(len([x for x in s1 if x["type"] == "vec"]), 1)


class TestV23Multimodal(unittest.TestCase):
    def test_image_is_supported_raw_source(self):
        from rag_gateway.evidence import SUPPORTED
        for ext in ('.png', '.jpg', '.jpeg', '.webp', '.tiff'):
            self.assertIn(ext, SUPPORTED)

    def test_ocr_and_vision_blocks_enter_evidence_chunks(self):
        from rag_gateway.evidence import chunk_extracted_document
        doc = {'blocks': [
            {'block_id':'b0','kind':'paragraph','text':'原生正文。','breadcrumb':['A'],'locator':'pdf:page:1:block:1','metadata':{}},
            {'block_id':'b1','kind':'visual_ocr','text':'## OCR 识别文本\n步骤甲 步骤乙 步骤丙','breadcrumb':['A','OCR'],'locator':'pdf:page:1:ocr','metadata':{'visual_asset':'rag/assets/x.png','visual_kind':'pdf_visual_page'}},
            {'block_id':'b2','kind':'visual_semantics','text':'## 视觉语义解析\n流程为：步骤甲 → 步骤乙 → 步骤丙。','breadcrumb':['A','视觉语义'],'locator':'pdf:page:1:vision','metadata':{'visual_asset':'rag/assets/x.png','visual_kind':'pdf_visual_page'}},
        ]}
        chunks = chunk_extracted_document(doc, max_chars=500, overlap=20)
        text = '\n'.join(x['text'] for x in chunks)
        self.assertIn('步骤甲 → 步骤乙 → 步骤丙', text)
        self.assertIn('OCR 识别文本', text)
        self.assertTrue(any('visual_ocr' in x.get('kinds',[]) for x in chunks))
        self.assertTrue(any('rag/assets/x.png' in x.get('visual_assets',[]) for x in chunks))

    def test_visual_processor_merges_fake_ocr_and_vision(self):
        import tempfile
        from PIL import Image
        from rag_gateway.multimodal import VisualEvidenceProcessor, VisualAsset
        class FakeOCR:
            name='fake-ocr'
            def recognize(self, image_path): return '节点A 节点B'
        class FakeVision:
            name='fake-vision'
            def analyze(self, image_path, context=''): return '流程：节点A → 节点B。'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'rag/assets').mkdir(parents=True)
            img = root/'rag/assets/a.png'
            Image.new('RGB',(200,100),'white').save(img)
            import hashlib
            sha=hashlib.sha256(img.read_bytes()).hexdigest()
            cfg={'multimodal':{'enabled':True,'assets_dir':'rag/assets','cache_dir':'rag/visual_cache','ocr':{'enabled':True},'vision':{'enabled':True},'limits':{}}}
            p=VisualEvidenceProcessor(root,cfg)
            p._ocr=FakeOCR(); p._vision=FakeVision()
            asset=VisualAsset('v0','pdf:page:1',['第1页'],'rag/assets/a.png','pdf_visual_page','image/png',sha,{})
            a=p.analyze_asset(asset,'测试')
            self.assertIn('节点A',a.ocr_text)
            self.assertIn('节点A → 节点B',a.vision_text)
            self.assertTrue((root/'rag/visual_cache').exists())

class TestV24VisionSplit(unittest.TestCase):
    def test_hot_ingest_defers_visual_analysis_to_hermes(self):
        import tempfile
        from PIL import Image
        from rag_gateway.hot import HotDocumentStore
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            raw=root/'raw'; raw.mkdir()
            img=raw/'flow.png'
            Image.new('RGB',(320,180),'white').save(img)
            cfg={
                'hot_document': {'dir':'rag/hot','full_document_chars':42000,'max_context_chars':36000,
                                 'chunk_chars':3600,'overlap_chars':400,'seed_hits':6,'neighbor_radius':1,
                                 'max_hits':10,'recent_ttl_seconds':7200},
                'multimodal': {
                    'enabled':True,'assets_dir':'rag/assets','cache_dir':'rag/visual_cache',
                    'limits':{'min_image_width':120,'min_image_height':80,'max_visual_assets_per_document':20,'max_analyzed_assets_per_document':20},
                    'hot':{'vision_strategy':'hermes_native','max_materialized_visual_assets':10,'max_render_pages':5,
                           'max_eager_visual_assets':0,'max_hermes_vision_assets_per_query':4},
                    'ocr':{'enabled':True},'vision':{'enabled':True,'provider':'model_api'}
                }
            }
            store=HotDocumentStore(root,cfg)
            ing=store.ingest(img,profile='u')
            self.assertEqual(ing['status'],'ready')
            self.assertEqual(ing['visual_analysis'].get('strategy'),'hermes-native-deferred')
            self.assertEqual(ing['visual_analysis'].get('vision_block_count'),0)
            self.assertEqual(ing['visual_analysis'].get('ocr_block_count'),0)
            r=store.search('这张流程图是什么意思？',attachment=str(img),profile='u')
            self.assertTrue(r['hermes_vision']['required'])
            self.assertEqual(r['hermes_vision']['strategy'],'hermes-native')
            self.assertTrue(r['hermes_vision']['assets'])
            self.assertTrue(Path(r['hermes_vision']['assets'][0]['path']).exists())

    def test_hot_visual_not_required_for_plain_text_query_when_native_text_is_sufficient(self):
        import tempfile
        from rag_gateway.hot import HotDocumentStore
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            raw=root/'raw'; raw.mkdir()
            f=raw/'a.txt'; f.write_text('示例公司A项目的核心目标是提升组织协同和文化落地。'*40,encoding='utf-8')
            cfg={'hot_document': {'dir':'rag/hot','full_document_chars':42000,'max_context_chars':36000,
                                  'chunk_chars':3600,'overlap_chars':400,'seed_hits':6,'neighbor_radius':1,
                                  'max_hits':10,'recent_ttl_seconds':7200},
                 'multimodal':{'enabled':True,'hot':{'vision_strategy':'hermes_native','max_hermes_vision_assets_per_query':4}}}
            store=HotDocumentStore(root,cfg); store.ingest(f,profile='u')
            r=store.search('核心目标是什么？',attachment=str(f),profile='u')
            self.assertFalse(r['hermes_vision']['required'])
            self.assertEqual(r['hermes_vision']['assets'],[])


class TestLexLegAndBudgetPolicy(unittest.TestCase):
    """2026-10-12: lex_leg on/off/conditional + budget_policy (production defaults: conditional/density)."""

    def test_config_defaults_use_production_best(self):
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/rag.yaml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["retrieval"].get("lex_leg"), "conditional")
        self.assertEqual(cfg["context"].get("budget_policy"), "density")
        self.assertEqual(cfg["retrieval"]["modes"]["balanced"]["candidate_limit"], 20)
        self.assertEqual(cfg["retrieval"]["modes"]["deep"]["candidate_limit"], 16)
        self.assertEqual(cfg["context"]["policies"]["balanced"]["max_chars"], 12000)

    def test_conditional_drops_name_lex_for_nl(self):
        legs = build_typed_searches("示例公司A党委书记是谁", True, ["示例公司A"], lex_leg="conditional")
        self.assertFalse(any(l["type"] == "lex" for l in legs))
        self.assertTrue(any(l["type"] == "vec" for l in legs))

    def test_conditional_keeps_identifier_lex(self):
        legs = build_typed_searches("#U676d", True, [], lex_leg="conditional")
        lex = [l for l in legs if l["type"] == "lex"]
        self.assertTrue(lex)
        self.assertEqual(lex[0]["query"], "#U676d")

    def test_conditional_preserves_identifier_in_mixed_query(self):
        legs = build_typed_searches("示例公司A 20260622_demo 访谈整理", True, ["示例公司A"], lex_leg="conditional")
        lex = [l for l in legs if l["type"] == "lex"]
        self.assertTrue(lex)
        self.assertEqual(lex[0]["query"], "20260622_demo")

    def test_fast_mode_keeps_lex(self):
        legs = build_typed_searches("示例公司A党委书记是谁", False, ["示例公司A"], lex_leg="conditional")
        self.assertTrue(any(l["type"] == "lex" for l in legs))

    def test_on_keeps_legacy_name_lex(self):
        legs = build_typed_searches("示例公司A党委书记是谁", True, ["示例公司A"], lex_leg="on")
        lex = [l for l in legs if l["type"] == "lex"]
        self.assertTrue(lex)
        self.assertEqual(lex[0]["query"], "示例公司A")

    def test_off_drops_lex(self):
        legs = build_typed_searches("示例公司A党委书记是谁", True, ["示例公司A"], lex_leg="off")
        self.assertFalse(any(l["type"] == "lex" for l in legs))

    def test_identifier_queries_avoid_fast_mode(self):
        # fast is lex-only over company-core; identifier queries must reach evidence.
        self.assertEqual(choose_mode("20260622_demo"), "balanced")
        self.assertEqual(choose_mode("20260622_demo"), "balanced")
        self.assertEqual(choose_mode("示例公司A党委书记是谁"), "fast")
        self.assertEqual(choose_mode("20260622_demo", requested="fast"), "fast")  # explicit stays explicit
