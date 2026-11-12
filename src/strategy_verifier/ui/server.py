"""Verifier local UI (session H, part one).

A dependency-free local web front on the existing machine: the
server binds to loopback only, renders server-side HTML with the
boundary text fixed on every page, and calls the verifier functions
in process. Part one ships the shell, the status board, and the
front door as a form: a claim is composed in the browser, triaged
through the same refusal engine as the CLI, refusals render as cards
with their guidance, and an admissible claim can be saved as the
canonical TOML — the first piece of form-to-TOML. Nothing here adds
authority: the UI is a skin over commands that already exist, and
every boundary the product enforces in text it enforces here too.

Research diagnostics only. No orders, no advice, no trading paths.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    list_family_states,
    triage_claim_table,
)

_CSS_HEAD = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TITLE_SLOT · Verifier</title><style>
:root{--bg:#0f0f0f;--panel:#212121;--panel2:#272727;--hover:#3f3f3f;--line:#303030;--text:#f1f1f1;--muted:#aaa;--pass:#2ba640;--fail:#ff4e45;--warn:#f1c40f;--radius:10px} :root{--font-sans:Roboto,'Noto Sans KR',system-ui,Arial,sans-serif;--font-mono:'Roboto Mono',ui-monospace,Consolas,monospace}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font-sans)}button,input,textarea,select{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:280px minmax(520px,1fr) 340px;min-height:100vh;background:radial-gradient(50rem 40rem at 58% -30%,#162519 0%,transparent 64%),var(--bg)}.sidebar,.chat{background:#171717;border-color:var(--line);min-height:100vh}.sidebar{border-right:1px solid var(--line);padding:20px 14px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}.side-top{display:flex;justify-content:space-between;align-items:center}.logo{color:var(--text);background:none;border:0;padding:8px 9px;font-size:17px;font-weight:800;letter-spacing:.13em}.close{display:none}.sidebar nav{display:grid;gap:2px;margin-top:24px}.sidebar nav button{color:var(--muted);text-align:left;border:0;border-radius:7px;background:none;padding:10px 11px;font-size:14px}.sidebar nav button:hover,.sidebar nav button.active{background:#2a2a2a;color:#fff}.sidebar nav button.active{box-shadow:inset 3px 0 var(--pass)}.technique{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:15px;margin-top:24px}.micro{margin:0;color:var(--muted);font:10px var(--font-mono);letter-spacing:.08em}.technique h2{font:600 15px var(--font-mono);margin:9px 0}.badge-line{display:flex;flex-wrap:wrap;gap:6px}.technique code{display:block;color:var(--muted);font:11px var(--font-mono);margin:12px 0}.secondary,.primary{border:1px solid #4c4c4c;border-radius:7px;padding:9px 11px;background:#343434;color:#eee;font-size:12px;font-weight:700}.secondary:hover{background:var(--hover)}.technique .secondary{width:100%}.condition-dots{display:grid;gap:8px;margin-top:16px;color:#c7c7c7;font-size:11px}.condition-dots i{display:inline-block;width:6px;height:6px;border-radius:999px;margin-right:6px}.yes{background:var(--pass)}.no{background:#777}.boundary{margin-top:auto;padding:18px 6px 2px;color:#777;font-size:10px;line-height:1.5}.main{min-width:0;padding:34px 30px}.workspace,.secondary-view{width:min(100%,900px);margin:auto}.work-head{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}.work-head h1,.secondary-view h1{font-size:27px;letter-spacing:-.04em;margin:5px 0 0}.verdict-picker{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:10px}.verdict-picker button{border:1px solid #3b3b3b;border-radius:4px;background:#1c1c1c;color:#aaa;padding:5px 7px;font-size:10px}.verdict-picker button.selected{border-color:#ddd;color:#fff}.chart-top{height:45px;background:var(--panel);border:1px solid var(--line);border-bottom:0;border-radius:var(--radius) var(--radius) 0 0;display:flex;align-items:center;gap:9px;padding:0 14px;color:var(--muted);font:11px var(--font-mono)}.chart-top select{background:#151515;border:1px solid #424242;border-radius:5px;color:#eee;padding:5px 7px}.live-dot{width:7px;height:7px;border-radius:50%;background:var(--pass);box-shadow:0 0 0 3px #2ba64025;margin-left:8px}.connected{margin-left:auto;color:#8cc997}.adjudication{border-left:4px solid;padding:11px 15px;font-size:12px;line-height:1.45}.adjudication.unadjudicated{background:#463b16;border-color:var(--warn);color:#ffedab}.adjudication.not{background:#43201f;border-color:var(--fail);color:#ffc0bb}.adjudication.confirmed{background:#193b20;border-color:var(--pass);color:#c2f4cb}.chart-wrap{height:360px;position:relative;background:#181818;border:1px solid var(--line);border-top:0;overflow:hidden}.chart-wrap svg{width:100%;height:100%;display:block}.grid{stroke:#2c2c2c;stroke-width:1}.wick{stroke:#999;stroke-width:1.4}.bar.up{fill:#9a9a9a}.bar.down{fill:#4f4f4f}.occurrence path{fill:#181818;stroke:#dedede;stroke-width:1.5}.occurrence text{fill:#eee;font:10px var(--font-mono);text-anchor:middle}.cluster rect{fill:#3a3a3a}.cluster text{fill:#eee;font:10px var(--font-mono);text-anchor:middle}.chart-tooltip{position:absolute;top:42%;left:44%;width:194px;background:#292929;border:1px solid #555;border-radius:8px;padding:12px;box-shadow:0 14px 30px #0008;font-size:11px;line-height:1.45}.chart-tooltip>code{display:block;color:#bbb;margin-bottom:8px}.chart-tooltip p{margin:8px 0 0;color:#bbb}.latest{display:flex;align-items:center;gap:7px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-top:0;padding:14px;border-radius:0 0 var(--radius) var(--radius);font-size:11px;color:#bbb}.latest .micro{margin-right:4px}.work-footer{padding:18px 3px 0;color:#aaa;text-align:center;font-size:11px}.badge{display:inline-flex;align-items:center;width:max-content;border-radius:5px;padding:3px 6px;background:#383838;color:#ddd;font:10px var(--font-mono);white-space:nowrap}.badge.confirmed,.badge.pass,.badge.favorable,.badge.yes{color:#bdf2c6;background:#183c20}.badge.not,.badge.refusal,.badge.adverse,.badge.no{color:#ffbeb8;background:#48201e}.badge.unadjudicated,.badge.calibrated{color:#ffeb9e;background:#493c16}.chat{border-left:1px solid var(--line);padding:19px 14px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column}.chat header{display:flex;justify-content:space-between;gap:8px;padding:0 4px 16px;border-bottom:1px solid var(--line)}.chat h2{font-size:15px;margin:0}.chat header p{margin:5px 0 0;color:var(--muted);font-size:11px;line-height:1.4}.chat header button{border:0;background:transparent;color:#aaa;font-size:20px}.messages{overflow:auto;padding:15px 4px;display:grid;gap:10px}.user{justify-self:end;max-width:84%;margin:0;background:#3b3b3b;border-radius:10px 10px 2px 10px;padding:9px 10px;font-size:12px;line-height:1.45}.system{background:var(--panel);border:1px solid var(--line);border-radius:9px 9px 9px 2px;padding:10px;color:#d5d5d5;font-size:11px;line-height:1.5}.quick{display:flex;flex-wrap:wrap;gap:5px;padding:8px 2px}.quick button{border:1px solid #414141;border-radius:999px;background:transparent;color:#ccc;padding:6px 8px;font-size:10px}.chat form{display:flex;gap:6px;padding:10px 1px 0;border-top:1px solid var(--line)}.chat form input{min-width:0;width:100%;background:#252525;border:1px solid #454545;border-radius:7px;color:#eee;padding:8px;font-size:11px}.chat form button{background:#ddd;color:#111;border:0;border-radius:7px;padding:0 10px;font-size:11px}.chat-fab,.mobile-head{display:none}.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:20px}.secondary-view>h1{margin-bottom:24px}.table{padding:0;overflow:hidden}.t-head,.t-row{display:grid;grid-template-columns:1.1fr .7fr .9fr 2.1fr;gap:14px;align-items:center;padding:12px 18px}.t-head{color:var(--muted);font:10px var(--font-mono)}.t-row{min-height:62px;border-top:1px solid var(--line);font-size:12px}.t-row code{color:#ddd;font:11px var(--font-mono)}.t-row>div{display:flex;gap:5px}.t-row input,.t-row select{min-width:0;background:#181818;border:1px solid #454545;border-radius:5px;padding:6px;color:#ddd;font-size:11px}.split{display:grid;grid-template-columns:minmax(300px,.95fr) minmax(280px,1.05fr);gap:18px;align-items:start}.form{display:grid;gap:2px}.field{display:grid;gap:6px;margin-bottom:12px;color:#ddd;font-size:12px}.field>span{color:var(--muted);font:10px var(--font-mono)}.field input,.field textarea,.field select{width:100%;background:#151515;border:1px solid #414141;border-radius:6px;padding:9px;color:#eee;outline:none}.field textarea{resize:vertical;min-height:80px}.checks{display:grid;grid-template-columns:1fr 1fr;gap:7px;padding:8px;background:#181818;border:1px solid #414141;border-radius:6px}.checks label,.capture-list label{font:11px var(--font-mono)}input[type=checkbox]{accent-color:var(--pass);width:auto!important}.condition-rows{display:grid;gap:7px;margin:4px 0 14px}.condition-rows>div{display:flex;gap:5px}.condition-rows>div button{width:32px;background:#343434;color:#ddd;border:0;border-radius:6px}.primary{background:var(--pass);border-color:var(--pass);color:#061008}.primary:hover{background:#47bd5e}.result{display:flex;gap:11px;border-left:4px solid}.result.pass{border-left-color:var(--pass)}.result.refusal{border-left-color:var(--fail)}.result.running{border-left-color:#aaa}.result h2{font-size:17px;margin:2px 0 9px}.result p{font-size:12px;line-height:1.45;color:#d0d0d0}.result pre{font:11px/1.65 var(--font-mono);white-space:pre-wrap;color:#cfcfcf}.spinner{width:17px;height:17px;border:2px solid #777;border-top-color:#eee;border-radius:50%;animation:spin .9s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.capture-list{max-height:125px;overflow:auto;display:grid;gap:8px;padding:9px;background:#171717;border:1px solid #414141;border-radius:6px}.toggle{font-size:12px}.docs{display:grid;gap:10px}.docs a{display:flex;justify-content:space-between;align-items:center;text-decoration:none;color:#eee;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px}.docs a:hover{background:var(--hover)}.docs span{color:var(--muted);font-size:12px}
@media(max-width:1120px){.app{grid-template-columns:240px minmax(480px,1fr)}.chat{position:fixed;right:0;top:0;width:340px;z-index:10;transform:translateX(100%);transition:transform .2s}.chat.open{transform:translateX(0)}.chat-fab{display:block;position:fixed;right:18px;bottom:18px;border:1px solid #555;border-radius:99px;background:#eee;color:#111;padding:10px 14px;font-size:12px;font-weight:700;z-index:3}}
@media(max-width:720px){.app{display:block}.sidebar{position:fixed;z-index:20;left:0;width:280px;transform:translateX(-100%);transition:transform .2s}.sidebar.open{transform:translateX(0)}.close{display:block;background:none;border:0;color:#aaa;font-size:24px}.main{padding:0 14px 30px}.mobile-head{height:53px;display:flex;align-items:center;justify-content:space-between;margin:0 -14px 24px;padding:0 15px;border-bottom:1px solid var(--line);background:#141414}.mobile-head button{color:#ddd;background:transparent;border:0;font-size:12px}.mobile-head b{letter-spacing:.11em;font-size:13px}.work-head{align-items:start}.work-head h1,.secondary-view h1{font-size:24px}.verdict-picker{display:none}.chart-wrap{height:300px}.chart-top{border-radius:8px 8px 0 0}.latest{align-items:flex-start}.chat{width:min(100%,340px)}.split{grid-template-columns:1fr}.t-head{display:none}.t-row{grid-template-columns:1fr 1fr;padding:13px}.t-row>div{grid-column:1/-1}.t-row>div input{flex:1}.table{overflow:auto}.adjudication{font-size:11px}.work-footer{padding:15px 8px 0}.checks{grid-template-columns:1fr}}
/* NinjaTrader-inspired platform override */
:root{--bg:#1e1e1e;--panel:#252526;--panel2:#2d2d30;--line:#3f3f46;--text:#e8e8e8;--muted:#9a9a9a;--radius:1px}
body{font-size:12px}.platform{min-height:100vh;background:var(--bg)}.toolbar{height:36px;display:flex;align-items:center;gap:8px;background:#2d2d30;border-bottom:1px solid var(--line);padding:0 10px;white-space:nowrap}.toolbar>b{font-size:13px;letter-spacing:.1em}.tool-split{height:20px;border-left:1px solid #555}.toolbar select{height:24px;background:#1d1d1d;color:#ddd;border:1px solid #4a4a4a;border-radius:0;padding:0 4px;font:11px var(--font-mono)}.toolbar .live-dot{margin:0;width:6px;height:6px}.connection{color:#aaa;font:11px var(--font-mono)}.toolbar nav{display:flex;margin-left:auto;height:100%;align-items:stretch}.toolbar nav button{background:transparent;border:0;border-left:1px solid #3c3c40;color:#aaa;font-size:11px;padding:0 9px}.toolbar nav button.active,.toolbar nav button:hover{color:#fff;background:#1e1e1e;box-shadow:inset 0 -2px var(--pass)}.app{grid-template-columns:260px minmax(520px,1fr) 320px;min-height:calc(100vh - 36px);background:var(--bg)}.sidebar,.chat{background:var(--panel);border-radius:0}.sidebar{height:calc(100vh - 36px);padding:0;border-right:1px solid var(--line)}.side-top{height:29px;background:var(--panel2);border-bottom:1px solid var(--line);padding:0 8px}.logo{font:11px var(--font-mono);letter-spacing:.08em;padding:0}.sidebar nav{margin:7px 6px;gap:0}.sidebar nav button{border-radius:0;padding:7px 9px;font-size:12px}.sidebar nav button.active{box-shadow:inset 2px 0 var(--pass)}.technique{margin:6px;border:1px solid var(--line);border-radius:0;padding:10px;background:#202021}.technique:before,.chat header:before,.detections header:before{content:"TECHNIQUE";display:block;background:var(--panel2);margin:-10px -10px 10px;padding:7px 9px;border-bottom:1px solid var(--line);font:10px var(--font-mono);letter-spacing:.1em;color:#cfcfcf}.technique h2{font-size:13px}.badge,.secondary,.primary,.card,.field input,.field textarea,.field select,.checks,.capture-list,.chart-tooltip,.quick button,.chat form input,.chat form button,.docs a{border-radius:0}.condition-dots{font-size:10px}.boundary{font-size:10px;padding:8px 10px}.main{padding:12px;min-height:calc(100vh - 36px)}.workspace,.secondary-view{width:100%;max-width:none}.work-head{margin-bottom:8px}.work-head h1{font:600 16px var(--font-mono)}.work-head .micro{font-size:9px}.chart-wrap{height:calc(100vh - 360px);min-height:310px;border-radius:0}.adjudication{border-left-width:2px;padding:7px 9px;font:11px var(--font-mono)}.match-toast{position:absolute;right:8px;top:8px;width:218px;background:#2d2d30;border:1px solid #666;border-left:2px solid var(--fail);padding:7px;font:10px/1.4 var(--font-mono);color:#eee}.latest{border-radius:0;padding:8px;gap:5px}.work-footer{background:#202021;border:1px solid var(--line);border-top:0;padding:8px;text-align:left;font:11px var(--font-mono)}.detections{margin-top:8px;border:1px solid var(--line);background:#202021}.detections header{height:29px;display:flex;align-items:center;justify-content:space-between;background:var(--panel2);padding:0 8px;border-bottom:1px solid var(--line);font:10px var(--font-mono);letter-spacing:.09em}.detections header:before{display:none}.detections header button{background:transparent;border:1px solid #555;color:#ccc;font-size:10px;padding:3px 5px;margin-left:4px}.detect-head,.detect-row{display:grid;grid-template-columns:138px 70px 35px 33px minmax(210px,1fr) 105px;gap:6px;align-items:center;padding:6px 8px}.detect-head{color:#999;font:9px var(--font-mono);border-bottom:1px solid var(--line)}.detect-row{border-bottom:1px solid #333;font:10px var(--font-mono)}.detect-row:last-child{border:0}.detect-row code{font:10px var(--font-mono);color:#d2d2d2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.chat{height:calc(100vh - 36px);padding:0;border-left:1px solid var(--line)}.chat header{height:56px;padding:8px;border-bottom:1px solid var(--line)}.chat header:before{content:"ASK THE RECORD";position:absolute;top:0;left:0;right:0;height:25px;margin:0;padding:7px 9px;font:10px var(--font-mono);background:var(--panel2)}.chat header>div{padding-top:20px}.chat h2{font-size:0}.chat header p{font-size:10px}.chat header button{z-index:1}.messages{padding:8px;gap:7px}.user,.system{border-radius:0;font-size:11px;padding:8px}.quick{padding:7px}.quick button{font-size:9px}.secondary-view h1{font:600 18px var(--font-mono);margin:4px 0 12px}.card,.table{border-radius:0;padding:12px}.table{padding:0}.t-head,.t-row{padding:8px 10px}.result{border-left-width:2px}.result h2{font-size:14px}.docs a{padding:10px}.mobile-head{display:none}
@media(max-width:1120px){.toolbar nav{display:none}.app{grid-template-columns:260px minmax(480px,1fr)}.chat{top:36px;height:calc(100vh - 36px)}.chat-fab{border-radius:0;bottom:10px;right:10px}.chart-wrap{height:430px}}
@media(max-width:720px){.toolbar{display:none}.app{min-height:100vh}.sidebar{height:100vh}.main{padding:0 8px}.mobile-head{display:flex;height:34px;margin:0 -8px 8px;padding:0 8px;background:var(--panel2);border-bottom:1px solid var(--line)}.chart-wrap{height:310px;min-height:0}.chat{top:0;height:100vh}.detections{overflow:auto}.detect-head,.detect-row{min-width:630px}.match-toast{display:none}.work-head{margin:6px 0}.latest{font-size:10px}.chart-tooltip{left:35%;top:40%}}
.condition-rows>div input{flex:1;min-width:0}.form>button.secondary{justify-self:start}.axis{fill:#8a8a8a;font:9px var(--font-mono)}
/* ===== Glass FUI (layer three, final cascade) ===== */
:root{--bg:#E8E6DE;--ambient1:#A9B5A8;--ambient2:#2E4A44;--accent:#C2591F;--glass:#1A1D1Cea;--glass2:#141716;--hair:#6E7A74;--ink:#20241F;--paper:#DEDBD2;--pass:#3E5C3A;--fail:#B5432A;--warn:#C9A227;--font-sans:'Rajdhani','Saira SemiCondensed','Arial Narrow',system-ui,sans-serif}
body{background:#E8E6DE;background-image:radial-gradient(52rem 40rem at 82% 6%,#D96C3B66,transparent 60%),radial-gradient(46rem 38rem at 8% 92%,#2E4A4455,transparent 62%),linear-gradient(118deg,#9FAF9E 0 34%,transparent 34.2%),linear-gradient(298deg,#2E4A44f0 0 23%,transparent 23.2%),linear-gradient(62deg,transparent 0 74%,#C2591Fee 74.2% 88%,transparent 88.2%),linear-gradient(244deg,transparent 0 66%,#8FA08Fdd 66.2%);background-attachment:fixed;color:var(--ink)}
.toolbar{background:#E8E6DE73;backdrop-filter:blur(18px) saturate(120%);-webkit-backdrop-filter:blur(18px) saturate(120%);border-bottom:1px solid #ffffff66;color:var(--ink);box-shadow:inset 0 1px 0 #ffffffaa}
.toolbar>b{color:var(--ink);letter-spacing:.18em}
.toolbar select{background:#F4F2EA;color:var(--ink);border-color:#B9B6AC}
.connection{color:#5c6157}
.toolbar nav button{color:#5c6157;border-left-color:#C9C6BC}
.toolbar nav button.active,.toolbar nav button:hover{color:var(--ink);background:#F4F2EA;box-shadow:inset 0 -2px var(--accent)}
.app{background:transparent}
.sidebar{background:#E8E6DE7a;backdrop-filter:blur(18px) saturate(120%);-webkit-backdrop-filter:blur(18px) saturate(120%);border-right:1px solid #ffffff66;box-shadow:inset 0 1px 0 #ffffffaa}
.side-top{background:#D3D0C6;border-bottom:1px solid #B9B6AC}
.logo{color:var(--ink)}
.sidebar nav button{color:#4c5148}
.sidebar nav button:hover,.sidebar nav button.active{background:#EFEDE4;color:var(--ink)}
.sidebar nav button.active{box-shadow:inset 2px 0 var(--accent)}
.boundary{color:#6a6f64}
.micro{color:#7a7f74}
h1,.work-head h1,.secondary-view h1{color:var(--ink);text-transform:uppercase;letter-spacing:.06em}
.card,.technique,.table{background:#141716a8;backdrop-filter:blur(16px) saturate(130%);-webkit-backdrop-filter:blur(16px) saturate(130%);border:1px solid #ffffff2e;box-shadow:inset 0 1px 0 #ffffff30,inset 0 0 24px #ffffff07,0 20px 44px #1a1d1c40;color:#E9E7DF;position:relative;clip-path:polygon(0 0,44% 0,48% 13px,100% 13px,100% 100%,0 100%);padding-top:26px}
.card:before,.technique:before,.table:before{content:"";position:absolute;top:4px;left:6px;width:16px;height:2px;background:var(--accent)}
.card:after,.technique:after,.table:after{content:"";position:absolute;bottom:4px;right:6px;width:16px;height:2px;background:var(--accent)}
.technique:before{content:"";margin:0;padding:0;border:0;background:var(--accent);height:2px;width:16px}
.technique{color:#E4E2DA}
.technique h2{color:#F0EEE6}
.technique code{color:#9aa79b}
.micro{letter-spacing:.12em}
.card .micro,.technique .micro,.table .micro{color:#93a094}
.field>span{color:#93a094}
.field input,.field textarea,.field select{background:#10131288;border:1px solid #4b554e;color:#EDEBE3}
.checks,.capture-list{background:#10131266;border-color:#4b554e;color:#dcdad2}
.t-head{color:#93a094}
.t-row{border-top:1px solid #333a35;color:#dcdad2}
.t-row code{color:#e8e6de}
.t-row input,.t-row select{background:#101312aa;border-color:#4b554e;color:#EDEBE3}
.badge{background:#2a302b;color:#cfd6cd}
.badge.confirmed,.badge.pass,.badge.favorable,.badge.yes{background:var(--pass);color:#EDF3E8}
.badge.not,.badge.refusal,.badge.adverse,.badge.no{background:var(--fail);color:#FBE9E2}
.badge.unadjudicated,.badge.calibrated{background:var(--warn);color:#241D06}
.primary{background:var(--accent);border-color:var(--accent);color:#FCF7EF}
.primary:hover{background:#D96C3B}
.secondary{background:#10131266;border:1px solid #7b857d;color:#E4E2DA}
.secondary:hover{background:#22282366}
.adjudication{border:0;color:#FCF7EF;letter-spacing:.04em;clip-path:polygon(0 0,100% 0,100% 100%,0 100%)}
.adjudication.unadjudicated{background:var(--warn);color:#241D06}
.adjudication.not{background:var(--fail)}
.adjudication.confirmed{background:var(--pass)}
.chart-wrap{background:#101312cf;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid #ffffff26;box-shadow:inset 0 1px 0 #ffffff22,0 18px 40px #1a1d1c40}
.grid{stroke:#2a2f2b}
.wick{stroke:#8f9a90}
.bar.up{fill:#b9c2b6}.bar.down{fill:#55605a}
.occurrence path{fill:none;stroke:#E8E6DE;stroke-width:1.4}
.occurrence text{fill:#E8E6DE}
.axis{fill:#7d877e}
.latest{background:#141716a0;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid #ffffff26;box-shadow:inset 0 1px 0 #ffffff26;color:#d6dcd3}
.work-footer{background:#E8E6DE66;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid #ffffff5c;color:#3f443b}
.match-toast{background:#E8E6DE8c;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border:1px solid #ffffff70;border-left:2px solid var(--fail);color:var(--ink);box-shadow:0 12px 30px #1a1d1c33}
.detections{background:#E8E6DE70;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid #ffffff66;box-shadow:inset 0 1px 0 #ffffffaa,0 14px 34px #1a1d1c26;color:var(--ink)}
.detections header{background:#D3D0C6;border-bottom:1px solid #B9B6AC;color:#3c4038}
.detect-head{color:#7a7f74;border-bottom:1px solid #C9C6BC}
.detect-row{border-bottom:1px solid #D6D3C9;border-left:2px solid var(--accent)}
.detect-row code{color:#2c302a}
.chat{background:#E8E6DE73;backdrop-filter:blur(18px) saturate(120%);-webkit-backdrop-filter:blur(18px) saturate(120%);border-left:1px solid #ffffff66;box-shadow:inset 0 1px 0 #ffffffaa}
.chat header{border-bottom:1px solid #B9B6AC}
.chat header:before{background:#D3D0C6;color:#3c4038}
.chat header p{color:#6a6f64}
.messages .user{background:#2E4A44;color:#EDEFE9}
.messages .system{background:#141716b0;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid #ffffff2a;box-shadow:inset 0 1px 0 #ffffff26;color:#E4E2DA}
.quick button{border-color:#9a978d;color:#4c5148}
.chat form input{background:#F4F2EA;border-color:#B9B6AC;color:var(--ink)}
.chat form button{background:var(--accent);color:#FCF7EF}
.result{background:transparent}
.result.pass{border-left-color:var(--pass)}
.result.refusal{border-left-color:var(--fail)}
.result p,.result pre{color:#D8D6CE}
.docs a{background:#141716a8;backdrop-filter:blur(16px) saturate(130%);-webkit-backdrop-filter:blur(16px) saturate(130%);border:1px solid #ffffff2e;box-shadow:inset 0 1px 0 #ffffff30,0 16px 36px #1a1d1c3a;color:#E9E7DF;clip-path:polygon(0 0,38% 0,42% 12px,100% 12px,100% 100%,0 100%);padding-top:22px}
.docs a:hover{background:#232824ea}
.docs span{color:#93a094}
.spinner{border-color:#7b857d;border-top-color:#E8E6DE}
.mobile-head{background:#E8E6DE73;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-bottom:1px solid #ffffff66}
.mobile-head button,.mobile-head b{color:var(--ink)}

/* ===== Glass FUI r2 calibration (from the round-two export) ===== */
html{overflow-x:hidden}
body.platform{background:#E8E6DE;background-image:radial-gradient(ellipse 55% 43% at 91% 8%,rgba(194,89,31,.38),transparent 62%),radial-gradient(ellipse 56% 48% at 8% 90%,rgba(46,74,68,.44),transparent 67%);background-attachment:fixed}
.geo{position:fixed;pointer-events:none;z-index:0}
.geo.one{width:68vw;height:67vw;top:-29vw;left:-25vw;background:#2E4A44;clip-path:polygon(0 0,100% 42%,42% 100%)}
.geo.two{width:55vw;height:70vw;right:-25vw;top:-13vw;background:#9FAF9E;clip-path:polygon(42% 0,100% 0,100% 100%,0 52%)}
.geo.three{width:46vw;height:46vw;bottom:-28vw;left:21vw;background:#C2591F;clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%)}
.toolbar,.app{position:relative;z-index:1}
.toolbar{background:rgba(232,230,222,.52);border:1px solid rgba(255,255,255,.40);border-top:0;box-shadow:inset 0 1px rgba(255,255,255,.65)}
.toolbar>b:before{content:"";display:inline-block;width:13px;height:15px;margin-right:6px;vertical-align:-2px;clip-path:polygon(25% 3%,75% 3%,100% 50%,75% 97%,25% 97%,0 50%);background:#C2591F}
.toolbar nav button.active,.toolbar nav button:hover{box-shadow:inset 0 -2px #B5432A;background:transparent}
.sidebar,.chat,.detections,.match-toast,.mobile-head{background:rgba(232,230,222,.52)}
.card,.technique,.table,.docs a{clip-path:none;background:rgba(20,23,22,.66);backdrop-filter:blur(16px) saturate(1.12);-webkit-backdrop-filter:blur(16px) saturate(1.12);border:1px solid rgba(255,255,255,.18);box-shadow:inset 0 1px rgba(255,255,255,.19),0 20px 44px rgba(26,29,28,.25);padding-top:16px;margin-top:24px;overflow:visible}
.card:before,.technique:before,.table:before,.docs a:before{content:"";position:absolute;top:-1px;left:17px;transform:translateY(-100%);width:92px;height:22px;background:rgba(20,23,22,.66);backdrop-filter:blur(16px) saturate(1.12);-webkit-backdrop-filter:blur(16px) saturate(1.12);border:1px solid rgba(255,255,255,.18);border-bottom:0;margin:0;padding:0}
.card:after,.technique:after,.table:after,.docs a:after{content:"";position:absolute;left:-1px;top:12px;width:10px;height:2px;background:#C2591F;bottom:auto;right:auto}
.chart-wrap{background:rgba(16,19,18,.81);border:0;border-top:1px solid #59635D}
.bar.up{fill:#91A391}.bar.down{fill:#59635D}
.wick{stroke:#7d8a80}
.latest{background:rgba(20,23,22,.6)}
.latest span,.latest .badge{border:1px solid #68736E}
.adjudication b{font-weight:700;letter-spacing:.07em}
.messages .user{border:1px solid rgba(255,255,255,.18)}
.primary{background:#C2591F;border-color:#C2591F}
.primary:hover{background:#D96C3B}
.detect-row{border-left:2px solid #C2591F}
@media(max-width:800px){.geo.one{width:105vw;height:95vw;top:-44vw;left:-38vw}.geo.two{width:90vw;right:-50vw}.geo.three{width:95vw;bottom:-56vw;left:0}}

/* ===== interaction feel (buttons, chips, inputs) ===== */
button,.qc,.quick button{transition:transform .07s ease,box-shadow .14s ease,background .12s ease,filter .1s ease,border-color .12s ease;will-change:transform}
.primary:hover,.secondary:hover,.qc:hover,.quick button:hover,.chat form button:hover{transform:translateY(-1px)}
.primary:hover{box-shadow:0 6px 16px rgba(194,89,31,.38)}
.secondary:hover,.qc:hover,.quick button:hover{box-shadow:0 5px 14px rgba(26,29,28,.22)}
.primary:active,.secondary:active,.qc:active,.quick button:active,.chat form button:active{transform:translateY(1px) scale(.985);box-shadow:0 1px 3px rgba(26,29,28,.28)}
.primary:active{filter:brightness(1.12)}
.secondary:active{background:rgba(233,231,223,.14)}
.toolbar nav button,.sidebar nav button{transition:box-shadow .12s ease,background .12s ease,color .12s ease}
.toolbar nav button:active,.sidebar nav button:active{background:rgba(194,89,31,.12)}
button:focus-visible,.qc:focus-visible{outline:1px solid #C2591F;outline-offset:2px}
.field input:focus,.field textarea:focus,.field select:focus,.chat form input:focus,.verdict-picker select:focus{border-color:#C2591F;box-shadow:0 0 0 2px rgba(194,89,31,.22);outline:none}
@media(prefers-reduced-motion:reduce){button,.qc,.quick button{transition:none}.primary:hover,.secondary:hover,.qc:hover,.quick button:hover,.primary:active,.secondary:active,.qc:active,.quick button:active{transform:none}}

/* ===== collapsible side panels ===== */
.app{grid-template-columns:var(--colL,260px) minmax(0,1fr) var(--colR,320px)}
.app.no-chat{--colR:0px}
body.hide-left .app{--colL:0px}
body.hide-right .app{--colR:0px}
body.hide-left .sidebar{display:none}
body.hide-right .chat{display:none}
.edge-toggle{position:fixed;top:50%;transform:translateY(-50%);z-index:6;width:18px;height:58px;padding:0;background:rgba(20,23,22,.66);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.22);color:#E8E6DE;font-size:11px;cursor:pointer;line-height:56px}
.edge-toggle.left{left:0;border-left:0}
.edge-toggle.right{right:0;border-right:0;display:none}
body.has-chat .edge-toggle.right{display:block}
.edge-toggle:hover{background:rgba(20,23,22,.82)}
@media(max-width:720px){.edge-toggle{display:none!important}}

/* ===== futuristic click pulse (hexagon) ===== */
button:not(.edge-toggle){position:relative;overflow:hidden}
.fx-hex{position:absolute;pointer-events:none;clip-path:polygon(25% 3%,75% 3%,100% 50%,75% 97%,25% 97%,0 50%);background:rgba(232,230,222,.38);mix-blend-mode:screen;transform:scale(.15);opacity:.95;animation:fxhex .5s cubic-bezier(.2,.7,.3,1) forwards}
.primary .fx-hex{background:rgba(255,214,180,.6)}
.quick .fx-hex,.toolbar .fx-hex,.sidebar .fx-hex,.mobile-head .fx-hex,.chat form .fx-hex{background:rgba(46,74,68,.32);mix-blend-mode:multiply}
@keyframes fxhex{to{transform:scale(2.3);opacity:0}}
@media(prefers-reduced-motion:reduce){.fx-hex{display:none}}

/* ===== sweep five: chat header, banner air, chip labels, clock ===== */
.chat header{position:relative;display:block;padding:38px 12px 10px}
.chat header:before{position:absolute;top:0;left:0;right:0;height:30px;line-height:30px;padding:0 12px}
.chat header h2{display:none}
.chat header p{margin:0;font-size:11px;color:#5d6259}
.adjudication{padding:10px 14px;line-height:1.5}
.latest .micro{border:0;padding-left:0}
.tool-clock{margin-left:auto;font:11px var(--font-mono);color:#4c5148;letter-spacing:.08em;white-space:pre}
.toolbar{display:flex;align-items:center;gap:14px}
@media(max-width:720px){.tool-clock{display:none}}

/* ===== status folder-tile grid ===== */
.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:30px 18px;margin-top:4px}
.fam-tile{padding:16px 14px 34px}
.fam-tile h2{margin:0 0 9px;font:600 16px var(--font-sans);letter-spacing:.07em;text-transform:uppercase;color:#F0EEE6}
.meta-rows{display:grid;gap:4px;margin-bottom:6px}
.meta-rows code{font:10.5px var(--font-mono);color:#9aa79b}
.meta-rows .badge{margin-left:6px;font-size:9.5px}
.tile-chip{position:absolute;right:10px;bottom:9px;font:9.5px var(--font-mono);background:rgba(16,19,18,.6);border:1px solid #4b554e;color:#cfd6cd;padding:2px 6px;letter-spacing:.06em}
.tile-adv{display:grid;gap:6px;margin-top:6px;padding-top:10px;border-top:1px solid #333a35}
.tile-adv select,.tile-adv input{background:rgba(16,19,18,.55);border:1px solid #4b554e;color:#EDEBE3;padding:5px 6px;font:11px var(--font-mono)}

/* ===== panel grid fix + toolbar panel buttons + transitions ===== */
body.hide-left:not(.hide-right) .app{grid-template-columns:minmax(0,1fr) var(--colR,320px)}
body.hide-left:not(.hide-right) .app.no-chat{grid-template-columns:minmax(0,1fr)}
body.hide-right:not(.hide-left) .app{grid-template-columns:var(--colL,260px) minmax(0,1fr)}
body.hide-left.hide-right .app{grid-template-columns:minmax(0,1fr)}
.edge-toggle{display:none!important}
.panel-btn{display:inline-flex;align-items:center;justify-content:center;width:28px;height:22px;padding:0;background:transparent;color:#4c5148;border:0;border-radius:0;cursor:pointer}
.panel-btn:hover{background:#F4F2EA;color:#20241F}
.panel-btn:active{background:#E9E6DC}
.panel-btn.off{color:#A8AC9F}
.panel-btn:focus{outline:none}
.panel-btn:focus-visible{outline:1px solid #C2591F;outline-offset:2px}
.panel-btn.right{display:none;margin-left:10px}
body.has-chat .panel-btn.right{display:inline-flex}
.panel-btn.left{margin-right:2px}
@media(max-width:720px){.panel-btn{display:none!important}}
@view-transition{navigation:auto}
::view-transition-old(root){animation:vtout .16s ease forwards}
::view-transition-new(root){animation:vtin .28s cubic-bezier(.2,.7,.3,1) forwards}
@keyframes vtout{to{opacity:0;filter:blur(5px)}}
@keyframes vtin{from{opacity:0;transform:scale(.992);filter:blur(7px) saturate(1.25)}to{opacity:1;transform:none;filter:none}}
.main>section{animation:enter .26s cubic-bezier(.2,.7,.3,1)}
@keyframes enter{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){::view-transition-old(root),::view-transition-new(root),.main>section{animation:none}}

/* ===== chat column lock + FUI cursor + hover targeting ===== */
.chat{display:flex;flex-direction:column;min-height:0}
.chat header{position:relative;flex:0 0 auto;display:block;margin:0;padding:36px 12px 10px}
.chat header:before{position:absolute;top:0;left:0;right:0;height:30px;line-height:30px;padding:0 12px}
.messages{flex:1 1 auto;min-height:0;overflow-y:auto;margin:0;padding:10px 12px}
.chat .quick,.chat form{flex:0 0 auto}
html,body{cursor:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='22'><path d='M2 2 L13 9 L6 14 Z' fill='%233C4038'/><path d='M18.5 16 L15.75 11.24 L10.25 11.24 L7.5 16 L10.25 20.76 L15.75 20.76 Z' fill='none' stroke='%233C4038' stroke-width='1.3'/></svg>") 2 2, auto}
button,a,select,label,.qc{cursor:inherit}
.card,.fam-tile,.docs a{transition:border-color .16s ease,box-shadow .2s ease,transform .16s ease}
.card:after,.technique:after,.docs a:after{transition:width .18s ease}
.card:hover,.docs a:hover{border-color:#ffffff42;box-shadow:inset 0 1px rgba(255,255,255,.3),0 24px 52px rgba(26,29,28,.34)}
.fam-tile:hover,.docs a:hover{transform:translateY(-2px)}
.card:hover:after,.docs a:hover:after{width:22px}
.detect-row{transition:background .12s ease}
.detect-row:hover{background:#EFEDE4}
.t-row{transition:background .12s ease}
.t-row:hover{background:#1c211e}
@media(prefers-reduced-motion:reduce){.card,.fam-tile,.docs a{transition:none}.fam-tile:hover,.docs a:hover{transform:none}}

/* ===== polish 10: LexOS assembly — cascade, sweep, scan ===== */
.main>section{animation:none}
@keyframes fuiIn{from{opacity:0;transform:translateY(9px);filter:blur(7px)}70%{filter:blur(0)}to{opacity:1;transform:none;filter:none}}
.main .card,.main .table,.docs a,.result{animation:fuiIn .34s cubic-bezier(.2,.7,.3,1) backwards}
.secondary-view>*:nth-child(2){animation-delay:.04s}
.secondary-view>*:nth-child(3){animation-delay:.1s}
.secondary-view>*:nth-child(4){animation-delay:.16s}
.secondary-view>*:nth-child(5){animation-delay:.22s}
.secondary-view>*:nth-child(6){animation-delay:.28s}
.split>*:nth-child(2){animation-delay:.09s}
.tile-grid>*:nth-child(1){animation-delay:.02s}
.tile-grid>*:nth-child(2){animation-delay:.07s}
.tile-grid>*:nth-child(3){animation-delay:.12s}
.tile-grid>*:nth-child(4){animation-delay:.17s}
.tile-grid>*:nth-child(5){animation-delay:.22s}
.tile-grid>*:nth-child(6){animation-delay:.27s}
.tile-grid>*:nth-child(7){animation-delay:.32s}
.tile-grid>*:nth-child(8){animation-delay:.37s}
.tile-grid>*:nth-child(9){animation-delay:.42s}
.tile-grid>*:nth-child(10){animation-delay:.47s}
.tile-grid>*:nth-child(11){animation-delay:.52s}
.tile-grid>*:nth-child(12){animation-delay:.57s}
.docs>a:nth-child(2){animation-delay:.06s}
.docs>a:nth-child(3){animation-delay:.12s}
.docs>a:nth-child(4){animation-delay:.18s}
.docs>a:nth-child(5){animation-delay:.24s}
.docs>a:nth-child(6){animation-delay:.3s}
.workspace .work-head,.workspace .adjudication,.workspace .chart-wrap,.workspace .latest,.workspace .detections,.workspace .work-footer{animation:fuiIn .32s cubic-bezier(.2,.7,.3,1) backwards}
.workspace .work-head{animation-delay:.02s}
.workspace .adjudication{animation-delay:.08s}
.workspace .chart-wrap{animation-delay:.14s}
.workspace .latest{animation-delay:.2s}
.workspace .detections{animation-delay:.26s}
.workspace .work-footer{animation-delay:.32s}
@keyframes panelL{from{opacity:0;transform:translateX(-12px)}to{opacity:1;transform:none}}
@keyframes panelR{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:none}}
@media(min-width:721px){.sidebar{animation:panelL .32s cubic-bezier(.2,.7,.3,1)}}
@media(min-width:1121px){.chat{animation:panelR .32s cubic-bezier(.2,.7,.3,1)}}
@keyframes hexSettle{from{opacity:0;transform:scale(.3)}to{opacity:1;transform:none}}
.toolbar>b:before{animation:hexSettle .5s cubic-bezier(.2,.7,.3,1)}
.toolbar{overflow:hidden}
.toolbar:after{content:"";position:absolute;left:-150px;bottom:0;width:150px;height:2px;background:linear-gradient(90deg,transparent,var(--accent) 55%,transparent);opacity:0;animation:sweep 1s cubic-bezier(.3,.6,.3,1) .18s 1}
@keyframes sweep{0%{opacity:1;transform:none}80%{opacity:1}100%{opacity:0;transform:translateX(calc(100vw + 170px))}}
.chart-wrap:after{content:"";position:absolute;top:0;bottom:0;left:-56px;width:52px;background:linear-gradient(90deg,transparent,rgba(232,230,222,.12) 44%,rgba(232,230,222,.45) 50%,transparent 52%);pointer-events:none;opacity:0;animation:scan .9s cubic-bezier(.4,.2,.3,1) .55s 1}
@keyframes scan{0%{opacity:1;transform:none}88%{opacity:1}100%{opacity:0;transform:translateX(115vw)}}
.match-toast{animation:toastIn .3s cubic-bezier(.2,.7,.3,1) backwards,toastOut .35s ease-in 3.55s forwards}
@keyframes toastIn{0%{opacity:0;transform:translateX(14px)}38%{opacity:.9;transform:translateX(3px)}52%{opacity:.35}68%{opacity:1}100%{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateX(8px)}}
@keyframes msgIn{from{opacity:0;transform:translateY(5px);filter:blur(3px)}to{opacity:1;transform:none;filter:none}}
.messages .user,.messages .system{animation:msgIn .24s cubic-bezier(.2,.7,.3,1) backwards}
@media(prefers-reduced-motion:reduce){.main .card,.main .table,.docs a,.result,.tile-grid>*,.split>*,.secondary-view>*,.workspace .work-head,.workspace .adjudication,.workspace .chart-wrap,.workspace .latest,.workspace .detections,.workspace .work-footer,.sidebar,.chat,.toolbar>b:before,.toolbar:after,.chart-wrap:after,.match-toast,.messages .user,.messages .system{animation:none}}

/* ===== polish 13: native condensed voice ===== */
:root{--font-sans:'Avenir Next Condensed','Bahnschrift','HelveticaNeue-CondensedBold','Arial Narrow',system-ui,sans-serif;--font-mono:ui-monospace,'SF Mono','Cascadia Mono','Roboto Mono',Consolas,monospace}
.work-head h1,.secondary-view h1{font-family:var(--font-sans);font-weight:600;letter-spacing:.06em}
.toolbar>b{font-family:var(--font-sans);font-weight:600}
.sidebar nav button{letter-spacing:.03em}

/* ===== polish 14: the awkward trio ===== */
.sidebar .technique:before{display:none}
.sidebar .technique{margin-top:12px}
.messages{align-content:start}
.chat header{height:auto;min-height:56px}

/* ===== polish 15: mobile drawers behave ===== */
.chat header button{position:absolute;top:0;right:4px;height:30px;z-index:2;border:0;background:transparent;color:#3C4038;font-size:18px;line-height:30px;padding:0 8px}
@media(min-width:1121px){.chat header button{display:none}}
button.chat-fab{position:fixed;right:10px;bottom:10px;left:auto;top:auto;z-index:32}
.chat.open+.chat-fab{display:none}
</style></head>"""

