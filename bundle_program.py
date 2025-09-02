#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline bundler: combine multiple local HTML decks into a single self-contained
HTML "Program" that plays each deck in sequence using an <iframe srcdoc> runner.

Usage examples:
  python3 bundle_program.py -o program.html shabakh-pres.html death-of-author.html
  python3 bundle_program.py -o program-egypt.html shabakh-pres.html

Notes:
- Local relative assets are inlined:
  * <link rel="stylesheet" href="local.css"> → <style>…</style>
  * <script src="local.js"></script> → <script>…</script>
  * <img src="local.png"> → <img src="data:image/png;base64,….">
- Remote assets (http/https) are left as-is.
- CSS url(...) inside local CSS are NOT rewritten; prefer embedding images
  directly in your decks or keep backgrounds simple for export.
"""

from __future__ import annotations
import argparse
import base64
import html
import mimetypes
import os
import re
from pathlib import Path

# Simple regexes for inlining. These are intentionally conservative.
LINK_CSS_RE = re.compile(r"<link\s+[^>]*rel=[\"']stylesheet[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
SCRIPT_SRC_RE = re.compile(r"<script\s+[^>]*src=[\"']([^\"']+)[\"'][^>]*>\s*</script>", re.IGNORECASE)
IMG_SRC_RE = re.compile(r"(<img\s+[^>]*src=[\"'])([^\"']+)([\"'][^>]*>)", re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

def is_remote(url: str) -> bool:
    return url.startswith('http://') or url.startswith('https://') or url.startswith('data:')

def read_text(p: Path) -> str:
    with p.open('r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def read_bytes(p: Path) -> bytes:
    with p.open('rb') as f:
        return f.read()

def to_data_uri(p: Path) -> str:
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or 'application/octet-stream'
    b = read_bytes(p)
    b64 = base64.b64encode(b).decode('ascii')
    return f"data:{mime};base64,{b64}"

def inline_css_and_js(html_text: str, base_dir: Path) -> str:
    # Inline CSS links
    def repl_link(m):
        href = m.group(1)
        if is_remote(href):
            return m.group(0)
        css_path = (base_dir / href).resolve()
        if css_path.exists():
            try:
                css = read_text(css_path)
                return f"<style>\n{css}\n</style>"
            except Exception:
                return m.group(0)
        return m.group(0)

    html_text = LINK_CSS_RE.sub(repl_link, html_text)

    # Inline JS scripts
    def repl_script(m):
        src = m.group(1)
        if is_remote(src):
            return m.group(0)
        js_path = (base_dir / src).resolve()
        if js_path.exists():
            try:
                js = read_text(js_path)
                return f"<script>\n{js}\n</script>"
            except Exception:
                return m.group(0)
        return m.group(0)

    html_text = SCRIPT_SRC_RE.sub(repl_script, html_text)

    return html_text

def inline_images(html_text: str, base_dir: Path) -> str:
    def repl_img(m):
        pre, src, post = m.groups()
        if is_remote(src):
            return m.group(0)
        img_path = (base_dir / src).resolve()
        if img_path.exists():
            try:
                data = to_data_uri(img_path)
                return f"{pre}{data}{post}"
            except Exception:
                return m.group(0)
        return m.group(0)

    return IMG_SRC_RE.sub(repl_img, html_text)

def extract_title(html_text: str, fallback: str) -> str:
    m = TITLE_RE.search(html_text)
    if m:
        return html.unescape(m.group(1)).strip() or fallback
    return fallback

RUNNER_TEMPLATE = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>{title}</title>
<style>
  :root{ --bg:#0f1114; --panel:#16191f; --ink:#e9edf3; --muted:#9aa3ad; --rule:#2a3038; --accent:#e0523f }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
  header{position:sticky; top:0; z-index:10; background:linear-gradient(180deg,rgba(22,25,31,.9),rgba(22,25,31,.75)); border-bottom:1px solid var(--rule); backdrop-filter: blur(6px)}
  .bar{display:flex; align-items:center; gap:10px; padding:10px 14px}
  .brand{font-weight:800}
  .controls{margin-left:auto; display:flex; gap:8px; align-items:center}
  button,select{appearance:none; border:1px solid var(--rule); background:var(--panel); color:var(--ink); border-radius:10px; padding:8px 10px; cursor:pointer}
  main{height:calc(100% - 52px)}
  .stage{height:100%; display:grid; place-items:center; padding:10px}
  iframe{ width:min(1200px, 94vw); aspect-ratio:16/9; border-radius:16px; border:1px solid var(--rule); box-shadow:0 12px 36px rgba(0,0,0,.25); background:#000; }
  .meta{color:var(--muted); font:600 12px/1 ui-monospace,Menlo,Consolas,monospace}
</style>
</head>
<body>
  <header>
    <div class=\"bar\">
      <div class=\"brand\">{title}</div>
      <div class=\"controls\">
        <button id=\"prev\">◀ Prev</button>
        <button id=\"next\">Next ▶</button>
        <span id=\"ctr\" class=\"meta\"></span>
        <button id=\"popout\">Open in Window</button>
      </div>
    </div>
  </header>
  <main>
    <div class=\"stage\"><iframe id=\"frame\"></iframe></div>
  </main>
  <script>
    const DECKS = {decks_json}; // [{title, html}]
    const $f = document.getElementById('frame');
    const $ctr = document.getElementById('ctr');
    const $prev = document.getElementById('prev');
    const $next = document.getElementById('next');
    const $pop = document.getElementById('popout');
    let idx = 0; const total = DECKS.length;

    function update(){
      const d = DECKS[idx];
      $f.srcdoc = d.html;
      $ctr.textContent = `${idx+1} / ${total} — ${d.title}`;
      history.replaceState(null, '', '#' + (idx+1));
    }
    function show(i){ idx = Math.max(0, Math.min(total-1, i)); update(); }
    $prev.addEventListener('click', ()=> show(idx-1));
    $next.addEventListener('click', ()=> show(idx+1));
    window.addEventListener('keydown', (e)=>{
      if(e.key==='ArrowLeft') show(idx-1);
      if(e.key==='ArrowRight' || e.key===' ') show(idx+1);
    });

    // Pop out current deck into its own window
    $pop.addEventListener('click', ()=>{
      const d = DECKS[idx];
      const w = window.open('', '_blank', 'noopener,noreferrer,width=1280,height=800');
      if(w) { w.document.open(); w.document.write(d.html); w.document.close(); }
    });

    const start = Math.max(1, Math.min(total, parseInt(location.hash.replace('#','')||'1',10)))-1; show(start);
  </script>
</body>
</html>
"""


