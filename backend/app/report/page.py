"""The HTML report page: one self-contained file (styles inline, screenshots
as data URIs) that reads well on screen, in the app's preview and printed to
PDF. It serializes the blocks of :func:`app.report.build_blocks`; what each
audience sees is decided there, this module only lays it out.
"""

from __future__ import annotations

import base64
import html
from typing import Any

e = html.escape

STATUS_TEXT = {"verified": "Verified", "unverified": "Unverified",
               "leaked": "Leaked record", "inference": "Analyst inference"}


def to_html(blocks: list[tuple[str, Any]]) -> str:
    title = next((v for k, v in blocks if k == "h1"), "Report")
    audience = next((v for k, v in blocks if k == "audience"), "internal")
    from . import AUDIENCE_NOTE  # the package imports this module

    cover: list[str] = []
    main: list[str] = []
    section = 0
    for kind, val in blocks:
        if kind == "h1":
            cover.append(f"<h1>{e(val)}</h1>")
        elif kind == "audience":
            label = "Internal working copy" if val == "internal" else "Client copy"
            cover.insert(0, f'<div class="eyebrow"><span class="tag tag-{val}">{label}</span>'
                            "<span>Open-source intelligence report</span></div>")
        elif kind == "meta":
            cover.append('<dl class="meta">' + "".join(
                f"<div><dt>{e(k)}</dt><dd>{e(str(v))}</dd></div>" for k, v in val.items())
                + "</dl>")
        elif kind == "stats":
            cover.append('<div class="stats">' + "".join(
                f'<div class="stat"><b>{e(str(v))}</b><span>{e(k)}</span></div>'
                for k, v in val.items()) + "</div>")
        elif kind == "h2":
            section += 1
            slug = "evidence" if val == "Evidence" else f"s{section}"
            main.append(f'<h2 id="{slug}"><span class="num">{section:02d}</span>{e(val)}</h2>')
        elif kind == "p":
            if val:
                main.append(f'<p class="lead">{e(val)}</p>')
        elif kind == "claims":
            main.append('<ol class="claims">' + "".join(_claim(c) for c in val) + "</ol>")
        elif kind == "ul":
            main.append('<ul class="steps">' + "".join(f"<li>{e(x)}</li>" for x in val) + "</ul>")
        elif kind == "evidence":
            main.append('<div class="evidence">' + "".join(_evidence(i) for i in val) + "</div>")

    notice = (f'<div class="notice notice-{audience}" role="note">'
              f"{e(AUDIENCE_NOTE[audience])}</div>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{e(title)}</title><style>{CSS}</style></head>"
            f'<body class="aud-{audience}"><main class="page">'
            f'<header class="cover">{"".join(cover)}</header>{notice}'
            f'{"".join(main)}<footer class="end">End of report</footer></main></body></html>')


def _claim(c: dict) -> str:
    anchor = f' id="claim-{e(c["id"])}"' if c.get("id") else ""
    tags = []
    if c.get("id"):
        tags.append(f'<code class="cid">{e(c["id"])}</code>')
    if c.get("severity"):
        tags.append(f'<span class="pill sev-{e(c["severity"])}">{e(c["severity"].title())} severity</span>')
    # Verified is the default: the client copy only flags what is not.
    if c["status"] != "verified" or c.get("id"):
        tags.append(f'<span class="pill st-{e(c["status"])}">{STATUS_TEXT.get(c["status"], e(c["status"]))}</span>')
    if c.get("sensitive"):
        tags.append('<span class="pill st-leaked">Sensitive (Art. 9)</span>')
    cites = "".join(f'<a class="cite" href="#ev{n}" title="Evidence {n}">{n}</a>' for n in c["refs"])
    return (f'<li{anchor} class="claim st-{e(c["status"])}"><div class="claim-body">'
            f'<p>{e(c["text"])}</p>'
            + (f'<div class="tags">{"".join(tags)}</div>' if tags else "")
            + "</div>"
            + (f'<div class="cites" aria-label="Evidence">{cites}</div>' if cites else "")
            + "</li>")