_BOUNDARY = (
    "Research instrument only. Nothing here is investment advice or "
    "a solicitation to trade. Past results guarantee nothing. "
    "Full text: docs/DISCLAIMER.md"
)

_AVAILABLE_DATA = (
    ("trades", "ticks, size, side"),
    ("klines", "candles, any timeframe"),
    ("daily_klines", "daily candles"),
    ("funding", "funding rate"),
    ("open_interest", "open interest"),
)

_NAV = (
    ("/workspace", "Workspace"), ("/", "Status"), ("/claim", "Claim"), ("/register", "Register"),
    ("/measure", "Measure"), ("/judge", "Judge"), ("/read", "Read"),
    ("/docs", "Docs"),
)


def _technique_panel(workdir: Path) -> str:
    try:
        states = list_family_states(workdir / "state")
    except Exception:
        states = []
    if not states:
        return (
            '<p class="micro">APPLIED TECHNIQUE</p><h2>none</h2>'
            '<div class="badge-line"><span class="badge">no family '
            "registered</span></div>"
        )
    state = states[-1]
    return (
        '<p class="micro">APPLIED TECHNIQUE</p>'
        f"<h2>{state.family}</h2>"
        '<div class="badge-line">'
        f'<span class="badge {state.stage}">{state.stage}</span>'
        "</div>"
    )


