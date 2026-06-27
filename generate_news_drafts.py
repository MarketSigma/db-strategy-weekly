    #!/usr/bin/env python3
"""
Generate 3 weekly strategy article drafts by combining latest news with Supabase bank metrics.

Usage:
  python generate_news_drafts.py --out drafts.html --bank "Doha Bank"

Environment:
  SUPABASE_URL
  SUPABASE_KEY
  OPENAI_API_KEY                 optional but recommended
  OPENAI_MODEL                   optional, default gpt-4.1-mini

This script is intentionally separate from the daily dashboard.
It reads only bank_metric_values / related views and writes only local HTML/JSON files.
"""
import argparse, datetime as dt, html, json, os, re
from pathlib import Path
from typing import Dict, List, Any

import feedparser

BLUE = "#0072ce"
NAVY = "#002b5c"
SLATE = "#2c3e54"
MUTED = "#7a8aa0"

ROOT = Path(__file__).resolve().parent


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_news(limit_per_feed: int = 8) -> List[Dict[str, str]]:
    sources = load_json(ROOT / "news_sources.json")
    items: List[Dict[str, str]] = []
    for group, feeds in sources.items():
        for src in feeds:
            parsed = feedparser.parse(src["rss"])
            for e in parsed.entries[:limit_per_feed]:
                title = getattr(e, "title", "").strip()
                summary = re.sub("<.*?>", "", getattr(e, "summary", "")).strip()
                link = getattr(e, "link", "")
                published = getattr(e, "published", "") or getattr(e, "updated", "")
                if title:
                    items.append({
                        "group": group,
                        "source": src["name"],
                        "title": title,
                        "summary": summary[:500],
                        "url": link,
                        "published": published,
                    })
    return items


def score_news(items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rules = load_json(ROOT / "impact_rules.json")
    scored = []
    for item in items:
        text = (item["title"] + " " + item.get("summary", "")).lower()
        matches = []
        score = 0
        for code, rule in rules.items():
            hit_count = sum(1 for k in rule["keywords"] if k in text)
            if hit_count:
                score += hit_count * 10
                matches.append({"impact_code": code, **rule})
        if matches:
            scored.append({**item, "score": score, "matches": matches})
    return sorted(scored, key=lambda x: x["score"], reverse=True)


def fetch_metrics(bank: str) -> Dict[str, Any]:
    try:
        from supabase import create_client
    except Exception:
        return {}
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        return {}
    sb = create_client(url, key)
    rows = sb.table("bank_metric_latest_verified").select("*").eq("bank_name", bank).execute().data or []
    peers = sb.table("bank_metric_latest_verified").select("*").neq("bank_name", bank).execute().data or []
    metrics = {r["metric_code"]: r for r in rows}
    peer_metrics: Dict[str, Dict[str, Any]] = {}
    for r in peers:
        peer_metrics.setdefault(r["bank_name"], {})[r["metric_code"]] = r
    return {"primary_bank": bank, "metrics": metrics, "peers": peer_metrics}


def fallback_drafts(scored: List[Dict[str, Any]], metric_pack: Dict[str, Any], bank: str) -> List[Dict[str, Any]]:
    drafts = []
    top = scored[:3] or [{"title": "Weekly banking sector development", "summary": "No high-confidence RSS match was found.", "source": "System", "url": "", "published": "", "matches": [{"angle": "strategic implication", "metrics": []}]}]
    metrics = metric_pack.get("metrics", {})
    def val(code):
        r = metrics.get(code) or {}
        v = r.get("value")
        unit = r.get("unit", "")
        if v is None:
            return "not yet available"
        if unit == "percent":
            return f"{v}%"
        if unit == "QAR million" and abs(float(v)) >= 1000:
            return f"QAR {float(v)/1000:.1f}bn"
        if unit == "QAR million":
            return f"QAR {float(v):.0f}m"
        return f"{v} {unit}".strip()
    for i, n in enumerate(top, 1):
        match = n["matches"][0]
        drafts.append({
            "rank": i,
            "headline": f"{match.get('angle','Market development')}: what it means for {bank}",
            "source_title": n["title"],
            "source": n["source"],
            "url": n.get("url", ""),
            "development": n.get("summary") or n["title"],
            "bank_read": f"For {bank}, this should be read against net loans of {val('net_loans')}, deposits of {val('customer_deposits')}, NIM of {val('nim_pct')}, CET1 of {val('cet1_pct')} and CAR of {val('car_pct')}.",
            "recommendation": "Use this as a management discussion draft. Replace any unverified metrics before final approval.",
            "metrics_used": match.get("metrics", [])
        })
    return drafts


def llm_refine(drafts: List[Dict[str, Any]], metric_pack: Dict[str, Any], bank: str) -> List[Dict[str, Any]]:
    if not os.getenv("OPENAI_API_KEY"):
        return drafts
    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = {
            "instruction": "Rewrite these into concise executive weekly strategy article drafts. Return valid JSON list only. Each draft must include headline, why_now, doha_bank_impact, strategic_options, final_reflection. Do not invent figures; only use provided metric values.",
            "bank": bank,
            "drafts": drafts,
            "metrics": metric_pack,
        }
        resp = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=json.dumps(prompt, default=str),
        )
        text = resp.output_text.strip()
        return json.loads(text)
    except Exception as e:
        print(f"LLM refinement skipped: {e}")
        return drafts


