"""Frontend embebido para el piano roll de YuE2 Studio 4.

El editor se renderiza dentro de un iframe autocontenido. El estado musical
viaja como JSON compatible con :mod:`tools.piano_roll_score`. Al pulsar
"Aplicar al ABC" el iframe envía el score al documento Gradio mediante
``postMessage``; ``PIANOROLL_BRIDGE_JS`` lo entrega a un Textbox oculto y
activa el callback Python correspondiente.
"""
from __future__ import annotations

import base64
import json

from . import piano_roll_score


PIANOROLL_BRIDGE_CSS = """
.yue2-pr-bridge-hidden { display: none !important; }
"""

PIANOROLL_BRIDGE_JS = r"""
() => {
  if (window.__yue2PianoRollBridgeInstalled) return;
  window.__yue2PianoRollBridgeInstalled = true;

  function setNativeValue(element, value) {
    const proto = element.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) descriptor.set.call(element, value);
    else element.value = value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || message.type !== "yue2-pianoroll-apply") return;
    if (typeof message.workspace !== "string" || !message.workspace) return;

    const safe = (window.CSS && CSS.escape)
      ? CSS.escape(message.workspace)
      : message.workspace.replace(/[^A-Za-z0-9_-]/g, "");
    const payloadRoot = document.querySelector(`#${safe}-payload`);
    const applyRoot = document.querySelector(`#${safe}-apply`);
    const field = payloadRoot?.querySelector("textarea, input");
    const button = applyRoot?.matches("button")
      ? applyRoot
      : applyRoot?.querySelector("button");

    if (!field || !button) {
      console.error("YuE2 Studio: no se encontró el bridge del piano roll", message.workspace);
      return;
    }

    try {
      setNativeValue(field, JSON.stringify(message.payload));
      window.setTimeout(() => button.click(), 0);
    } catch (error) {
      console.error("YuE2 Studio: fallo al aplicar piano roll", error);
    }
  });
}
"""


def _empty_panel(message: str) -> str:
    return (
        '<div style="padding:28px;border:1px solid var(--border-color-primary,#d0d0d0);'
        'border-radius:10px;text-align:center;opacity:.78">'
        f"{message}</div>"
    )


def build_piano_roll_editor(
    abc_text: str,
    workspace_id: str,
    *,
    interactive: bool = True,
    height: int = 720,
) -> str:
    """Renderiza un piano roll para un ABC nativo de YuE2."""
    if not (abc_text or "").strip():
        return _empty_panel("El piano roll aparecerá aquí cuando haya un score ABC.")

    try:
        data = piano_roll_score.inspect_score(abc_text)
    except Exception as exc:
        return _empty_panel(f"No se pudo construir el piano roll: {exc}")

    if not data.get("grid_available", False):
        return _empty_panel(
            "Este score contiene cambios internos de tonalidad o compás. "
            "Studio 4 conserva el ABC, pero el piano roll MVP queda en modo no editable para este caso."
        )

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    workspace = json.dumps(workspace_id)
    editable = "true" if interactive else "false"

    inner = f'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:#151218; color:#eee9f1; }}