def _page(title: str, body: str, active: str = "",
          workdir: Path | None = None,
          with_chat: bool = False) -> str:
    workdir = workdir if workdir is not None else Path(".")
    nav = "".join(
        '<button class="' + ("active" if path == active else "")
        + '" onclick="location.href=' + "'" + path + "'" + '">'
        + label + "</button>"
        for path, label in _NAV
    )
    open_side = (
        '"document.querySelector(' + "'.chat'"
        + ")?.classList.remove('open');"
        + 'document.querySelector(' + "'.sidebar'"
        + ").classList.add('open')\""
    )
    close_side = (
        '"this.closest(' + "'.sidebar'"
        + ").classList.remove('open')\""
    )
    shell = _CSS_HEAD.replace("TITLE_SLOT", title)
    shell += (
        '<body class="platform' + (' has-chat' if with_chat else '') + '">'
        '<div class="geo one"></div><div class="geo two"></div>'
        '<div class="geo three"></div>'
        ''
        '<header class="toolbar">'
        "<button class='panel-btn left' title='sidebar' onclick='togglePanel(0)'><svg width='14' height='12' viewBox='0 0 14 12'><rect x='0.5' y='0.5' width='13' height='11' rx='2.5' fill='none' stroke='currentColor'/><line x1='4.5' y1='1' x2='4.5' y2='11' stroke='currentColor'/></svg></button>"
        '<b>VERIFIER</b>'
        '<span class="tool-split"></span>'
        '<span class="connection">' + _ACCESS_LABEL + '</span>'
        '<span class="tool-clock" id="toolClock"></span>'
        "<button class='panel-btn right' title='chat' onclick='togglePanel(1)'><svg width='14' height='12' viewBox='0 0 14 12'><rect x='0.5' y='0.5' width='13' height='11' rx='2.5' fill='none' stroke='currentColor'/><line x1='9.5' y1='1' x2='9.5' y2='11' stroke='currentColor'/></svg></button>"
        "</header>"
        '<div class="app' + ('' if with_chat else ' no-chat') + '">'
        '<aside class="sidebar"><div class="side-top">'
        '<button class="logo" onclick="location.href=' + "'/'"
        + '">VERIFIER</button>'
        '<button class="close" onclick=' + close_side
        + ">\u00d7</button></div>"
        "<nav>" + nav + "</nav>"
        '<section class="technique">' + _technique_panel(workdir)
        + "</section>"
        '<p class="boundary">' + _BOUNDARY + "</p></aside>"
        '<div class="main">'
        '<div class="mobile-head">'
        "<button onclick=" + open_side + ">MENU</button>"
        "<b>VERIFIER</b><span></span></div>"
        '<section class="secondary-view">' + body
        + "</section></div>"
        + (_chat_panel() if with_chat else "")
        + "</div><script>"
        + "function applyPanels(){var b=document.body;"
        + "b.classList.toggle('hide-left',"
        + "localStorage.getItem('vfHideL')==='1');"
        + "b.classList.toggle('hide-right',"
        + "localStorage.getItem('vfHideR')==='1');"
        + "var l=document.querySelector('.panel-btn.left');"
        + "var r=document.querySelector('.panel-btn.right');"
        + "if(l)l.classList.toggle('off',"
        + "b.classList.contains('hide-left'));"
        + "if(r)r.classList.toggle('off',"
        + "b.classList.contains('hide-right'));}"
        + "function togglePanel(side){var k=side?'vfHideR':'vfHideL';"
        + "localStorage.setItem(k,"
        + "localStorage.getItem(k)==='1'?'0':'1');applyPanels();}"
        + "applyPanels();"
        + "(function(){var c=document.getElementById('toolClock');"
        + "if(!c)return;function tick(){"
        + "c.textContent=new Date().toISOString()"
        + ".slice(0,19).replace('T','  ')+' UTC';}"
        + "tick();setInterval(tick,1000);})();"
        + "document.addEventListener('click',function(e){"
        + "var b=e.target.closest('button');"
        + "if(!b||b.classList.contains('edge-toggle'))return;"
        + "var r=b.getBoundingClientRect();"
        + "var s=document.createElement('span');"
        + "s.className='fx-hex';"
        + "var d=Math.max(r.width,r.height)*1.2;"
        + "s.style.width=d+'px';s.style.height=d+'px';"
        + "s.style.left=(e.clientX-r.left-d/2)+'px';"
        + "s.style.top=(e.clientY-r.top-d/2)+'px';"
        + "b.appendChild(s);"
        + "setTimeout(function(){s.remove();},520);});"
        + "</script></body></html>"
    )
    return shell

