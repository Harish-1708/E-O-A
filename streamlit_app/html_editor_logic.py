"""The pure side of the HTML design editor (in an HTML campaign's Sequences tab): which leads can be previewed, reading an uploaded file, and rendering a preview with exactly
the function the real send uses, so what is previewed is what would be sent. No Streamlit and no network here."""
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import html_campaign_logic as hc  # noqa: E402
import outreach  # noqa: E402

SAMPLE_LEAD = {"FirstName": "Ann", "LastName": "Lee", "Company": "Acme Studio", "Email": "ann@example.com"}
UPLOAD_LIMIT_BYTES = 1_000_000                      # far more than Gmail will show (it cuts at about 102 KB), but a sane ceiling for an upload
PREVIEW_WIDTHS = {"Desktop": None, "Mobile": 375}
PREVIEW_HEIGHT = 380                  # where the preview frame starts; the page inside then resizes the frame AND the box around it to fit the email (see FIT_SCRIPT)
PREVIEW_MAX_HEIGHT = 900              # a longer email scrolls inside the preview from here, so a big template never pushes everything else off the screen
# Added to the PREVIEW only (never saved, never sent, and only after the design itself has passed the safety check, which refuses any script of the designer's own). It sets the
# height of the preview's frame AND of the element Streamlit wraps it in to the height of the email: sizing the frame alone left the wrapper at its starting height, so a tall
# email spilled out over whatever was drawn below it. The frame is never made taller than the wrapper actually accepted, so if the wrapper cannot be resized the preview
# simply scrolls inside itself: it can never overlap what is below.
FIT_SCRIPT = ("<script>(function(){var cap=%d;function fit(){try{var d=document.documentElement,b=document.body;var h=Math.min(Math.max(d.scrollHeight,b?b.scrollHeight:0),cap);"
              "var f=window.frameElement,w=f.parentElement,t=h;if(w){w.style.setProperty('height',h+'px','important');t=Math.min(h,w.getBoundingClientRect().height||h);}"
              "f.style.setProperty('height',t+'px','important');}catch(e){}}"
              "window.addEventListener('load',fit);window.addEventListener('resize',fit);setTimeout(fit,120);setTimeout(fit,700);})();</script>") % PREVIEW_MAX_HEIGHT
FOOTER_PLACEHOLDER = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#92400e;background:#fffaf0;border:1px dashed #f3d9a8;padding:10px;margin:8px;'
                      'text-align:center;">Your footer will appear here. Add the business name and the postal address in the Email footer panel below.</div>')


def footer_snapshot_key(campaign_name: str) -> str:
    """The session key holding a footer that was just saved, until the app's own copy of the settings catches up (after the app redeploys)."""
    return f"footer_saved_{campaign_name}"


def effective_footer(campaign_cfg: Dict, just_saved: Optional[Dict] = None) -> Dict:
    """The footer in force: the one just saved if there is one (so the preview, the checks and the launch check use it straight away), else the campaign's own."""
    footer = just_saved if just_saved else (campaign_cfg.get("footer") or {})
    return {key: str(footer.get(key) or "").strip() for key in hc.FOOTER_FIELDS}


def design_path(campaign_name: str, prefix: str, variant: str) -> str:
    return f"templates/{campaign_name}/{prefix}_{variant}.html"


def known_variables(leads: List[Dict]) -> Optional[List[str]]:
    """The placeholder names that mean something: the template variables plus every column of the Sheet. None when there are no leads yet (the columns are then unknown,
    and the checks skip the placeholder rule instead of wrongly reporting a custom column)."""
    if not leads:
        return None
    columns: List[str] = []
    for lead in leads:
        for key in lead:
            if not str(key).startswith("_") and key not in columns:
                columns.append(key)
    return list(dict.fromkeys(list(outreach.TEMPLATE_VARIABLE_MAP) + columns))


def lead_options(leads: List[Dict], limit: int = 25) -> List[Tuple[str, str]]:
    """[(key, label)]: the sample lead first, then the first few real leads, so a design can be previewed with real names."""
    options = [("sample", f"Sample lead ({SAMPLE_LEAD['FirstName']} {SAMPLE_LEAD['LastName']})")]
    for index, lead in enumerate((leads or [])[:limit]):
        name = " ".join(part for part in (str(lead.get("FirstName") or "").strip(), str(lead.get("LastName") or "").strip()) if part)
        email = str(lead.get("Email") or "").strip()
        label = f"{name} · {email}" if name and email else (name or email or f"Lead {index + 1}")
        options.append((f"lead_{index}", label))
    return options


def pick_lead(leads: List[Dict], key: str) -> Dict:
    """The lead a key stands for. Anything unknown (or an index that no longer exists) gives the sample lead rather than an error."""
    if key and key.startswith("lead_"):
        try:
            return dict((leads or [])[int(key[5:])])
        except (ValueError, IndexError):
            pass
    return dict(SAMPLE_LEAD)


def decode_upload(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    """(text, None) for a usable uploaded file, or (None, the reason it cannot be used)."""
    if data is None or not data.strip():
        return None, "The file is empty."
    if len(data) > UPLOAD_LIMIT_BYTES:
        return None, f"The file is {len(data) // 1024} KB, which is too large for an email (Gmail cuts messages off after about 102 KB)."
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "The file is not saved as UTF-8 text. Save it again from your editor as UTF-8 and upload it again."


def render_preview(raw: str, lead: Dict, campaign_cfg: Dict, utm_content: str = "") -> Dict:
    """{"html": the email as one lead would receive it (None when it must not be shown), "errors": [...], "missing": [placeholders that match no column]}. Unsafe HTML is never
    rendered at all. An incomplete footer is reported (a real send would refuse) while the preview still shows the layout, with a marker where the footer goes."""
    unsafe = hc.unsafe_problems(raw)
    if unsafe:
        return {"html": None, "errors": unsafe, "missing": []}
    try:
        result = outreach.render_html_text(raw, lead, campaign_cfg, utm_content=utm_content)
        return {"html": result["html"], "errors": [], "missing": result["missing_variables"]}
    except outreach.TemplateError as exc:
        if "footer is incomplete" not in str(exc):
            return {"html": None, "errors": [str(exc)], "missing": []}
        missing: List[str] = []
        shown = outreach.render_text(raw, lead, missing_out=missing, escape=outreach.html_lib.escape, reserved={"footer": FOOTER_PLACEHOLDER})
        return {"html": shown, "errors": [str(exc)], "missing": list(dict.fromkeys(missing))}


def preview_document(rendered: str) -> str:
    """The rendered email as a complete page for the preview frame: a fragment is wrapped (white background, readable default font), a full page is kept as it is, and the
    script that fits the frame to the email is added at the end of the body."""
    page = rendered if "<html" in rendered.lower() else f'<html><body style="margin:0;background:#ffffff;font-family:Arial,Helvetica,sans-serif;">{rendered}</body></html>'
    closing = page.lower().rfind("</body>")
    return page + FIT_SCRIPT if closing < 0 else page[:closing] + FIT_SCRIPT + page[closing:]
