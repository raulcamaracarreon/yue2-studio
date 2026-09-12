"""Demo aislado del piano roll de YuE2 Studio 4.

No carga YuE2 ni SheetSage2: sirve para probar edición ABC ↔ piano roll sin
consumir VRAM antes de integrarla en la aplicación principal.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import gradio as gr

from tools import piano_roll_score
from tools.piano_roll_ui import (
    PIANOROLL_BRIDGE_CSS,
    PIANOROLL_BRIDGE_JS,
    build_piano_roll_editor,
)

WORKSPACE = "studio4-demo"

DEFAULT_ABC = '''X:1
T:
M:4/4
L:1/16
Q:1/4=90
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Dm
% demo
V: Vocal
"Dm"A4A4c4A4|"Gm"G8z8|
V: Ins
D4F4A4F4|G4B4d4B4|
'''


def build_score_viewer(abc_text: str) -> str:
    if not (abc_text or "").strip():
        return '<div style="padding:24px;text-align:center;opacity:.7">La partitura aparecerá aquí.</div>'
    abc_json = json.dumps(abc_text)
    inner = f'''<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/abcjs@6.5.2/dist/abcjs-basic-min.js"></script>
<style>body{{margin:0;padding:14px;background:#fff;color:#111;font-family:Arial,sans-serif}}#paper{{width:100%;overflow-x:auto}}#err{{color:#a00;white-space:pre-wrap}}</style>
</head><body><div id="paper"></div><div id="err"></div><script>
const abc={abc_json};
try{{if(!window.ABCJS)throw new Error("No se pudo cargar abcjs");ABCJS.renderAbc("paper",abc,{{responsive:"resize",add_classes:true,staffwidth:1050}})}}catch(e){{document.getElementById("err").textContent=String(e)}}
</script></body></html>'''
    encoded = base64.b64encode(inner.encode()).decode()
    return f'<iframe src="data:text/html;base64,{encoded}" style="width:100%;height:650px;border:1px solid #ccc;border-radius:8px;background:#fff"></iframe>'


def _summary(info: dict, prefix: str = "Score cargado") -> str:
    roll = info["roll"]
    return (
        f"### {prefix}\n"
        f"**{info['bars']} compases** · **{info['bpm']} BPM** · **{info['key']}** · **{info['meter']}**  \n"
        f"Vocal: **{len(roll['tracks']['Vocal'])} notas** · Ins: **{len(roll['tracks']['Ins'])} notas** · "
        f"Acordes: **{len(roll['chords'])}** · Secciones: **{len(roll['sections'])}**"
    )


def load_score(upload_path, abc_text):
    try:
        text = Path(upload_path).read_text(encoding="utf-8") if upload_path else (abc_text or "")
        info = piano_roll_score.inspect_score(text)
        return (
            info["abc"],
            build_score_viewer(info["abc"]),
            build_piano_roll_editor(info["abc"], WORKSPACE, interactive=True),
            _summary(info),
        )
    except Exception as exc:
        raise gr.Error(f"No se pudo cargar el score: {exc}")


def apply_roll(payload_text):
    try:
        payload = json.loads(payload_text)
        result = piano_roll_score.build_score(payload)
        abc = result["abc"]
        return (
            abc,
            build_score_viewer(abc),
            build_piano_roll_editor(abc, WORKSPACE, interactive=True),
            _summary(result, "Cambios aplicados y ABC validado"),
        )
    except Exception as exc:
        raise gr.Error(f"El piano roll no pudo convertirse a ABC válido: {exc}")


with gr.Blocks(
    title="YuE2 Studio 4 — Piano Roll MVP",
    js=PIANOROLL_BRIDGE_JS,
    css=PIANOROLL_BRIDGE_CSS,
) as demo:
    gr.Markdown(
        "# YuE2 Studio 4 — Piano Roll MVP\n"
        "Prueba aislada del flujo **ABC ↔ eventos ↔ piano roll ↔ ABC validado**. "
        "No carga el modelo YuE2 ni consume VRAM."
    )

    with gr.Row():
        upload = gr.File(label="Abrir score.abc", type="filepath")
        load_btn = gr.Button("Cargar / actualizar", variant="primary")

    status = gr.Markdown("Carga un score real o usa el ejemplo incluido.")

    with gr.Tabs():
        with gr.Tab("🎹 Piano Roll"):
            piano = gr.HTML(build_piano_roll_editor(DEFAULT_ABC, WORKSPACE, interactive=True))
        with gr.Tab("🎼 Partitura"):
            notation = gr.HTML(build_score_viewer(DEFAULT_ABC))
        with gr.Tab("ABC"):
            abc = gr.Textbox(value=DEFAULT_ABC, label="ABC", lines=28, interactive=True)

    payload = gr.Textbox(
        value="",
        elem_id=f"{WORKSPACE}-payload",
        elem_classes=["yue2-pr-bridge-hidden"],
        label="bridge payload",
    )
    apply_bridge = gr.Button(
        "Aplicar bridge",
        elem_id=f"{WORKSPACE}-apply",
        elem_classes=["yue2-pr-bridge-hidden"],
    )

    load_btn.click(
        fn=load_score,
        inputs=[upload, abc],
        outputs=[abc, notation, piano, status],
        queue=False,
    )
    apply_bridge.click(
        fn=apply_roll,
        inputs=payload,
        outputs=[abc, notation, piano, status],
        queue=False,
    )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7861, show_error=True)