def render_html(drafts: List[Dict[str, Any]], bank: str) -> str:
    today = dt.date.today().strftime("%d %B %Y")
    blocks = ""
    for i, d in enumerate(drafts, 1):
        opts = d.get("strategic_options")
        if isinstance(opts, list):
            opts_html = "".join(f"<li>{html.escape(str(x))}</li>" for x in opts)
        else:
            opts_html = f"<li>{html.escape(str(d.get('recommendation', opts or 'Review and approve before sending.')))}</li>"
        blocks += f"""
        <tr><td style="padding:22px 34px; border-top:1px solid #e2e8f0;">
          <p style="margin:0 0 6px 0; font-family:Arial; font-size:11px; letter-spacing:1.5px; color:{BLUE}; text-transform:uppercase; font-weight:bold;">Draft {i}</p>
          <h2 style="margin:0 0 10px 0; color:{NAVY}; font-family:Georgia; font-weight:normal; font-size:24px; line-height:1.25;">{html.escape(str(d.get('headline','Untitled')))}</h2>
          <p style="margin:0 0 12px 0; color:{MUTED}; font-family:Arial; font-size:12px;">Source: {html.escape(str(d.get('source_title', d.get('source',''))))}</p>
          <p style="font-size:16px; line-height:1.6; color:{SLATE};"><strong>Why now:</strong> {html.escape(str(d.get('why_now', d.get('development',''))))}</p>
          <p style="font-size:16px; line-height:1.6; color:{SLATE};"><strong>{html.escape(bank)} impact:</strong> {html.escape(str(d.get('doha_bank_impact', d.get('bank_read',''))))}</p>
          <p style="font-size:16px; line-height:1.6; color:{SLATE};"><strong>Strategic options:</strong></p>
          <ul style="font-size:16px; line-height:1.6; color:{SLATE};">{opts_html}</ul>
          <p style="font-size:15px; line-height:1.6; color:{NAVY}; font-style:italic;">{html.escape(str(d.get('final_reflection', d.get('recommendation',''))))}</p>
        </td></tr>
        """
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#eef2f6;font-family:Georgia;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:28px 12px;background:#eef2f6;"><tr><td align="center">
    <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;max-width:680px;">
      <tr><td style="height:4px;background:{NAVY};"></td></tr>
      <tr><td style="padding:28px 34px;">
        <p style="font-family:Arial;font-size:12px;letter-spacing:2px;color:{BLUE};font-weight:bold;text-transform:uppercase;margin:0;">Approval Required</p>
        <h1 style="color:{NAVY};font-weight:normal;margin:12px 0 4px 0;">DB Strategy Weekly — Draft Topics</h1>
        <p style="font-family:Arial;color:{MUTED};font-size:13px;margin:0;">{today} · Select one draft, amend if needed, then approve final send in Make.</p>
      </td></tr>
      {blocks}
      <tr><td style="padding:18px 34px;border-top:1px solid #e2e8f0;"><p style="font-family:Arial;color:{MUTED};font-size:11px;line-height:1.5;margin:0;">Generated from latest RSS/news sources and verified Supabase metric values. Review before external distribution.</p></td></tr>
    </table></td></tr></table></body></html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="drafts.html")
    p.add_argument("--json-out", default="drafts.json")
    p.add_argument("--bank", default="Doha Bank")
    args = p.parse_args()

    news = fetch_news()
    scored = score_news(news)
    metric_pack = fetch_metrics(args.bank)
    drafts = fallback_drafts(scored, metric_pack, args.bank)
    drafts = llm_refine(drafts, metric_pack, args.bank)

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(drafts, f, indent=2, ensure_ascii=False, default=str)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render_html(drafts, args.bank))
    print(f"Wrote {args.out} and {args.json_out}. Draft count: {len(drafts)}")


if __name__ == "__main__":
    main()

    