def _status_body(workdir: Path) -> str:
    from strategy_verifier.verifier.reading import _adjudication

    states = list_family_states(workdir / "state")
    if not states:
        tiles = (
            '<div class="card fam-tile"><h2>none</h2>'
            '<div class="meta-rows"><code>No families yet '
            "\u2014 start at Claim.</code></div></div>"
        )
    else:
        from strategy_verifier.verifier.lifecycle import TRANSITIONS

        def _controls(state) -> str:
            allowed = TRANSITIONS[state.stage]
            if not allowed:
                return "<span class='micro'>terminal</span>"
            options = "".join(
                f"<option>{target}</option>" for target in allowed
            )
            return (
                f"<select id='to_{state.family}'>{options}</select>"
                f"<input id='note_{state.family}' "
                "placeholder='memo (frozen and closed require one)'>"
                "<button class='secondary' onclick=" + chr(34)
                + f"adv('{state.family}')" + chr(34)
                + ">Advance</button>"
            )

        def _tile(state) -> str:
            lines = list(_adjudication(
                workdir / "state", state.family
            ))
            tone = _adjudication_tone(lines)
            label = {"not": "NOT_CONFIRMED",
                     "confirmed": "CONFIRMED"}.get(
                tone, "UNADJUDICATED")
            last = (
                state.transitions[-1] if state.transitions else "\u2014"
            )
            return (
                '<div class="card fam-tile">'
                f"<h2>{state.family}</h2>"
                '<div class="meta-rows">'
                f"<code>DIR: /state/{state.family}.json</code>"
                "<code>STAGE: "
                f"<span class='badge {state.stage}' "
                f"id='stage_{state.family}'>{state.stage}</span>"
                "</code>"
                f"<code>LAST: {last}</code>"
                "<code>VERDICT: "
                f"<span class='badge "
                f"{tone if tone != 'unadjudicated' else ''}'>"
                f"{label}</span></code>"
                "</div>"
                f"<span class='tile-chip'>{len(state.transitions)} "
                "TRANSITIONS</span>"
                f"<div class='tile-adv' id='ctrl_{state.family}'>"
                f"{_controls(state)}</div>"
                "</div>"
            )

        tiles = "".join(_tile(state) for state in states)
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>Status Board</h1>
<div class="tile-grid">""" + tiles + """</div>
<div id="advResult"></div>
<p class="micro" style="margin-top:10px">Freezing, confirming, and
closing require a memo. Closed is permanent.</p>
""" + '<script>\nasync function adv(family) {\n  const to = document.getElementById(\'to_\'+family).value;\n  const note = document.getElementById(\'note_\'+family).value;\n  const response = await fetch(\'/api/advance\', {method:\'POST\',\n    headers:{\'Content-Type\':\'application/json\'},\n    body: JSON.stringify({family:family, to:to, note:note})});\n  const data = await response.json();\n  document.getElementById(\'advResult\').innerHTML = data.error\n    ? \'<div class="card result refusal"><div><h2>Refused</h2><p>\'+\n      data.error+\'</p></div></div>\'\n    : \'<div class="card result pass"><div><h2>\'+data.family+\n      \' \\u2192 \'+data.stage+\'</h2><p>\'+data.guidance+\'</p></div></div>\';\n  if (!data.error) {\n    document.getElementById(\'stage_\'+data.family).textContent =\n      data.stage;\n    const cell = document.getElementById(\'ctrl_\'+data.family);\n    if (!data.allowed || data.allowed.length === 0) {\n      cell.innerHTML = "<span class=\'micro\'>terminal</span>";\n    } else {\n      cell.innerHTML =\n        "<select id=\'to_"+data.family+"\'>" +\n        data.allowed.map(t=>\'<option>\'+t+\'</option>\').join(\'\') +\n        "</select><input id=\'note_"+data.family+\n        "\' placeholder=\'memo (frozen and closed require one)\'>"+\n        "<button class=\'secondary\' onclick=" + \'"\' +\n        "adv(\'" + data.family + "\')" + \'"\' + ">Advance</button>";\n    }\n  }\n}\n</script>')


def _claim_body() -> str:
    shown = {"klines": "klines (candles)",
             "daily_klines": "daily_klines (daily candles)"}
    checks = "".join(
        f"<label><input type='checkbox' name='data' value='{value}'>"
        f"{shown.get(value, value)}</label>"
        for value, _ in _AVAILABLE_DATA
    )
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>New Claim</h1>
<p class="micro">State the technique as a machine-testable claim.
Discretionary wording is refused with the reason: refusal is this
product's first feature, not an error.</p>
<div class="split">
<div class="card form">
<div class="field"><span>FAMILY NAME</span>
<input id="family" placeholder="my_technique"></div>
<div class="field"><span>DIRECTION RULE (WHEN LONG, WHEN SHORT)</span>
<textarea id="direction" placeholder="Close above the 200 average and the average rising over 12 bars means long; the mirror means short."></textarea></div>
<div class="field"><span>DATA SOURCES</span>
<div class="checks">""" + checks + """</div></div>
<div class="field"><span>CONDITIONS (EACH LINE ANSWERABLE YES OR NO)</span></div>
<div class="condition-rows" id="conds"><div>
<input placeholder="normalized distance of close to the 20 average at or under delta"></div></div>
<button class="secondary" type="button" onclick="addCond()">Add condition row</button>
<div style="margin-top:12px"><button class="primary" type="button"
onclick="submitTriage()">Screen at the front door</button></div>
</div>
<div id="result"></div>
</div>
<script>
function addCond() {
  const row = document.createElement('div');
  row.innerHTML = '<input>';
  document.getElementById('conds').appendChild(row);
}
function payload() {
  return {
    family: document.getElementById('family').value.trim(),
    direction: document.getElementById('direction').value.trim(),
    data: Array.from(document.querySelectorAll(
      'input[name=data]:checked')).map(el => el.value),
    conditions: Array.from(document.querySelectorAll('#conds input'))
      .map(el => el.value.trim()).filter(Boolean),
  };
}
async function submitTriage() {
  const response = await fetch('/api/triage', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload())});
  const data = await response.json();
  const target = document.getElementById('result');
  if (data.error) {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Error</h2><p>'+data.error+'</p></div></div>'; return;
  }
  if (data.admissible) {
    target.innerHTML =
      '<div class="card result pass"><div><h2>Admissible</h2>'+
      '<p>The claim is mechanically testable. Saving seals it as '+
      'the canonical TOML under claims/.</p>'+
      '<div style="margin-top:8px"><button class="primary" '+
      'onclick="saveClaim()">Save claim</button></div>'+
      '<pre id="savedTo"></pre></div></div>';
  } else {
    target.innerHTML = data.reasons.map(reason =>
      '<div class="card result refusal"><div>'+
      '<h2><span class="badge refusal">'+reason.code+'</span></h2>'+
      '<p><b>Field:</b> '+reason.field+'</p>'+
      '<p><b>Detail:</b> '+reason.detail+'</p>'+
      '<p><b>How to fix:</b> '+reason.guidance+'</p></div></div>'
    ).join('');
  }
}
async function saveClaim() {
  const response = await fetch('/api/save-claim', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload())});
  const data = await response.json();
  const escq = s => String(s).replace(/&/g,'&amp;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
  document.getElementById('savedTo').innerHTML =
    data.error ? ('Error: ' + escq(data.error))
    : ('Saved: ' + escq(data.path) +
       ' — <a href="/register">next → Register</a>');
}
</script>""")


