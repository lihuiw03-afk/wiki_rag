from __future__ import annotations
from pathlib import Path
import argparse, json, sys
from .config import load_config
from .projection import build_projection
from .search import CompanyRAG


def project_root(value: str | None) -> Path:
    return Path(value or ".").resolve()


def main(argv=None):
    p = argparse.ArgumentParser(prog="company-rag", description="Company Wiki RAG V2")
    p.add_argument("--root", default=".", help="company-wiki-rag-v2 root")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="Build chapter-aware wiki and raw-evidence projections")

    s = sub.add_parser("search", help="Search via QMD + hierarchy + Wiki graph")
    s.add_argument("query")
    s.add_argument("--mode", choices=["auto", "fast", "balanced", "deep"], default="auto")
    s.add_argument("--context-policy", choices=["auto", "balanced", "diversity", "source-focused"], default="auto")
    s.add_argument("--profile", default=None, help="Caller identity for tracing only; all profiles have full knowledge access")
    s.add_argument("--hyde", default=None, help="Optional LLM-generated hypothetical evidence for deep retrieval")
    s.add_argument("--json", action="store_true")
    s.add_argument("--context-only", action="store_true")

    args = p.parse_args(argv)
    root = project_root(args.root)
    if args.cmd == "build":
        cfg = load_config(root)
        stats = build_projection(root, cfg)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "search":
        try:
            result = CompanyRAG(root).search(
                args.query,
                mode=args.mode,
                profile=args.profile,
                hyde=args.hyde,
                context_policy=args.context_policy,
            )
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if args.context_only:
            print(result["context"])
        elif args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"mode={result['mode']} context={result['context_policy']} "
                f"elapsed={result['elapsed_ms']}ms collections={','.join(result['collections'])}"
            )
            print(result["context"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