def process_html(input_path: Path) -> tuple[str, str]:
    base_dir = input_path.parent
    raw = read_text(input_path)
    # Inline CSS & JS
    inlined = inline_css_and_js(raw, base_dir)
    # Inline <img> tags
    inlined = inline_images(inlined, base_dir)
    # Title
    title = extract_title(inlined, input_path.name)
    return title, inlined


def main():
    ap = argparse.ArgumentParser(description='Bundle multiple local HTML decks into a single self-contained Program HTML.')
    ap.add_argument('-o', '--output', required=True, help='Output HTML file path (e.g., program.html)')
    ap.add_argument('inputs', nargs='+', help='Input HTML files in desired order')
    args = ap.parse_args()

    inputs = [Path(p).resolve() for p in args.inputs]
    for p in inputs:
        if not p.exists():
            raise SystemExit(f"Input not found: {p}")

    decks = []
    for p in inputs:
        title, html_text = process_html(p)
        # Ensure the deck HTML has a proper <meta charset> to avoid encoding issues when injected
        if '<meta charset' not in html_text.lower():
            html_text = html_text.replace('<head>', '<head>\n  <meta charset="utf-8" />', 1)
        decks.append({
            'title': title,
            'html': html_text
        })

    # Pick a program title from first deck
    program_title = f"Program — {decks[0]['title']} (+{len(decks)-1})" if len(decks) > 1 else decks[0]['title']

    # JSON-safe embedding without importing json (to keep script minimal and robust)
    def js_string_escape(s: str) -> str:
        return s.replace('\\', '\\\\').replace('`', '\\`')

    decks_js = '[\n' + ',\n'.join(
        [f"  {{title: `{js_string_escape(d['title'])}`, html: `{js_string_escape(d['html'])}`}}" for d in decks]
    ) + '\n]'

    out_html = RUNNER_TEMPLATE.format(title=html.escape(program_title), decks_json=decks_js)

    out_path = Path(args.output).resolve()
    out_path.write_text(out_html, encoding='utf-8')
    print(f"Wrote {out_path}")

if __name__ == '__main__':
    main()