def _register_body(workdir: Path) -> str:
    claims = sorted(
        path.name
        for path in (workdir / "claims").glob("*_claim.toml")
    ) if (workdir / "claims").exists() else []
    claim_options = "".join(
        f"<option>{name}</option>" for name in claims
    ) or "<option value=''>no saved claims</option>"
    definitions = sorted(
        path.name for path in (workdir / "definitions").glob("*.toml")
    ) if (workdir / "definitions").exists() else []
    definition_options = "".join(
        f"<option>{name}</option>" for name in definitions
    )
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>Register</h1>
<p class="micro">The bar is sealed before the data. Exploration and
confirmation windows may not overlap on the calendar, and a family
name can never be registered twice.</p>
<div class="split"><div class="card form">
<div class="field"><span>CLAIM (claims/)</span>
<select id="claim">""" + claim_options + """</select></div>
<div class="field"><span>DEFINITION (definitions/, graph_v1)</span>
<select id="definition">""" + definition_options + """</select></div>
<div class="field"><span>EXPLORATION WINDOW</span>
<input id="explore" placeholder="2026-01-01..2026-03-31"></div>
<div class="field"><span>CONFIRMATION WINDOW</span>
<input id="confirm" placeholder="2024-01-01..2024-06-30"></div>
<div style="margin-top:10px"><button class="primary"
onclick="submitRegister()">Register</button></div>
</div><div id="regResult"></div></div>
<script>
async function submitRegister() {
  const response = await fetch('/api/register', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      claim: document.getElementById('claim').value,
      definition: document.getElementById('definition').value,
      explore: document.getElementById('explore').value.trim(),
      confirm: document.getElementById('confirm').value.trim(),
    })});
  const data = await response.json();
  const target = document.getElementById('regResult');
  if (data.error) {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Refused</h2><p>'+data.error+'</p></div></div>';
  } else if (data.admissible === false) {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Claim refused ('+data.reasons.length+' reasons)</h2>'+
      '<p>Fix it on the Claim screen and save again.</p></div></div>';
  } else {
    target.innerHTML = '<div class="card result pass"><div>'+
      '<h2>Registered: '+data.family+'</h2>'+
      '<pre>document: '+data.document+
      String.fromCharCode(10)+'definition sha256[:16]: '+
      data.definition_sha+String.fromCharCode(10)+
      'stage: registered (advance on Status)</pre></div></div>';
  }
}
</script>""")


def _docs_body(workdir: Path) -> str:
    rows = []
    for name in ("USER_GUIDE.md", "DISCLAIMER.md",
                 "PRODUCT_CHARTER.md", "COMPLIANCE.md"):
        exists = (workdir / "docs" / name).exists()
        note = "in the working folder docs/" if exists else "missing"
        rows.append(
            f"<a href='/docs/{name}' target='_blank'>"
            f"<code>{name}</code><span>{note}</span></a>"
        )
    return (
        '<p class="micro">RESEARCH PIPELINE</p><h1>Documents</h1>'
        '<div class="docs">' + "".join(rows) + "</div>"
    )

def _claim_toml(payload: dict) -> str:
    def _quote(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        "[claim]",
        f"family = {_quote(payload['family'])}",
        f"direction = {_quote(payload['direction'])}",
        "data = [" + ", ".join(
            _quote(item) for item in payload["data"]
        ) + "]",
        "conditions = [",
    ]
    for condition in payload["conditions"]:
        lines.append(f"  {_quote(condition)},")
    lines.append("]")
    return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    workdir: Path = Path(".")

    def log_message(self, *_args) -> None:  # 조용한 서버
        return

    def _send(self, status: int, content: str,
              content_type: str = "text/html; charset=utf-8") -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")


    def _authorized(self) -> bool:
        import base64 as _b64
        import hmac as _hmac
        import os as _os

        expected = _os.environ.get("VERIFIER_WEB_PASSWORD", "")
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = _b64.b64decode(
                    header[6:].strip()
                ).decode("utf-8")
                supplied = (
                    decoded.split(":", 1)[1]
                    if ":" in decoded else ""
                )
                if _hmac.compare_digest(supplied, expected):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header(
            "WWW-Authenticate", 'Basic realm="verifier"'
        )
        self.end_headers()
        return False
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            # auth-exempt by design: platform healthchecks cannot
            # send credentials, and the body is a bare ok
            self._send(200, "ok", "text/plain; charset=utf-8")
            return
        if not self._authorized():
            return
        if self.path == "/workspace":
            self._send(200, _page(
                "Workspace", _workspace_body(self.workdir),
                "/workspace", self.workdir, with_chat=True,
            ))
        elif self.path == "/api/stream":
            self._handle_stream()
            return
        elif self.path == "/":
            self._send(200, _page("Status", _status_body(self.workdir), "/", self.workdir))
        elif self.path == "/claim":
            self._send(200, _page("Claim", _claim_body(), "/claim", self.workdir))
        elif self.path == "/register":
            self._send(200, _page("Register", _register_body(self.workdir), "/register", self.workdir))
        elif self.path == "/measure":
            self._send(200, _page("Measure", _measure_body(self.workdir), "/measure", self.workdir))
        elif self.path == "/judge":
            self._send(200, _page("Judge", _judge_body(self.workdir), "/judge", self.workdir))
        elif self.path == "/read":
            self._send(200, _page("Read", _read_body(self.workdir), "/read", self.workdir))
        elif self.path.startswith("/docs/"):
            name = self.path[len("/docs/"):]
            allowed_docs = {
                "USER_GUIDE.md", "DISCLAIMER.md",
                "PRODUCT_CHARTER.md", "COMPLIANCE.md",
            }
            path = self.workdir / "docs" / name
            if name not in allowed_docs or not path.exists():
                self._send(404, _page("404", "<h1>404</h1>"))
                return
            self._send(
                200, path.read_text(encoding="utf-8"),
                "text/plain; charset=utf-8",
            )
        elif self.path.startswith("/reports/"):
            name = self.path[len("/reports/"):]
            if (
                not name or "/" in name or "\\" in name
                or ".." in name
                or not (name.endswith(".html")
                        or name.endswith(".txt")
                        or name.endswith(".jsonl"))
            ):
                self._send(404, _page("404", "<h1>404</h1>"))
                return
            path = (self.workdir / "reports" / name)
            try:
                resolved = path.resolve()
                reports_root = (self.workdir / "reports").resolve()
                inside = resolved.is_relative_to(reports_root)
            except (OSError, ValueError):
                inside = False
            if not inside or not path.exists():
                self._send(404, _page("404", "<h1>404</h1>"))
                return
            content = path.read_text(encoding="utf-8")
            content_type = (
                "text/html; charset=utf-8" if name.endswith(".html")
                else "text/plain; charset=utf-8"
            )
            self._send(200, content, content_type)
        elif self.path == "/api/captures":
            import time as _time
            root = self.workdir / "data" / "captures"
            entries = []
            if root.exists():
                paths = sorted(
                    root.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
                newest = paths[0].name if paths else None
                now = _time.time()
                for path in paths:
                    stat = path.stat()
                    entries.append({
                        "name": path.name,
                        "bytes": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "active": (path.name == newest or
                                   now - stat.st_mtime < 600),
                    })
            self._json(200, {"captures": entries})
        elif self.path == "/api/reports":
            root = self.workdir / "reports"
            entries = [
                {"name": path.name, "bytes": path.stat().st_size}
                for path in sorted(root.glob("*"))
                if path.is_file() and path.suffix in
                (".txt", ".html", ".jsonl")
            ] if root.exists() else []
            self._json(200, {"reports": entries})
        elif self.path.startswith("/captures/"):
            name = self.path[len("/captures/"):]
            root = self.workdir / "data" / "captures"
            if (not name or "/" in name or "\\" in name
                    or ".." in name
                    or not name.endswith(".jsonl")):
                self._send(404, _page("404", "<h1>404</h1>"))
                return
            path = root / name
            try:
                inside = path.resolve().is_relative_to(
                    root.resolve())
            except (OSError, ValueError):
                inside = False
            if not inside or not path.exists():
                self._send(404, _page("404", "<h1>404</h1>"))
                return
            import shutil as _shutil
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as handle:
                _shutil.copyfileobj(handle, self.wfile, 65536)
        elif self.path.startswith("/api/job"):
            self._handle_job()
        elif self.path == "/docs":
            self._send(200, _page("Docs", _docs_body(self.workdir), "/docs", self.workdir))
        elif self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._send(404, _page("404", "<h1>404</h1>"))

    def _read_payload(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return {
            "family": str(raw.get("family", "")),
            "direction": str(raw.get("direction", "")),
            "data": [str(item) for item in raw.get("data", [])
                     if isinstance(item, str)],
            "conditions": [
                str(item) for item in raw.get("conditions", [])
                if isinstance(item, str) and item.strip()
            ],
        }

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/api/captures/retire":
            self._handle_captures_retire()
            return
        if self.path == "/api/chat":
            self._handle_chat()
            return
        if self.path == "/api/workspace/start":
            self._handle_workspace_start()
            return
        if self.path == "/api/workspace/stop":
            self._handle_workspace_stop()
            return
        if self.path == "/api/measure":
            self._handle_measure()
            return
        if self.path == "/api/read":
            self._handle_read_ui()
            return
        if self.path == "/api/diagnose":
            self._handle_diagnose()
            return
        if self.path == "/api/verdict":
            self._handle_verdict()
            return
        if self.path == "/api/certificate":
            self._handle_certificate()
            return
        if self.path == "/api/register":
            self._handle_register()
            return
        if self.path == "/api/advance":
            self._handle_advance()
            return
        payload = self._read_payload()
        if payload is None:
            self._json(400, {"error": "malformed request body"})
            return
        claim_table = {
            "family": payload["family"],
            "direction": payload["direction"],
            "data": payload["data"],
            "conditions": payload["conditions"],
        }
        if self.path == "/api/triage":
            try:
                result = triage_claim_table(
                    claim_table, Path("<ui-form>")
                )
            except Exception as exc:  # 스키마 오류도 카드로
                self._json(200, {"error": str(exc)})
                return
            self._json(200, {
                "admissible": result.admissible,
                "reasons": [
                    {"code": reason.code, "field": reason.field,
                     "detail": reason.detail,
                     "guidance": reason.guidance}
                    for reason in result.reasons
                ],
            })
        elif self.path == "/api/save-claim":
            try:
                result = triage_claim_table(
                    claim_table, Path("<ui-form>")
                )
                if not result.admissible:
                    self._json(200, {
                        "error": "refused claims are not saved",
                    })
                    return
                claims_dir = self.workdir / "claims"
                claims_dir.mkdir(parents=True, exist_ok=True)
                out_path = claims_dir / f"{result.family}_claim.toml"
                if out_path.exists():
                    self._json(200, {
                        "error": f"already exists: {out_path.name} — a "
                        "new look takes a new name",
                    })
                    return
                out_path.write_text(
                    _claim_toml(payload), encoding="utf-8"
                )
                self._json(200, {"path": str(out_path)})
            except Exception as exc:
                self._json(200, {"error": str(exc)})
        else:
            self._json(404, {"error": "no such path"})


def _raw_json(handler) -> dict | None:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        raw = json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _handle_register_impl(handler) -> None:
    import tomllib

    from strategy_verifier.verifier import run_registration

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    workdir = handler.workdir
    claim_path = workdir / "claims" / str(raw.get("claim", ""))
    definition_name = str(raw.get("definition", ""))
    if not claim_path.exists():
        handler._json(200, {"error": "claim file not found"})
        return
    try:
        claim_raw = tomllib.loads(
            claim_path.read_text(encoding="utf-8")
        )
        registration_text = (
            claim_path.read_text(encoding="utf-8")
            + "\n[registration]\n"
            + 'engine = "graph_v1"\n'
            + f'definition = "definitions/{definition_name}"\n'
            + f'explore = "{raw.get("explore", "")}"\n'
            + f'confirm = "{raw.get("confirm", "")}"\n'
        )
        combined = workdir / "claims" / (
            f"{claim_raw.get('claim', {}).get('family', 'x')}"
            "_registration.toml"
        )
        combined.write_text(registration_text, encoding="utf-8")
        import os

        cwd = os.getcwd()
        os.chdir(workdir)
        try:
            triage, registration = run_registration(
                combined.relative_to(workdir),
                definitions_dir=Path("definitions"),
                docs_dir=Path("docs"),
                state_dir=Path("state"),
            )
        finally:
            os.chdir(cwd)
    except Exception as exc:
        handler._json(200, {"error": str(exc)})
        return
    if registration is None:
        handler._json(200, {
            "admissible": False,
            "reasons": [
                {"code": reason.code} for reason in triage.reasons
            ],
        })
        return
    handler._json(200, {
        "admissible": True,
        "family": registration.family,
        "document": str(registration.document_path),
        "definition_sha": registration.definition_sha256,
    })


def _handle_advance_impl(handler) -> None:
    from strategy_verifier.verifier import advance_family_state
    from strategy_verifier.ui.server import _GUIDANCE_LOOKUP

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    try:
        state = advance_family_state(
            handler.workdir / "state",
            str(raw.get("family", "")),
            str(raw.get("to", "")),
            str(raw.get("note", "")),
        )
    except Exception as exc:
        handler._json(200, {"error": str(exc)})
        return
    from strategy_verifier.verifier.lifecycle import TRANSITIONS

    handler._json(200, {
        "family": state.family,
        "stage": state.stage,
        "allowed": list(TRANSITIONS[state.stage]),
        "guidance": _GUIDANCE_LOOKUP.get(state.stage, ""),
    })


from strategy_verifier.verifier.lifecycle import _GUIDANCE as _GUIDANCE_LOOKUP

def _handle_captures_retire_impl(handler) -> None:
    """Delete a cold capture only when the caller proves it
    holds the bytes: the sha256 it sends must match the file.
    The active recording session is never retirable."""
    import hashlib
    import time as _time

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    name = str(raw.get("name", ""))
    claimed = str(raw.get("sha256", "")).strip().lower()
    if (not name or "/" in name or "\\" in name
            or ".." in name or not name.endswith(".jsonl")):
        handler._json(200, {"error": "no such capture"})
        return
    root = handler.workdir / "data" / "captures"
    path = root / name
    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside or not path.exists():
        handler._json(200, {"error": "no such capture"})
        return
    paths = sorted(root.glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    newest = paths[0].name if paths else None
    stat = path.stat()
    if name == newest or _time.time() - stat.st_mtime < 600:
        handler._json(200, {"error": "active session — "
                            "retire refused"})
        return
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != claimed:
        handler._json(200, {"error": "sha256 mismatch — the "
                            "caller does not hold these bytes"})
        return
    path.unlink()
    handler._json(200, {"retired": name, "sha256": digest,
                        "bytes": stat.st_size})


_Handler._handle_register = _handle_register_impl
_Handler._handle_advance = _handle_advance_impl
_Handler._handle_captures_retire = _handle_captures_retire_impl


def make_ui_server(workdir: Path, port: int = 0,
                   bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Defaults to loopback; serve mode binds wider. Port zero
    picks an ephemeral port."""

    handler = type("BoundHandler", (_Handler,), {"workdir": workdir})
    return ThreadingHTTPServer((bind, port), handler)


