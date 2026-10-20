"""Markup for the HTML design editor: the checks list and the small notes. Every piece of text is escaped."""
import html
from typing import Dict, List

HTML_EDITOR_CSS = """
.he-ck { display: flex; gap: 9px; align-items: flex-start; font-size: 13px; margin: 8px 0; line-height: 1.45; color: #374151; }
.he-ck i { width: 18px; height: 18px; border-radius: 50%; flex: none; display: flex; align-items: center; justify-content: center; font-size: 11px; font-style: normal; font-weight: 800; margin-top: 1px; }
.he-ok { background: #dcfce7; color: #15803d; } .he-wn { background: #fff4e5; color: #b45309; } .he-er { background: #fdecec; color: #b91c1c; }
.he-title { font-weight: 700; font-size: 14px; color: #111827; margin: 4px 0 6px; }
.he-sub { font-size: 12.5px; color: #6b7280; margin: 0 0 8px; }
.he-footer-preview { border: 1px dashed #d1d5db; border-radius: 10px; background: #fafbfc; margin: 2px 0 6px 0; }
[class*="st-key-he_footer"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; }
[class*="st-key-he_checks"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; }
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def checks_html(result: Dict[str, List[str]], extra_errors: List[str] = None) -> str:
    """The checks as a list: a red cross for each error, an amber mark for each warning, and a green tick (with a short line) when there is nothing to report."""
    errors = list(result.get("errors") or []) + list(extra_errors or [])
    warnings = list(result.get("warnings") or [])
    rows = [f'<div class="he-ck"><i class="he-er">✕</i><span>{esc(message)}</span></div>' for message in errors]
    rows += [f'<div class="he-ck"><i class="he-wn">!</i><span>{esc(message)}</span></div>' for message in warnings]
    if not rows:
        rows = ['<div class="he-ck"><i class="he-ok">✓</i><span>Every check passed.</span></div>']
    elif not errors:
        rows.insert(0, '<div class="he-ck"><i class="he-ok">✓</i><span>No problems that stop sending. These are only suggestions:</span></div>')
    return "".join(rows)


def title_html(text: str, sub: str = "") -> str:
    return f'<div class="he-title">{esc(text)}</div>' + (f'<div class="he-sub">{esc(sub)}</div>' if sub else "")