def _evidence(i: dict) -> str:
    link = (f'<a class="src-link" href="{e(i["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{e(_short(i["url"]))} &#8599;</a>') if i.get("url") else ""
    head = (f'<header><span class="evn">{i["n"]}</span><div class="evh">'
            f'<div class="evsrc">{e(i["source"])}</div>'
            f'<div class="evsub"><span class="q">{e(i["arg"])}</span>'
            f'<span>{e(i["verb"])} {e(i["date"])}</span>{link}</div></div></header>')
    facts = ""
    if i["facts"]:
        rows = "".join(f"<tr><th>{e(t)}</th><td>{e(v)}</td></tr>" for t, v in i["facts"])
        if i["more"]:
            rows += f'<tr><th></th><td class="muted">and {i["more"]} more</td></tr>'
        facts = f'<table class="facts"><caption>What the source returned</caption>{rows}</table>'
    shot = ""
    if i.get("image") is not None:
        data = base64.b64encode(i["image"].read_bytes()).decode("ascii")
        shot = (f'<figure class="shot"><div class="frame"><img alt="Screenshot for evidence {i["n"]}" '
                f'src="data:image/png;base64,{data}"></div><figcaption>Page capture'
                + (f', {e(i["shot_date"])}' if i.get("shot_date") else "")
                + (f' &middot; {e(_short(i["url"]))}' if i.get("url") else "")
                + "</figcaption></figure>")
    elif not i["facts"]:
        shot = '<p class="muted small">No page capture for this source.</p>'
    prov = ""
    if i.get("provenance"):
        def value(k: str, v: Any) -> str:
            if k == "Cited by":  # back to the claims, in the internal copy
                return ", ".join(f'<a href="#claim-{e(c)}">{e(c)}</a>' for c in str(v).split(", "))
            return e(str(v))
        prov = ('<dl class="prov">' + "".join(
            f"<div><dt>{e(k)}</dt><dd>{value(k, v)}</dd></div>"
            for k, v in i["provenance"].items() if v) + "</dl>")
    return f'<article class="ev" id="ev{i["n"]}">{head}{facts}{shot}{prov}</article>'


def _short(url: str, limit: int = 70) -> str:
    s = url.split("://", 1)[-1]
    return s if len(s) <= limit else s[:limit - 1] + "…"