def run_ui(workdir: Path, port: int, open_browser: bool) -> int:
    server = make_ui_server(workdir, port)
    host, bound_port = server.server_address
    url = f"http://{host}:{bound_port}/"
    print(f"Verifier UI: {url} (작업 폴더: {workdir})")
    print("종료: Ctrl+C · 루프백 전용 바인딩 · " + _BOUNDARY)
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---- H-3: 측정·판독 잡 + 진단·판정·감정서 (기능 우선, 화면은 소박) ----

import threading
import uuid

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _start_job(target) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running", "detail": ""}

    def _wrap() -> None:
        try:
            detail = target()
            with _JOBS_LOCK:
                _JOBS[job_id] = {"status": "done", "detail": detail}
        except Exception as exc:
            with _JOBS_LOCK:
                _JOBS[job_id] = {"status": "error", "detail": str(exc)}

    threading.Thread(target=_wrap, daemon=True).start()
    return job_id


def _list_files(workdir: Path, sub: str, suffix: str) -> list[str]:
    folder = workdir / sub
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.glob(f"*{suffix}"))



def _checkboxes(name: str, items: list[str],
                empty: str = "no files") -> str:
    if not items:
        return f"<span class='micro'>{empty}</span>"
    return "".join(
        f"<label><input type='checkbox' name='{name}' "
        f"value='{item}'><code>{item}</code></label>"
        for item in items
    )


def _select_options(items: list[str],
                    empty: str = "none") -> str:
    return "".join(f"<option>{item}</option>" for item in items) or (
        f"<option value=''>{empty}</option>"
    )

_POLL_SCRIPT = """
<script>
function picked(name) {
  return Array.from(document.querySelectorAll(
    'input[name='+name+']:checked')).map(el => el.value);
}
async function poll(jobId, target) {
  const response = await fetch('/api/job?id='+jobId);
  const data = await response.json();
  if (data.status === 'running') {
    target.innerHTML = '<div class="card result running">'+
      '<div class="spinner"></div><div><h2>Running…</h2></div></div>';
    setTimeout(()=>poll(jobId, target), 700);
  } else if (data.status === 'done') {
    target.innerHTML = '<div class="card result pass"><div>'+
      '<h2>Done</h2><pre>'+data.detail+'</pre></div></div>';
  } else {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Error</h2><p>'+data.detail+'</p></div></div>';
  }
}
async function startJob(path, payload, target) {
  target.innerHTML = '<div class="card result running">'+
    '<div class="spinner"></div><div><h2>Starting…</h2></div></div>';
  const response = await fetch(path, {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)});
  const data = await response.json();
  if (data.error) {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Refused</h2><p>'+data.error+'</p></div></div>';
    return;
  }
  poll(data.job, target);
}
</script>"""



def _measure_body(workdir: Path) -> str:
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>Measure</h1>
<p class="micro">Same artifact as the CLI, equation stamp included,
saved under reports/. Long inputs run in the background.
Confirmation-window data is not touched before freezing.</p>
<div class="split"><div class="card form">
<div class="field"><span>DEFINITION</span>
<select id="mdef">""" + _select_options(
        _list_files(workdir, "definitions", ".toml")
    ) + """</select></div>
<div class="field"><span>INPUT CAPTURES (data/captures/)</span>
<div class="capture-list">""" + _checkboxes(
        "minput", _list_files(workdir, "data/captures", ".jsonl"),
        empty="no captures yet — record a live stream (verifier record) or convert exchange history; files land in data/captures/",
    ) + """</div></div>
<label class="toggle"><input type="checkbox" id="mcal">
Calibration only (percentile ladder)</label>
<div class="field" style="margin-top:10px">
<span>REPORT FILENAME (reports/)</span>
<input id="mout" placeholder="btcusdt_myfam_explore.txt"></div>
<div style="margin-top:10px"><button class="primary"
onclick="startJob('/api/measure', {
  definition: document.getElementById('mdef').value,
  inputs: picked('minput'),
  calibrate: document.getElementById('mcal').checked,
  output: document.getElementById('mout').value.trim(),
}, document.getElementById('mres'))">Run</button></div>
</div><div id="mres"></div></div>""" + _POLL_SCRIPT)


def _judge_body(workdir: Path) -> str:
    reports = [
        name for name in _list_files(workdir, "reports", ".txt")
        if not name.endswith(("_diagnosis.txt", "_verdict.txt"))
    ]
    families = _select_options(
        sorted(state.family for state in list_family_states(
            workdir / "state")),
        empty="no families — register first",
    )
    import tomllib
    defaults = {}
    for state in list_family_states(workdir / "state"):
        try:
            reg_path = (workdir / "claims"
                        / f"{state.family}_registration.toml")
            with reg_path.open("rb") as fh:
                registration = tomllib.load(fh)
            def_rel = str(
                registration["registration"]["definition"])
            with (workdir / def_rel).open("rb") as fh:
                definition = tomllib.load(fh)
            outcomes = definition.get("outcomes", {})
            horizons = outcomes.get("horizons") or []
            thresholds = outcomes.get("threshold_pcts") or []
            if horizons and thresholds:
                defaults[state.family] = {
                    "horizon": str(horizons[0]),
                    "threshold": str(thresholds[0]),
                }
        except Exception:
            continue
    defaults_json = json.dumps(defaults, sort_keys=True)
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>Judge</h1>
<p class="micro">Pick the per-market measurement reports, then run
the clinical diagnosis and the verdict. Artifacts match the CLI and
land under reports/.</p>
<div class="split"><div class="card form">
<div class="field"><span>MARKET REPORTS</span>
<div class="capture-list">""" + _checkboxes(
        "jreport", reports, empty="no measurement reports yet — run Measure first; artifacts land under reports/")
    + """</div></div>
<div class="field"><span>FAMILY (registered)</span><select id="jfam">""" + families + """</select></div>
<div class="field"><span>PRIMARY HORIZON</span>
<input id="jhor" placeholder="1000000"></div>
<div class="field"><span>PRIMARY THRESHOLD</span>
<input id="jthr" placeholder="0.01"></div>
<div class="field"><span>ARM</span>
<input id="jarm" value="base_grid"></div>
<div class="field"><span>CONTROL ARM (empty = the coin flip, 50%)</span>
<input id="jctl" value="control_grid"></div>
<div style="margin-top:10px">
<button class="primary"
 onclick="runJudge('/api/diagnose','jdres')">Diagnose</button>
<button class="secondary"
 onclick="runJudge('/api/verdict','jvres')">Adjudicate</button>
<button class="secondary" onclick="runCertificate()">Generate
certificate</button></div>
</div><div>
<div id="jvres"></div><div id="jdres"></div><div id="jcres"></div>
</div></div>""" + _POLL_SCRIPT + """
<script>
var judgeDefaults = """ + defaults_json + """;
function fillJudgeDefaults() {
  const fam = document.getElementById('jfam').value;
  const hit = judgeDefaults[fam];
  if (!hit) { return; }
  document.getElementById('jhor').value = hit.horizon;
  document.getElementById('jthr').value = hit.threshold;
}
document.getElementById('jfam')
  .addEventListener('change', fillJudgeDefaults);
fillJudgeDefaults();
function judgePayload() {
  return {
    reports: picked('jreport'),
    family: document.getElementById('jfam').value.trim(),
    horizon: document.getElementById('jhor').value.trim(),
    threshold: document.getElementById('jthr').value.trim(),
    arm: document.getElementById('jarm').value.trim(),
    control: document.getElementById('jctl').value.trim(),
  };
}
async function runJudge(path, targetId) {
  const target = document.getElementById(targetId);
  const response = await fetch(path, {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(judgePayload())});
  const data = await response.json();
  if (data.error) {
    target.innerHTML = '<div class="card result refusal"><div>'+
      '<h2>Error</h2><p>'+data.error+'</p></div></div>'; return;
  }
  const tone = data.passed === false ? 'refusal'
    : data.passed === true ? 'pass' : 'running';
  target.innerHTML = '<div class="card result '+tone+'"><div>'+
    (data.headline ? '<h2><code>'+data.headline+'</code></h2>' : '')+
    '<p>saved: <code>'+data.path+'</code> '+
    '<a href="/'+data.path+'" target="_blank">open</a></p>'+
    '<pre>'+data.tail+'</pre></div></div>';
}
async function runCertificate() {
  const target = document.getElementById('jcres');
  const response = await fetch('/api/certificate', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(judgePayload())});
  const data = await response.json();
  target.innerHTML = data.error
    ? '<div class="card result refusal"><div><h2>Error</h2><p>'+
      data.error+'</p></div></div>'
    : '<div class="card result pass"><div><h2>Certificate</h2>'+
      '<p><code>'+data.path+'</code> <a href="/'+data.path+
      '" target="_blank">Open</a>. Print to save as PDF.</p>'+
      '</div></div>';
}
</script>""")