.toolbar {{ display:flex; flex-wrap:wrap; gap:8px 12px; align-items:end; padding:10px 12px; background:#211c25; border-bottom:1px solid #3c3442; }}
.toolbar label {{ display:flex; flex-direction:column; gap:3px; font-size:11px; color:#bfb5c6; }}
.toolbar select,.toolbar input,.toolbar button {{ min-height:32px; border-radius:7px; border:1px solid #554b5e; background:#302936; color:#f6f0f7; padding:4px 9px; }}
.toolbar button {{ cursor:pointer; font-weight:600; }}
.toolbar button.primary {{ background:#6b3fa0; border-color:#8e64bd; }}
.toolbar button:disabled {{ opacity:.45; cursor:not-allowed; }}
.legend {{ display:flex; gap:12px; align-items:center; margin-left:auto; font-size:12px; color:#cfc5d5; }}
.dot {{ width:11px; height:11px; border-radius:3px; display:inline-block; margin-right:4px; vertical-align:-1px; }}
.status {{ padding:6px 12px; min-height:30px; font-size:12px; color:#cbc1d1; border-bottom:1px solid #332c38; }}
.status.error {{ color:#ffb3b3; }}
.roll-wrap {{ height:620px; overflow:auto; position:relative; background:#17131a; }}
svg {{ display:block; user-select:none; touch-action:none; }}
.help {{ padding:8px 12px 11px; border-top:1px solid #332c38; font-size:11px; color:#9f95a6; line-height:1.45; }}
</style>
</head>
<body>
<div class="toolbar">
  <label>Pista activa
    <select id="track"><option value="Vocal">Vocal</option><option value="Ins">Ins</option></select>
  </label>
  <label>Cuantización
    <select id="snap"><option value="256">1/4</option><option value="128">1/8</option><option value="64" selected>1/16</option></select>
  </label>
  <label>Zoom horizontal
    <input id="zoom" type="range" min="0.55" max="2.8" step="0.05" value="1">
  </label>
  <button id="undo" type="button">↶ Deshacer</button>
  <button id="redo" type="button">↷ Rehacer</button>
  <button id="delete" type="button">Borrar selección</button>
  <button id="fit" type="button">Centrar alturas</button>
  <button id="apply" class="primary" type="button">Aplicar al ABC</button>
  <div class="legend"><span><i class="dot" style="background:#bb7df0"></i>Vocal</span><span><i class="dot" style="background:#46ceb2"></i>Ins</span></div>
</div>
<div id="status" class="status">Listo.</div>
<div id="roll" class="roll-wrap"></div>
<div class="help">Clic en espacio vacío: crea una nota · arrastrar nota: mover · arrastrar borde derecho: duración · Shift+clic: selección múltiple · Supr: borrar · Ctrl/Cmd+Z: deshacer · Ctrl/Cmd+Shift+Z: rehacer.</div>
<script>
(() => {{
  const WORKSPACE = {workspace};
  const INTERACTIVE = {editable};
  const source = {payload};
  let score = structuredClone(source);
  let history = [];
  let future = [];
  let selected = new Set();
  let activeTrack = "Vocal";
  let snap = 64;
  let zoom = 1;
  let low = 48;
  let high = 84;
  const ROW = 16;
  const KEY_W = 58;
  const TOP = 54;
  const CHORD_H = 24;
  const BASE_PX_PER_BEAT = 46;
  const colors = {{ Vocal: "#bb7df0", Ins: "#46ceb2" }};
  const dimColors = {{ Vocal: "#694683", Ins: "#287968" }};
  const rollEl = document.getElementById("roll");
  const statusEl = document.getElementById("status");
  const trackEl = document.getElementById("track");
  const snapEl = document.getElementById("snap");
  const zoomEl = document.getElementById("zoom");
  const applyEl = document.getElementById("apply");
  const deleteEl = document.getElementById("delete");
  const undoEl = document.getElementById("undo");
  const redoEl = document.getElementById("redo");

  if (!INTERACTIVE) {{
    applyEl.style.display = "none";
    deleteEl.disabled = true;
  }}

  function pitchName(p) {{
    const names = ["C","C♯","D","D♯","E","F","F♯","G","G♯","A","A♯","B"];
    return names[((p % 12) + 12) % 12] + (Math.floor(p / 12) - 1);
  }}
  function clamp(v,a,b) {{ return Math.max(a, Math.min(b, v)); }}
  function q(v) {{ return Math.round(v / snap) * snap; }}
  function clone() {{ return structuredClone(score); }}
  function noteId(track,index) {{ return `${{track}}:${{index}}`; }}
  function parseId(id) {{ const i=id.lastIndexOf(":"); return [id.slice(0,i), Number(id.slice(i+1))]; }}
  function setStatus(text,error=false) {{ statusEl.textContent=text; statusEl.className = "status" + (error ? " error" : ""); }}
  function pushHistory() {{ history.push(clone()); if (history.length > 80) history.shift(); future=[]; }}
  function undo() {{ if (!history.length || !INTERACTIVE) return; future.push(clone()); score=history.pop(); selected.clear(); draw(); }}
  function redo() {{ if (!future.length || !INTERACTIVE) return; history.push(clone()); score=future.pop(); selected.clear(); draw(); }}
  function allNotes() {{ return Object.entries(score.roll.tracks).flatMap(([track,notes]) => notes.map((n,index)=>({{track,index,...n}}))); }}
  function fitPitchRange() {{
    const notes=allNotes();
    if (!notes.length) {{ low=48; high=84; return; }}
    let mn=Math.min(...notes.map(n=>n.pitch)), mx=Math.max(...notes.map(n=>n.pitch));
    const span=Math.max(30, mx-mn+11), mid=(mn+mx)/2;
    low=clamp(Math.floor(mid-span/2),0,127-span);
    high=Math.min(127, low+span);
  }}
  function validateLocal() {{
    const total=score.roll.total_ticks;
    for (const track of ["Vocal","Ins"]) {{
      const notes=[...score.roll.tracks[track]].sort((a,b)=>a.start-b.start || a.pitch-b.pitch);
      let end=0;
      for (const n of notes) {{
        if (!Number.isInteger(n.start)||!Number.isInteger(n.duration)||!Number.isInteger(n.pitch)) return `${{track}}: valores no enteros`;
        if (n.pitch<0||n.pitch>127||n.duration<1||n.start<0||n.start+n.duration>total) return `${{track}}: nota fuera de rango`;
        if (n.start<end) return `${{track}}: hay notas solapadas`;
        end=n.start+n.duration;
      }}
    }}
    return "";
  }}
  function updateButtons() {{ undoEl.disabled=!history.length||!INTERACTIVE; redoEl.disabled=!future.length||!INTERACTIVE; deleteEl.disabled=!selected.size||!INTERACTIVE; }}

  function draw() {{
    const keepLeft=rollEl.scrollLeft, keepTop=rollEl.scrollTop;
    rollEl.replaceChildren();
    const ppq=score.roll.ppq || 256;
    const pxBeat=BASE_PX_PER_BEAT*zoom;
    const total=score.roll.total_ticks;
    const pitches=high-low+1;
    const width=Math.max(900, KEY_W + total/ppq*pxBeat + 20);
    const height=TOP+CHORD_H+pitches*ROW+2;
    const ns="http://www.w3.org/2000/svg";
    const svg=document.createElementNS(ns,"svg");
    svg.setAttribute("width",width); svg.setAttribute("height",height); svg.setAttribute("viewBox",`0 0 ${{width}} ${{height}}`);
    svg.setAttribute("tabindex","0");
    rollEl.append(svg);
    const shape=(tag,attrs,parent=svg)=>{{ const el=document.createElementNS(ns,tag); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v))); parent.append(el); return el; }};
    const text=(x,y,value,attrs={{}})=>{{ const el=shape("text",{{x,y,...attrs}}); el.textContent=value; return el; }};
    const yPitch=p=>TOP+CHORD_H+(high-p)*ROW;
    const xTick=t=>KEY_W+t/ppq*pxBeat;

    for (let p=high;p>=low;p--) {{
      const y=yPitch(p), black=[1,3,6,8,10].includes(((p%12)+12)%12);
      shape("rect",{{x:0,y,width,height:ROW,fill:black?"#201a24":"#2a232e",stroke:"#3c3341","stroke-width":0.45}});
      shape("rect",{{x:0,y,width:KEY_W,height:ROW,fill:black?"#302738":"#e8e2eb",stroke:"#65576d","stroke-width":0.6}});
      text(4,y+12,pitchName(p),{{fill:black?"#e6dceb":"#241e28","font-size":10,"pointer-events":"none"}});
    }}
    shape("rect",{{x:0,y:0,width:KEY_W,height:TOP+CHORD_H,fill:"#241c29"}});
    for (let t=0;t<=total;t+=snap) {{
      const x=xTick(t), bar=t%score.roll.bar_ticks===0, beat=t%ppq===0;
      shape("line",{{x1:x,x2:x,y1:TOP+CHORD_H,y2:height,stroke:bar?"#8d7b98":beat?"#55495d":"#39313e","stroke-width":bar?1.2:beat?0.8:0.45}});
    }}
    const bars=Math.ceil(total/score.roll.bar_ticks);
    const every=Math.max(1,Math.ceil(55/(score.roll.bar_ticks/ppq*pxBeat)));
    for (let b=0;b<bars;b+=every) text(xTick(b*score.roll.bar_ticks)+4,18,`Compás ${{b+1}}`,{{fill:"#d7cddd","font-size":10}});
    for (const section of score.roll.sections||[]) {{
      const start=section.start*score.roll.bar_ticks;
      const x=xTick(start);
      shape("line",{{x1:x,x2:x,y1:0,y2:height,stroke:"#c4a7d7","stroke-width":1.2,"stroke-dasharray":"4 3"}});
      text(x+5,35,section.name||"section",{{fill:"#d8bde8","font-size":11,"font-weight":600}});
    }}
    shape("rect",{{x:KEY_W,y:TOP,width:width-KEY_W,height:CHORD_H,fill:"#1f3032",stroke:"#395154"}});
    for (const chord of score.roll.chords||[]) text(xTick(chord.start)+3,TOP+16,chord.symbol,{{fill:"#9ee4da","font-size":11,"font-weight":600}});

    for (const track of ["Ins","Vocal"]) {{
      score.roll.tracks[track].forEach((n,index)=>{{
        if (n.pitch<low||n.pitch>high) return;
        const x=xTick(n.start), w=Math.max(3,n.duration/ppq*pxBeat), y=yPitch(n.pitch)+1;
        const id=noteId(track,index), isSel=selected.has(id), active=track===activeTrack;
        const rect=shape("rect",{{x,y,width:w,height:ROW-2,rx:3,fill:active?colors[track]:dimColors[track],stroke:isSel?"#ffffff":"#201926","stroke-width":isSel?2:0.7,"data-track":track,"data-index":index,cursor:INTERACTIVE?"grab":"default"}});
        const title=shape("title",{{}},rect); title.textContent=`${{track}} · ${{pitchName(n.pitch)}} · inicio ${{(n.start/ppq).toFixed(2)}} · duración ${{(n.duration/ppq).toFixed(2)}} negras`;
        if (w>=10 && INTERACTIVE) shape("rect",{{x:x+w-5,y:y+2,width:4,height:ROW-6,fill:"#fff",opacity:.75,"data-track":track,"data-index":index,"data-resize":"1",cursor:"ew-resize"}});
      }});
    }}

    function point(ev) {{ const r=svg.getBoundingClientRect(); return {{x:(ev.clientX-r.left)*width/r.width,y:(ev.clientY-r.top)*height/r.height}}; }}
    function pitchAt(y) {{ return clamp(high-Math.floor((y-(TOP+CHORD_H))/ROW),0,127); }}
    function tickAt(x) {{ return clamp(q((x-KEY_W)/pxBeat*ppq),0,total); }}

    svg.onpointerdown=(ev)=>{{
      if (!INTERACTIVE || ev.button!==0) return;
      const p0=point(ev);
      if (p0.x<KEY_W || p0.y<TOP+CHORD_H) return;
      ev.preventDefault();
      const target=ev.target;
      const track=target.getAttribute("data-track");
      const indexText=target.getAttribute("data-index");
      const resize=target.getAttribute("data-resize")==="1";
      const index=indexText===null?null:Number(indexText);

      if (track && index!==null) {{
        const id=noteId(track,index);
        if (ev.shiftKey) {{
          if (selected.has(id)) selected.delete(id); else selected.add(id);
          updateButtons(); draw(); return;
        }}
        if (!selected.has(id)) selected=new Set([id]);
        activeTrack=track; trackEl.value=track;
        const originals=new Map([...selected].map(sel=>{{ const [tr,ix]=parseId(sel); return [sel,structuredClone(score.roll.tracks[tr][ix])]; }}));
        const startP=p0;
        let changed=false;
        const before=clone();
        const move=(e)=>{{
          const p=point(e); const dx=q((p.x-startP.x)/pxBeat*ppq); const dy=Math.round((startP.y-p.y)/ROW);
          if (resize && selected.size===1) {{
            const old=originals.get(id), rawEnd=old.start+old.duration+(p.x-startP.x)/pxBeat*ppq;
            const end=clamp(q(rawEnd),old.start+snap,total);
            score.roll.tracks[track][index].duration=Math.max(snap,end-old.start);
            changed=true;
          }} else {{
            for (const sel of selected) {{
              const [tr,ix]=parseId(sel), old=originals.get(sel), n=score.roll.tracks[tr][ix];
              n.start=clamp(old.start+dx,0,total-old.duration);
              n.pitch=clamp(old.pitch+dy,0,127);
            }}
            changed=true;
          }}
          draw();
        }};
        const finish=()=>{{
          window.removeEventListener("pointermove",move);
          if(changed){{ history.push(before); if(history.length>80)history.shift(); future=[]; }}
          const err=validateLocal(); setStatus(err||"Edición local lista para aplicar.",!!err); draw();
        }};
        window.addEventListener("pointermove",move);
        window.addEventListener("pointerup",finish,{{once:true}});
        return;
      }}

      const before=clone();
      const start=clamp(tickAt(p0.x),0,total-snap), pitch=pitchAt(p0.y);
      const note={{start,duration:snap,pitch}};
      score.roll.tracks[activeTrack].push(note);
      score.roll.tracks[activeTrack].sort((a,b)=>a.start-b.start||a.pitch-b.pitch);
      history.push(before); if(history.length>80)history.shift(); future=[];
      const ix=score.roll.tracks[activeTrack].indexOf(note); selected=new Set([noteId(activeTrack,ix)]);
      const err=validateLocal(); setStatus(err||"Nota creada.",!!err); draw();
    }};

    updateButtons();
    requestAnimationFrame(()=>{{ rollEl.scrollLeft=keepLeft; rollEl.scrollTop=keepTop; }});
  }}

  function deleteSelected() {{
    if (!selected.size || !INTERACTIVE) return;
    pushHistory();
    const grouped={{Vocal:[],Ins:[]}};
    for (const id of selected) {{ const [tr,ix]=parseId(id); grouped[tr].push(ix); }}
    for (const tr of ["Vocal","Ins"]) grouped[tr].sort((a,b)=>b-a).forEach(ix=>score.roll.tracks[tr].splice(ix,1));
    selected.clear(); setStatus("Selección borrada."); draw();
  }}

  trackEl.onchange=()=>{{activeTrack=trackEl.value; draw();}};
  snapEl.onchange=()=>{{snap=Number(snapEl.value); draw();}};
  zoomEl.oninput=()=>{{zoom=Number(zoomEl.value); draw();}};
  undoEl.onclick=undo; redoEl.onclick=redo; deleteEl.onclick=deleteSelected;
  document.getElementById("fit").onclick=()=>{{fitPitchRange(); draw();}};
  applyEl.onclick=()=>{{
    const err=validateLocal();
    if(err){{setStatus(err,true);return;}}
    window.parent.postMessage({{type:"yue2-pianoroll-apply",workspace:WORKSPACE,payload:score}},"*");
    setStatus("Enviando cambios a Python para reconstruir y validar el ABC…");
  }};
  window.addEventListener("keydown",(ev)=>{{
    if(ev.key==="Delete"||ev.key==="Backspace"){{ if(document.activeElement?.tagName!=="INPUT"&&document.activeElement?.tagName!=="SELECT"){{ev.preventDefault();deleteSelected();}} }}
    if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==="z"){{ev.preventDefault();ev.shiftKey?redo():undo();}}
  }});
  fitPitchRange(); draw();
}})();
</script>
</body>
</html>'''

    encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    safe_height = max(420, min(1200, int(height)))
    return (
        '<iframe '
        f'src="data:text/html;base64,{encoded}" '
        f'style="width:100%;height:{safe_height}px;border:1px solid #3c3442;'
        'border-radius:10px;background:#151218;" loading="lazy"></iframe>'
    )
