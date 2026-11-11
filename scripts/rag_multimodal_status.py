#!/usr/bin/env python3
from pathlib import Path
from urllib import request
import importlib.util, json, os, shutil, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag_gateway.config import load_config

cfg = load_config(ROOT).get('multimodal', {})
vision = cfg.get('vision', {})
base = os.environ.get(vision.get('base_url_env', 'COMPANY_WIKI_VISION_BASE_URL'), vision.get('base_url', '')).rstrip('/')
model = os.environ.get(vision.get('model_env', 'COMPANY_WIKI_VISION_MODEL'), vision.get('model', ''))
key = os.environ.get(vision.get('api_key_env','COMPANY_WIKI_VISION_API_KEY'), vision.get('api_key',''))
checks = {
    'multimodal_enabled': bool(cfg.get('enabled', True)),
    'paddleocr_python': importlib.util.find_spec('paddleocr') is not None,
    'tesseract_cli': bool(shutil.which('tesseract')),
    'libreoffice_cli': bool(shutil.which('libreoffice') or shutil.which('soffice')),
    'assets': sum(1 for p in (ROOT / cfg.get('assets_dir', 'rag/assets')).rglob('*') if p.is_file()) if (ROOT / cfg.get('assets_dir', 'rag/assets')).exists() else 0,
    'visual_cache_entries': sum(1 for p in (ROOT / cfg.get('cache_dir', 'rag/visual_cache')).glob('*.json')) if (ROOT / cfg.get('cache_dir', 'rag/visual_cache')).exists() else 0,
    'background_vision_provider': vision.get('provider', 'model_api'),
    'vision_api_base_url': base,
    'vision_api_model': model,
    'vision_api_key_present': bool(key),
    'vision_api_configured': bool(base and model and key),
    'hot_vision_strategy': (cfg.get('hot', {}) or {}).get('vision_strategy', 'hermes_native'),
}
# Optional lightweight probe only when explicitly enabled. Hosted APIs differ in /models support.
checks['vision_api_http'] = None
if bool(vision.get('probe_on_start', False)) and base and key:
    try:
        req = request.Request(base + '/models', headers={'Authorization': 'Bearer ' + key})
        with request.urlopen(req, timeout=float(vision.get('probe_timeout_seconds', 1.5))) as r:
            checks['vision_api_http'] = 200 <= r.status < 300
    except Exception:
        checks['vision_api_http'] = False

manifest = ROOT / 'rag/manifest.json'
if manifest.exists():
    data = json.loads(manifest.read_text(encoding='utf-8'))
    raw = list(data.get('raw_extracted', {}).values())
    checks['manifest_visual_assets'] = sum(int(x.get('visual_asset_count', 0)) for x in raw)
    checks['manifest_ocr_blocks'] = sum(int((x.get('visual_analysis') or {}).get('ocr_block_count', 0)) for x in raw)
    checks['manifest_vision_blocks'] = sum(int((x.get('visual_analysis') or {}).get('vision_block_count', 0)) for x in raw)
    checks['manifest_visual_pending'] = sum(int((x.get('visual_analysis') or {}).get('pending_asset_count', 0)) for x in raw)
    checks['manifest_visual_unresolved'] = sum(int((x.get('visual_analysis') or {}).get('unresolved_vision_asset_count', 0)) for x in raw)

print(json.dumps(checks, ensure_ascii=False, indent=2))