def _read_body(workdir: Path) -> str:
    return ("""<p class="micro">RESEARCH PIPELINE</p>
<h1>Batch Read</h1>
<p class="micro">Describes where the rule occurred and how those
occurrences resolved, with the family adjudication always attached.
Nothing on this screen is an instruction to act.</p>
<div class="split"><div class="card form">
<div class="field"><span>DEFINITION (graph_v1)</span>
<select id="rdef">""" + _select_options(
        _list_files(workdir, "definitions", ".toml")
    ) + """</select></div>
<div class="field"><span>INPUT CAPTURES</span>
<div class="capture-list">""" + _checkboxes(
        "rinput", _list_files(workdir, "data/captures", ".jsonl"),
        empty="no captures yet — record a live stream (verifier record) or convert exchange history; files land in data/captures/",
    ) + """</div></div>
<div class="field"><span>LATEST BARS</span>
<input id="rbars" value="3" style="width:80px"></div>
<div style="margin-top:10px"><button class="primary"
onclick="startJob('/api/read', {
  definition: document.getElementById('rdef').value,
  inputs: picked('rinput'),
  last_bars: document.getElementById('rbars').value.trim(),
}, document.getElementById('rres'))">Read</button></div>
</div><div id="rres"></div></div>""" + _POLL_SCRIPT)

def _handle_measure_impl(handler) -> None:
    from strategy_verifier.verifier import (
        load_verifier_definition,
        run_verifier_definition,
        render_equation_block,
    )

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    workdir = handler.workdir
    definition_path = workdir / "definitions" / str(raw.get("definition", ""))
    inputs = [
        workdir / "data" / "captures" / str(name)
        for name in raw.get("inputs", [])
    ]
    output_name = str(raw.get("output", "")).strip()
    calibrate = bool(raw.get("calibrate", False))
    if not definition_path.exists():
        handler._json(200, {"error": "definition file not found"})
        return
    if not inputs or not all(path.exists() for path in inputs):
        handler._json(200, {"error": "select input captures"})
        return
    if not output_name:
        handler._json(200, {"error": "enter a report filename"})
        return
    out_path = workdir / "reports" / output_name
    if out_path.exists():
        handler._json(200, {"error": f"already exists: {output_name} — "
                            "measurement reports are evidence and are never overwritten"})
        return

    def _job() -> str:
        definition = load_verifier_definition(definition_path)
        rendered = run_verifier_definition(
            definition, input_paths=inputs,
            input_session_mode="single", calibrate_only=calibrate,
        )
        rendered += render_equation_block(
            market_paths=inputs,
            technique_path=definition_path,
            order=(
                "verify-run session=single calibrate="
                f"{calibrate} inputs={len(inputs)}"
            ),
            result_body=rendered,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        tail = "\n".join(rendered.splitlines()[-14:])
        return f"reports/{output_name}\n{tail}"

    handler._json(200, {"job": _start_job(_job)})


def _handle_read_ui_impl(handler) -> None:
    from strategy_verifier.verifier import (
        load_verifier_definition,
        render_equation_block,
        render_reading_report,
        run_reading,
    )

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    workdir = handler.workdir
    definition_path = workdir / "definitions" / str(raw.get("definition", ""))
    inputs = [
        workdir / "data" / "captures" / str(name)
        for name in raw.get("inputs", [])
    ]
    try:
        last_bars = int(str(raw.get("last_bars", "3")))
    except ValueError:
        handler._json(200, {"error": "latest bars must be an integer"})
        return
    if not definition_path.exists() or not inputs or not all(
        path.exists() for path in inputs
    ):
        handler._json(200, {"error": "select a definition and inputs"})
        return

    def _job() -> str:
        definition = load_verifier_definition(definition_path)
        reading = run_reading(
            definition, input_paths=inputs,
            input_session_mode="single", last_bars=last_bars,
            state_dir=workdir / "state",
        )
        rendered = render_reading_report(reading)
        rendered += render_equation_block(
            market_paths=inputs,
            technique_path=definition_path,
            order=(
                "verify-read session=single last_bars="
                f"{last_bars} inputs={len(inputs)}"
            ),
            result_body=rendered,
        )
        return rendered

    handler._json(200, {"job": _start_job(_job)})


def _judge_common(handler):
    raw = _raw_json(handler)
    if raw is None:
        return None, None
    workdir = handler.workdir
    reports = [
        workdir / "reports" / str(name)
        for name in raw.get("reports", [])
    ]
    if not reports or not all(path.exists() for path in reports):
        return raw, "select market reports"
    if not str(raw.get("family", "")).strip():
        return raw, "enter the family name"
    try:
        int(str(raw.get("horizon", "")))
    except ValueError:
        return raw, "primary horizon must be an integer"
    return raw, None


def _handle_diagnose_impl(handler) -> None:
    from strategy_verifier.verifier import (
        render_diagnosis_report,
        render_equation_block,
        run_diagnosis,
    )

    raw, problem = _judge_common(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    if problem:
        handler._json(200, {"error": problem})
        return
    workdir = handler.workdir
    reports = [workdir / "reports" / str(n) for n in raw["reports"]]
    family = str(raw["family"]).strip()
    try:
        result = run_diagnosis(
            family=family, report_paths=reports,
            primary_horizon=int(str(raw["horizon"])),
            primary_threshold=str(raw["threshold"]).strip(),
            arm=str(raw.get("arm", "base_grid")).strip() or "base_grid",
            control_arm=(str(raw.get("control", "")).strip() or None),
            side_arms=None, split_pairs=(),
        )
        rendered = render_diagnosis_report(result)
        rendered += render_equation_block(
            market_paths=reports, technique_path=None,
            order=(
                f"verify-diagnose family={family} cell="
                f"{raw['horizon']}/{str(raw['threshold']).strip()} "
                f"arm={str(raw.get('arm', 'base_grid')).strip() or 'base_grid'}"
            ),
            result_body=rendered,
        )
        out_path = workdir / "reports" / f"{family}_diagnosis.txt"
        out_path.write_text(rendered, encoding="utf-8")
    except Exception as exc:
        handler._json(200, {"error": str(exc)})
        return
    handler._json(200, {
        "path": f"reports/{family}_diagnosis.txt",
        "tail": "\n".join(rendered.splitlines()[:18]),
    })


def _handle_verdict_impl(handler) -> None:
    from strategy_verifier.verifier import (
        render_equation_block,
        render_verdict_report,
        run_verdict,
    )

    raw, problem = _judge_common(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    if problem:
        handler._json(200, {"error": problem})
        return
    workdir = handler.workdir
    reports = [workdir / "reports" / str(n) for n in raw["reports"]]
    family = str(raw["family"]).strip()
    control = str(raw.get("control", "")).strip() or None
    arm = str(raw.get("arm", "base_grid")).strip() or "base_grid"
    try:
        result = run_verdict(
            family=family, report_paths=reports,
            horizon=int(str(raw["horizon"])),
            threshold=str(raw["threshold"]).strip(),
            arm=arm, control_arm=control,
        )
        rendered = render_verdict_report(result)
        rendered += render_equation_block(
            market_paths=reports, technique_path=None,
            order=(
                f"verify-verdict family={family} cell="
                f"{raw['horizon']}/{str(raw['threshold']).strip()} "
                f"arm={arm} control={control}"
            ),
            result_body=rendered,
        )
        out_path = workdir / "reports" / f"{family}_verdict.txt"
        out_path.write_text(rendered, encoding="utf-8")
    except Exception as exc:
        handler._json(200, {"error": str(exc)})
        return
    headline = next(
        (line for line in rendered.splitlines()
         if line.startswith("VERDICT: ")), ""
    )
    handler._json(200, {
        "passed": result.passed,
        "headline": headline,
        "path": f"reports/{family}_verdict.txt",
        "tail": "\n".join(rendered.splitlines()[-16:]),
    })


def _handle_certificate_impl(handler) -> None:
    from strategy_verifier.verifier import render_certificate

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "malformed request body"})
        return
    workdir = handler.workdir
    family = str(raw.get("family", "")).strip()
    verdict_path = workdir / "reports" / f"{family}_verdict.txt"
    diagnosis_path = workdir / "reports" / f"{family}_diagnosis.txt"
    if not verdict_path.exists() or not diagnosis_path.exists():
        handler._json(200, {"error": "run diagnosis and verdict first"})
        return
    try:
        result = render_certificate(
            family=family,
            verdict_text=verdict_path.read_text(encoding="utf-8"),
            diagnosis_text=diagnosis_path.read_text(encoding="utf-8"),
            output_path=workdir / "reports" / f"{family}_certificate.html",
        )
    except Exception as exc:
        handler._json(200, {"error": str(exc)})
        return
    handler._json(200, {
        "path": f"reports/{family}_certificate.html",
        "verdict": result.verdict_name,
    })


def _handle_job_impl(handler) -> None:
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(handler.path).query)
    job_id = (query.get("id") or [""])[0]
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        handler._json(404, {"error": "no such job"})
        return
    handler._json(200, job)


_Handler._handle_measure = _handle_measure_impl
_Handler._handle_read_ui = _handle_read_ui_impl
_Handler._handle_diagnose = _handle_diagnose_impl
_Handler._handle_verdict = _handle_verdict_impl
_Handler._handle_certificate = _handle_certificate_impl
_Handler._handle_job = _handle_job_impl


# ---- J-3: 워크스페이스 (SSE + 라이브 차트) ----

import threading as _threading
from collections import deque as _deque
from decimal import Decimal as _Decimal

_ACCESS_LABEL = "local \u00b7 loopback only"

_WORKSPACE_LOCK = _threading.Lock()
_WORKSPACE: dict = {"reader": None, "candles": None, "family": None,
                    "bar_ms": 0, "current": None, "logged": 0,
                    "base": "", "bar_seconds": 0}


def _workspace_on_tick_factory(bar_ms: int, candles):
    state = {"bar_id": None, "candle": None}

    def _on_tick(tick) -> None:
        price = tick.price
        if bar_ms <= 0:
            candles.append({"t": tick.timestamp.isoformat(),
                            "o": str(price), "h": str(price),
                            "l": str(price), "c": str(price)})
            return
        bar_id = int(tick.timestamp.timestamp() * 1000) // bar_ms
        if bar_id != state["bar_id"]:
            state["bar_id"] = bar_id
            state["candle"] = {"t": tick.timestamp.isoformat(),
                               "o": str(price), "h": str(price),
                               "l": str(price), "c": str(price)}
            candles.append(state["candle"])
        else:
            candle = state["candle"]
            candle["h"] = str(max(_Decimal(candle["h"]), price))
            candle["l"] = str(min(_Decimal(candle["l"]), price))
            candle["c"] = str(price)

    return _on_tick


def _adjudication_tone(lines) -> str:
    text = " ".join(lines)
    if "stage=confirmed" in text:
        return "confirmed"
    if "NOT_CONFIRMED" in text or "stage=closed" in text:
        return "not"
    return "unadjudicated"