CSS = """
:root{--ink:#0f1b2a;--muted:#5b6878;--line:#dfe4ea;--soft:#f4f6f9;--paper:#fff;
--brand:#13294b;--accent:#d9952b;--ok:#1f8a5b;--warn:#b7791f;--bad:#c0392b;--info:#5a6b85}
*{box-sizing:border-box}
html{background:#eef1f5}
body{margin:0;color:var(--ink);font:15px/1.6 "IBM Plex Sans","Segoe UI",system-ui,-apple-system,Arial,sans-serif;
-webkit-font-smoothing:antialiased}
.page{max-width:860px;margin:24px auto;background:var(--paper);padding:0 0 32px;
box-shadow:0 1px 3px rgba(15,27,42,.08),0 12px 40px rgba(15,27,42,.08);border-radius:10px;overflow:hidden}
.cover{background:linear-gradient(135deg,#0d1f3a 0%,#13294b 55%,#1d3d66 100%);color:#fff;padding:36px 44px 28px;
position:relative}
.cover:after{content:"";position:absolute;inset:auto 0 0 0;height:4px;background:var(--accent)}
.eyebrow{display:flex;gap:12px;align-items:center;font-size:12px;letter-spacing:.08em;text-transform:uppercase;
color:#b9c6d8}
.tag{display:inline-block;padding:3px 9px;border-radius:999px;font-weight:700;letter-spacing:.06em}
.tag-client{background:#e8f3ec;color:#145c3c}
.tag-internal{background:#fdecea;color:#9b2318}
.cover h1{font-size:30px;line-height:1.2;margin:14px 0 18px;font-weight:650;letter-spacing:-.01em}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px 24px;margin:0}
.meta div{min-width:0}
.meta dt{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#9fb0c6}
.meta dd{margin:2px 0 0;font-size:14px;overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:22px}
.stat{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:10px 12px}
.stat b{display:block;font-size:22px;line-height:1.1}
.stat span{font-size:12px;color:#b9c6d8}
.notice{margin:20px 44px 0;padding:10px 14px;border-radius:8px;font-size:13px}
.notice-internal{background:#fdecea;color:#7d1d14;border:1px solid #f3c2bc}
.notice-client{background:#eef5fb;color:#1d3d66;border:1px solid #cfe0f0}
h2{display:flex;align-items:baseline;gap:12px;margin:34px 44px 12px;font-size:19px;font-weight:650;
padding-bottom:8px;border-bottom:1px solid var(--line)}
h2 .num{font:600 12px/1 ui-monospace,"IBM Plex Mono",Consolas,monospace;color:var(--accent)}
.lead{margin:0 44px;font-size:16px;color:#253447}
.claims{list-style:none;margin:0 44px;padding:0;display:grid;gap:8px}
.claim{display:flex;gap:14px;align-items:flex-start;padding:12px 14px;border:1px solid var(--line);
border-left:4px solid var(--ok);border-radius:8px;background:var(--paper)}
.claim.st-unverified{border-left-color:var(--warn);background:#fffaf0}
.claim.st-leaked{border-left-color:var(--bad);background:#fff6f5}
.claim.st-inference{border-left-color:var(--info);background:var(--soft)}
.claim:target{outline:2px solid var(--accent)}
.claim-body{flex:1;min-width:0}
.claim p{margin:0;overflow-wrap:anywhere}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.pill{font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:999px;border:1px solid}
.st-verified.pill{color:var(--ok);border-color:#b8dfcb;background:#effaf4}
.st-unverified.pill{color:var(--warn);border-color:#f0d9a8;background:#fff8e8}
.st-leaked.pill{color:var(--bad);border-color:#f3c2bc;background:#fdecea}
.st-inference.pill{color:var(--info);border-color:#d3dae6;background:var(--soft)}
.sev-high{color:var(--bad);border-color:#f3c2bc}.sev-medium{color:var(--warn);border-color:#f0d9a8}
.sev-low,.sev-info{color:var(--info);border-color:#d3dae6}
.cid{font:12px ui-monospace,Consolas,monospace;color:var(--muted);background:var(--soft);padding:1px 6px;border-radius:4px}
.cites{display:flex;flex-wrap:wrap;gap:4px;justify-content:flex-end;max-width:120px}
.cite{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;padding:0 6px;
border-radius:6px;background:var(--brand);color:#fff;font-size:12px;font-weight:700;text-decoration:none}
.cite:hover{background:var(--accent);color:var(--ink)}
.steps{margin:0 44px;padding:0;list-style:none;display:grid;gap:6px}
.steps li{position:relative;padding:8px 12px 8px 36px;background:var(--soft);border-radius:8px}
.steps li:before{content:"";position:absolute;left:12px;top:13px;width:12px;height:12px;border:2px solid var(--muted);
border-radius:3px}
.evidence{margin:0 44px;display:grid;gap:14px}
.ev{border:1px solid var(--line);border-radius:10px;padding:14px 16px;scroll-margin-top:16px;break-inside:avoid-page}
.ev:target{border-color:var(--accent);box-shadow:0 0 0 3px rgba(217,149,43,.25)}
.ev header{display:flex;gap:12px;align-items:flex-start}
.evn{flex:none;display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;
border-radius:8px;background:var(--brand);color:#fff;font-weight:700}
.evh{min-width:0}
.evsrc{font-weight:650}
.evsub{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:13px;color:var(--muted)}
.evsub .q{font-family:ui-monospace,Consolas,monospace;color:var(--ink);overflow-wrap:anywhere}
.src-link{color:#1d5fa8;text-decoration:none;overflow-wrap:anywhere}
.src-link:hover{text-decoration:underline}
.facts{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px}
.facts caption{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
padding-bottom:4px}
.facts th{width:120px;text-align:left;vertical-align:top;font-weight:600;color:var(--muted);padding:5px 10px 5px 0;
border-top:1px solid var(--line)}
.facts td{padding:5px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
.shot{margin:12px 0 0}
.shot .frame{max-height:560px;overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--soft)}
.shot img{display:block;width:100%;height:auto}
.shot figcaption{font-size:12px;color:var(--muted);margin-top:6px}
.prov{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:6px 16px;margin:12px 0 0;
padding:10px 12px;background:#fbf7ef;border:1px dashed #e7cf9f;border-radius:8px;font-size:12px}
.prov dt{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em}
.prov dd{margin:0;font-family:ui-monospace,Consolas,monospace;overflow-wrap:anywhere}
.muted{color:var(--muted)}.small{font-size:12.5px;margin:10px 0 0}
.end{margin:36px 44px 0;padding-top:12px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);
text-align:center}
@media (max-width:640px){.cover{padding:28px 20px 22px}.stats{grid-template-columns:repeat(2,1fr)}
h2,.lead,.claims,.steps,.evidence,.notice,.end{margin-left:16px;margin-right:16px}.page{margin:0;border-radius:0}}
@media print{html,body{background:#fff}.page{margin:0;max-width:none;box-shadow:none;border-radius:0}
.cover{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.shot .frame{max-height:none;overflow:visible}.ev,.claim{break-inside:avoid}h2{break-after:avoid}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""