def _workspace_body(workdir: Path) -> str:
    definitions = _select_options(
        _list_files(workdir, "definitions", ".toml")
    )
    captures_dir = workdir / "data" / "captures"
    capture_names = [
        path.name for path in sorted(
            captures_dir.glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    ] if captures_dir.exists() else []
    captures = _select_options(
        capture_names,
        empty="no captures — record or convert first")
    return ("""<div class="workspace">
<div class="work-head"><div>
<p class="micro">WORKSPACE / LIVE RECORD VIEW</p>
<h1 id="wsTitle">no feed</h1></div>
<div class="verdict-picker">
<select id="wdef">""" + definitions + """</select>
<select id="wbase">""" + captures + """</select>
<button class="secondary" onclick="startWs()">Follow</button>
<button class="secondary" onclick="stopWs()">Stop</button>
</div></div>
<div id="banner" class="adjudication unadjudicated">UNADJUDICATED
— no feed is being followed yet.</div>
<div class="chart-wrap"><svg id="chart" viewBox="0 0 680 240"
preserveAspectRatio="none"></svg></div>
<div class="latest" id="latest"><span class="micro">LATEST BAR
CONDITIONS</span><span>none</span></div>
<footer class="work-footer">No instruction to act exists on this
screen. Whether to act is solely yours.</footer>
<section class="detections"><header><span>DETECTIONS</span>
<span class="micro">facts only · appended to
reports/detections-&lt;family&gt;.jsonl</span></header>
<div class="detect-head"><span>TIME</span><span>SOURCE</span>
<span>TF</span><span>SIDE</span><span>MATCHED CONDITIONS</span>
<span>VERDICT</span></div>
<div id="detrows"></div></section>
</div>
<script>
const es = new EventSource('/api/stream');
es.onmessage = e => render(JSON.parse(e.data));
async function startWs() {
  const response = await fetch('/api/workspace/start', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      definition: document.getElementById('wdef').value,
      base: document.getElementById('wbase').value,
    })});
  const data = await response.json();
  if (data.error) { alert(data.error); }
}
async function stopWs() {
  await fetch('/api/workspace/stop', {method:'POST'});
  document.getElementById('wsTitle').textContent = 'no feed';
  document.getElementById('chart').innerHTML = '';
  document.getElementById('latest').innerHTML =
    '<span class="micro">LATEST BAR CONDITIONS</span><span>none</span>';
  document.getElementById('detrows').innerHTML = '';
  detCount = 0;
  const banner = document.getElementById('banner');
  banner.className = 'adjudication unadjudicated';
  banner.textContent = 'UNADJUDICATED — no feed is being followed.';
}
let detCount = 0;
function render(state) {
  document.getElementById('wsTitle').textContent = state.family
    ? state.family + ' \u00b7 following ' + state.base : 'no feed';
  if (state.family) {
    document.getElementById('wdef').value = state.definition;
    document.getElementById('wbase').value = state.base;
  }
  renderDetections(state);
  const banner = document.getElementById('banner');
  banner.className = 'adjudication ' + state.tone;
  banner.textContent = state.adjudication.join(' · ');
  const chips = state.latest ? Object.entries(
    state.latest.conditions).map(([k,v]) =>
    '<span class="badge '+(v?'yes':'no')+'">'+k+' = '+
    (v?'yes':'no')+'</span>').join('') : '<span>none</span>';
  document.getElementById('latest').innerHTML =
    '<span class="micro">LATEST BAR CONDITIONS</span>' + chips +
    (!state.latest && state.candles && state.candles.length
      ? '<span>warming up — no closed bar yet</span>' : '') +
    (state.latest ? '<span>direction: '+state.latest.direction+
     '</span><span>occurred: '+
     (state.latest.occurred?'yes':'no')+'</span>' : '');
  drawChart(state.candles, state.markers);
}
function renderDetections(state) {
  const rows = document.getElementById('detrows');
  const markers = (state.markers || []);
  rows.innerHTML = markers.slice().reverse().map(m => {
    const held = Object.entries(m.conditions || {})
      .filter(([,v]) => v).map(([k]) => k).join(' \u00b7 ')
      || 'none';
    return '<div class="detect-row" title=' + "'" +
      JSON.stringify(m.series || {}) + "'" + '><code>' +
      m.timestamp + '</code><code>' + (state.base||'') +
      '</code><code>' + (state.bar_seconds||0) + 's</code><code>' +
      (m.side === 'long' ? 'L' : 'S') + '</code><code>' + held +
      '</code><span class="badge ' +
      (state.tone==='confirmed'?'confirmed':
       state.tone==='not'?'not':'unadjudicated') + '">' +
      state.verdict_label + '</span></div>';
  }).join('');
  if (markers.length > detCount && detCount >= 0) {
    const last = markers[markers.length - 1];
    const wrap = document.querySelector('.chart-wrap');
    const toast = document.createElement('div');
    toast.className = 'match-toast';
    toast.textContent = 'MATCH \u00b7 ' + last.timestamp +
      ' \u00b7 ' + last.side + '-side \u00b7 verdict ' +
      state.verdict_label;
    wrap.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  }
  detCount = markers.length;
}
function drawChart(candles, markers) {
  const svg = document.getElementById('chart');
  if (!candles || !candles.length) { svg.innerHTML=''; return; }
  const view = candles.slice(-40);
  const slots = Math.max(view.length, 20);
  let low = Infinity, high = -Infinity;
  view.forEach(c => { low = Math.min(low, +c.l);
                      high = Math.max(high, +c.h); });
  if (!(high > low)) { high = low + 1; }
  const y = p => 230 - ((+p - low) / (high - low)) * 220;
  const width = 680 / slots;
  let parts = [];
  [0.25,0.5,0.75].forEach(g => parts.push(
    '<line class="grid" x1="0" x2="680" y1="'+(230-220*g)+
    '" y2="'+(230-220*g)+'"/>'));
  const times = {};
  view.forEach((c, i) => {
    const x = i * width + width * 0.15;
    const bodyWidth = width * 0.7;
    times[c.t] = x + bodyWidth / 2;
    const up = +c.c >= +c.o;
    parts.push('<line class="wick" x1="'+(x+bodyWidth/2)+
      '" x2="'+(x+bodyWidth/2)+'" y1="'+y(c.h)+'" y2="'+y(c.l)+'"/>');
    const top = y(Math.max(+c.o, +c.c));
    const heightPx = Math.max(2, Math.abs(y(c.o) - y(c.c)));
    parts.push('<rect class="bar '+(up?'up':'down')+'" x="'+x+
      '" y="'+top+'" width="'+bodyWidth+'" height="'+heightPx+'"/>');
  });
  (markers || []).slice(-30).forEach(m => {
    const barTime = m.timestamp;
    let x = null;
    Object.keys(times).forEach(t => {
      if (t <= barTime) { x = times[t]; }
    });
    if (x === null) { return; }
    const my = y(m.price);
    const hexagon = [[0,-7],[6,-3.5],[6,3.5],[0,7],[-6,3.5],[-6,-3.5]]
      .map(([dx,dy],i)=>(i?'L':'M')+(x+dx)+' '+(my+dy)).join(' ')+' Z';
    parts.push('<g class="occurrence"><path d="'+hexagon+'"><title>'+m.timestamp+' '+m.side+
      ' price='+m.price+' outcome='+m.outcome+
      '</title></path><text x="'+x+'" y="'+(my+18)+'">'+
      (m.side === 'long' ? 'L' : 'S')+'</text></g>');
  });
  const mid = (low + high) / 2;
  const span = high - low;
  const decimals = span < 0.5 ? 3 : span < 5 ? 2 : 1;
  [[high, 12], [mid, 122], [low, 228]].forEach(([p, yy]) =>
    parts.push('<text class="axis" x="676" y="'+yy+
      '" text-anchor="end">'+(+p).toFixed(decimals)+'</text>'));
  const clock = t => t.slice(11, 16);
  parts.push('<text class="axis" x="4" y="238">'+
    clock(view[0].t)+'</text>');
  if (view.length > 1) {
    parts.push('<text class="axis" x="'+(view.length*width-6)+
      '" y="238" text-anchor="end">'+
      clock(view[view.length-1].t)+'</text>');
  }
  svg.innerHTML = parts.join('');
}
</script>""")


def workspace_attach(workdir: Path, definition_name: str,
                     base_name: str) -> str | None:
    """Attach the global workspace reader; returns an error string
    or None. Shared by the start endpoint and serve-mode boot."""
    from strategy_verifier.verifier import (
        LiveReader,
        load_verifier_definition,
    )
    from strategy_verifier.verifier.graph import GraphDefinition

    definition_path = workdir / "definitions" / definition_name
    base = workdir / "data" / "captures" / base_name
    if not definition_path.exists() or not base.exists():
        return "pick a definition and a capture base file"
    try:
        definition = load_verifier_definition(definition_path)
        if not isinstance(definition, GraphDefinition):
            raise DomainValidationError(
                "the workspace follows graph_v1 definitions"
            )
        candles = _deque(maxlen=400)
        reader = LiveReader(
            definition, base,
            on_tick=_workspace_on_tick_factory(
                definition.bar_seconds * 1000, candles
            ),
        )
    except Exception as exc:
        return str(exc)
    seen: set = set()
    evidence_path = (workdir / "reports"
                     / f"detections-{definition.family}.jsonl")
    if evidence_path.exists():
        for line in evidence_path.read_text(
                encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                seen.add((row["timestamp"], row["side"],
                          row["base"]))
            except (ValueError, KeyError):
                continue
    with _WORKSPACE_LOCK:
        old = _WORKSPACE.get("reader")
        if old is not None:
            old.close()
        _WORKSPACE.update({
            "reader": reader, "candles": candles,
            "family": definition.family,
            "workdir": workdir, "logged": 0, "seen": seen,
            "base": base_name,
            "definition": definition_name,
            "bar_seconds": definition.bar_seconds,
        })
    return None


def _handle_workspace_start(handler) -> None:
    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "bad request body"})
        return
    error = workspace_attach(
        handler.workdir,
        str(raw.get("definition", "")),
        str(raw.get("base", "")),
    )
    if error is not None:
        handler._json(200, {"error": error})
        return
    with _WORKSPACE_LOCK:
        family = _WORKSPACE["family"]
        base_name = _WORKSPACE["base"]
    handler._json(200, {"family": family, "base": base_name})


def _handle_workspace_stop(handler) -> None:
    with _WORKSPACE_LOCK:
        reader = _WORKSPACE.get("reader")
        if reader is not None:
            reader.close()
        _WORKSPACE.update({"reader": None, "candles": None,
                           "family": None})
    handler._json(200, {"stopped": True})


def _workspace_state_json() -> str:
    from strategy_verifier.verifier.reading import _adjudication

    with _WORKSPACE_LOCK:
        reader = _WORKSPACE.get("reader")
        if reader is None:
            payload = {
                "tone": "unadjudicated",
                "adjudication": ["UNADJUDICATED — no feed is being "
                                 "followed."],
                "candles": [], "markers": [], "latest": None,
            }
            return json.dumps(payload, ensure_ascii=False)
        reader.drain()
        peek = reader.session.peek()
        lines = list(_adjudication(
            _WORKSPACE["workdir"] / "state", _WORKSPACE["family"]
        ))
        latest = (
            peek["latest_bars"][-1] if peek["latest_bars"] else None
        )
        all_markers = list(peek["markers"])
        logged = _WORKSPACE.get("logged", 0)
        if len(all_markers) > logged:
            tone = _adjudication_tone(lines)
            label = {"not": "NOT_CONFIRMED",
                     "confirmed": "CONFIRMED"}.get(
                tone, "UNADJUDICATED")
            evidence = (
                _WORKSPACE["workdir"] / "reports"
                / f"detections-{_WORKSPACE['family']}.jsonl"
            )
            evidence.parent.mkdir(parents=True, exist_ok=True)
            seen = _WORKSPACE.setdefault("seen", set())
            with evidence.open("a", encoding="utf-8") as handle:
                for marker in all_markers[logged:]:
                    key = (marker["timestamp"], marker["side"],
                           _WORKSPACE["base"])
                    if key in seen:
                        continue
                    seen.add(key)
                    handle.write(json.dumps({
                        "timestamp": marker["timestamp"],
                        "base": _WORKSPACE["base"],
                        "bar_seconds": _WORKSPACE["bar_seconds"],
                        "side": marker["side"],
                        "price": marker["price"],
                        "conditions": marker.get("conditions", {}),
                        "series": marker.get("series", {}),
                        "family_verdict": label,
                    }, ensure_ascii=False) + "\n")
            _WORKSPACE["logged"] = len(all_markers)
        tone_now = _adjudication_tone(lines)
        payload = {
            "tone": tone_now,
            "verdict_label": {"not": "NOT_CONFIRMED",
                              "confirmed": "CONFIRMED"}.get(
                tone_now, "UNADJUDICATED"),
            "family": _WORKSPACE["family"],
            "base": _WORKSPACE["base"],
            "definition": _WORKSPACE.get("definition", ""),
            "bar_seconds": _WORKSPACE["bar_seconds"],
            "adjudication": lines,
            "candles": list(_WORKSPACE["candles"]),
            "markers": list(peek["markers"])[-30:],
            "latest": latest,
            "event_count": peek["event_count"],
            "bar_count": peek["bar_count"],
        }
        return json.dumps(payload, ensure_ascii=False)


def _handle_stream(handler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    import time as _time

    try:
        while True:
            data = _workspace_state_json()
            handler.wfile.write(
                b"data: " + data.encode("utf-8") + b"\n\n"
            )
            handler.wfile.flush()
            _time.sleep(1.0)
    except (BrokenPipeError, ConnectionResetError, OSError):
        return


_Handler._handle_workspace_start = _handle_workspace_start
_Handler._handle_workspace_stop = _handle_workspace_stop
_Handler._handle_stream = _handle_stream


def _chat_panel() -> str:
    quick = "".join(
        "<button type='button' class='qc'>" + q + "</button>"
        for q in ("Why this match?", "This rule's record",
                  "Why this verdict?", "Which conditions held?")
    )
    return ("""<aside class="chat"><header><div>
<h2>Ask the record</h2>
<p>Explains what happened. Never advises.</p></div>
<button onclick="closeChat()" aria-label="close chat">\u00d7</button></header>
<div class="messages" id="chatlog">
<div class="system">I describe occurrences and the adjudicated
record. Whether to act is entirely yours.</div></div>
<div class="quick">""" + quick + """</div>
<form onsubmit="return sendChat(event)">
<input id="chatText" placeholder="Ask about the record">
<button>Send</button></form></aside>
<button class="chat-fab" onclick="openChat()">ASK</button>
<script>
function openChat() {
  document.querySelector('.chat').classList.add('open');
  document.querySelector('.sidebar').classList.remove('open');
}
function closeChat() {
  document.querySelector('.chat').classList.remove('open');
}
function bubble(cls, text, mode) {
  const log = document.getElementById('chatlog');
  const node = document.createElement(cls === 'user' ? 'p' : 'div');
  node.className = cls;
  node.textContent = text + (mode && mode !== 'llm'
    ? '  [' + mode + ']' : '');
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
}
async function askChat(question) {
  bubble('user', question);
  const response = await fetch('/api/chat', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({question: question})});
  const data = await response.json();
  bubble('system', data.text, data.mode);
}
document.querySelectorAll('.quick .qc').forEach(function(button) {
  button.onclick = function() { askChat(button.textContent); };
});
function sendChat(event) {
  event.preventDefault();
  const input = document.getElementById('chatText');
  if (input.value.trim()) askChat(input.value.trim());
  input.value = '';
  return false;
}
</script>""")


def _handle_chat(handler) -> None:
    from strategy_verifier.verifier.chat import answer, build_context
    from strategy_verifier.verifier.reading import _adjudication

    raw = _raw_json(handler)
    if raw is None:
        handler._json(400, {"error": "bad request body"})
        return
    question = str(raw.get("question", "")).strip()
    with _WORKSPACE_LOCK:
        reader = _WORKSPACE.get("reader")
        if reader is None:
            context = build_context(None, [], "UNADJUDICATED",
                                    [], None, 0)
        else:
            reader.drain()
            peek = reader.session.peek()
            lines = list(_adjudication(
                _WORKSPACE["workdir"] / "state",
                _WORKSPACE["family"],
            ))
            tone = _adjudication_tone(lines)
            label = {"not": "NOT_CONFIRMED",
                     "confirmed": "CONFIRMED"}.get(
                tone, "UNADJUDICATED")
            latest = (
                peek["latest_bars"][-1]
                if peek["latest_bars"] else None
            )
            context = build_context(
                _WORKSPACE["family"], lines, label,
                list(peek["markers"]), latest, peek["bar_count"],
            )
    text, mode = answer(question, context)
    handler._json(200, {"text": text, "mode": mode})


_Handler._handle_chat = _handle_chat
