#!/usr/bin/env python3
"""Integra el Score Workspace de Studio 4 en la pestaña Edit Score.

Este script es deliberadamente temporal y determinista: modifica app.py usando
anclas exactas de la rama studio-4-pianoroll, valida la sintaxis y se detiene si
la estructura esperada no coincide. No toca main ni la release v3.3.4.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: se esperaba 1 coincidencia y se encontraron {count}.")
    return text.replace(old, new, 1)


def main() -> None:
    text = APP.read_text(encoding="utf-8")

    if 'EDIT_SCORE_WORKSPACE = "edit-score"' in text:
        print("Edit Score Workspace ya está integrado; no se hicieron cambios.")
        return

    text = replace_once(
        text,
        "import gradio as gr\nimport torch\nfrom yue2 import YuE2Pipeline\n",
        "import gradio as gr\nimport torch\nfrom yue2 import YuE2Pipeline\n\n"
        "from tools import piano_roll_score\n"
        "from tools.piano_roll_ui import (\n"
        "    PIANOROLL_BRIDGE_CSS,\n"
        "    PIANOROLL_BRIDGE_JS,\n"
        "    build_piano_roll_editor,\n"
        ")\n",
        "imports del piano roll",
    )

    helper_anchor = """\n\n# ---------------------------------------------------------------------------\n# Output library\n# ---------------------------------------------------------------------------\n"""
    helper_code = r'''\n\n# ---------------------------------------------------------------------------\n# Studio 4 Score Workspace\n# ---------------------------------------------------------------------------\nEDIT_SCORE_WORKSPACE = "edit-score"\n\n\ndef build_edit_score_workspace(abc_text):\n    """Renderiza Partitura + Piano Roll para el mismo estado ABC."""\n    return (\n        build_score_viewer(abc_text),\n        build_piano_roll_editor(\n            abc_text,\n            EDIT_SCORE_WORKSPACE,\n            interactive=True,\n        ),\n    )\n\n\ndef apply_edit_piano_roll(payload_text):\n    """Reconstruye ABC desde el piano roll y refresca todo Edit Score."""\n    try:\n        payload = json.loads(payload_text or "")\n        result = piano_roll_score.build_score(payload)\n    except Exception as exc:\n        raise gr.Error(f"El Piano Roll no pudo convertirse a ABC válido: {exc}")\n\n    abc = result["abc"]\n    bpm, key, meter = extract_abc_controls(abc)\n    return (\n        abc,\n        build_score_viewer(abc),\n        build_piano_roll_editor(\n            abc,\n            EDIT_SCORE_WORKSPACE,\n            interactive=True,\n        ),\n        bpm,\n        key,\n        meter,\n        "✅ Piano Roll aplicado al ABC y validado.",\n    )\n'''
    text = replace_once(
        text,
        helper_anchor,
        helper_code + helper_anchor,
        "helpers del Score Workspace",
    )

    load_old = """        build_score_viewer(score),\n        f\"Cargado para edición: `{run_dir}`\",\n"""
    load_new = """        build_score_viewer(score),\n        build_piano_roll_editor(\n            score, EDIT_SCORE_WORKSPACE, interactive=True\n        ),\n        f\"Cargado para edición: `{run_dir}`\",\n"""
    text = replace_once(
        text,
        load_old,
        load_new,
        "salida de load_run_for_edit",
    )

    text = replace_once(
        text,
        "with gr.Blocks(title=APP_NAME, css=CSS) as demo:",
        "with gr.Blocks(\n"
        "    title=APP_NAME,\n"
        "    css=CSS + PIANOROLL_BRIDGE_CSS,\n"
        "    js=PIANOROLL_BRIDGE_JS,\n"
        ") as demo:",
        "bridge global de Gradio",
    )

    start_marker = """    # ------------------------------------------------------------------\n    # EDIT SCORE\n    # ------------------------------------------------------------------\n"""
    end_marker = """    # ------------------------------------------------------------------\n    # COMPARE\n    # ------------------------------------------------------------------\n"""
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise RuntimeError("No se encontró el bloque completo de Edit Score.")

    edit_block = r'''    # ------------------------------------------------------------------
    # EDIT SCORE
    # ------------------------------------------------------------------
    with gr.Tab("✏️ Edit Score"):
        with gr.Row():
            edit_source = gr.Dropdown(
                choices=initial_choices,
                value=initial_a,
                label="Generación fuente",
                scale=3,
            )
            edit_load = gr.Button("Cargar", scale=1)
            edit_refresh = gr.Button("↻ Actualizar", scale=1)

        edit_load_status = gr.Markdown("Selecciona una generación y pulsa Cargar.")

        with gr.Row():
            with gr.Column(scale=3):
                edit_style = gr.Textbox(
                    label="Style Prompt",
                    lines=3,
                )
                edit_lyrics = gr.Textbox(
                    label="Lyrics",
                    lines=14,
                )

            with gr.Column(scale=2):
                with gr.Row():
                    edit_bpm = gr.Slider(
                        40, 220, value=88, step=1, label="BPM"
                    )
                    edit_key = gr.Dropdown(
                        KEY_NAMES, value="C", label="Tonalidad"
                    )

                edit_meter = gr.Dropdown(
                    METERS, value="4/4", label="Compás"
                )

                edit_cot = gr.Radio(
                    choices=["full", "melody"],
                    value="full",
                    label="CoT",
                    info="Para edición con ABC, usa full o melody.",
                )

                with gr.Row():
                    edit_seed = gr.Number(
                        value=831001,
                        precision=0,
                        label="Seed",
                    )
                    edit_randomize = gr.Button("🎲 Nueva seed")

        gr.Markdown("### Score Workspace")
        with gr.Tabs():
            with gr.Tab("🎼 Partitura"):
                edit_input_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("🎹 Piano Roll"):
                edit_piano_roll = gr.HTML(
                    value=build_piano_roll_editor(
                        "", EDIT_SCORE_WORKSPACE, interactive=True
                    )
                )
            with gr.Tab("ABC"):
                edit_abc = gr.Textbox(
                    label="ABC editable",
                    lines=24,
                    interactive=True,
                )

        edit_workspace_status = gr.Markdown(
            "Partitura, Piano Roll y ABC representan el mismo score. "
            "En Piano Roll pulsa **Aplicar al ABC** para consolidar los cambios."
        )

        # Bridge oculto del iframe del piano roll hacia el callback Python.
        edit_roll_payload = gr.Textbox(
            value="",
            elem_id=f"{EDIT_SCORE_WORKSPACE}-payload",
            elem_classes=["yue2-pr-bridge-hidden"],
            label="bridge payload",
        )
        edit_roll_apply = gr.Button(
            "Aplicar bridge",
            elem_id=f"{EDIT_SCORE_WORKSPACE}-apply",
            elem_classes=["yue2-pr-bridge-hidden"],
        )

        edit_generate = gr.Button(
            "🎛️ REGENERATE FROM SCORE",
            variant="primary",
        )

        with gr.Row():
            edit_audio = gr.Audio(
                label="Versión editada",
                type="filepath",
            )
            edit_status = gr.Markdown("Listo.")

        with gr.Row():
            edit_output_folder = gr.Textbox(
                label="Carpeta de salida",
                interactive=False,
            )
            edit_open_folder = gr.Button("📂 Abrir carpeta de salida")

        with gr.Tabs():
            with gr.Tab("🎼 Partitura resultante"):
                edit_result_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("ABC resultante"):
                edit_result_abc = gr.Textbox(
                    label="ABC resultante",
                    lines=24,
                    interactive=True,
                )

        with gr.Accordion("Detalles", open=False):
            edit_result_json = gr.JSON(label="result.json")

        edit_load.click(
            fn=load_run_for_edit,
            inputs=edit_source,
            outputs=[
                edit_style,
                edit_lyrics,
                edit_abc,
                edit_cot,
                edit_seed,
                edit_bpm,
                edit_key,
                edit_meter,
                edit_input_visual,
                edit_piano_roll,
                edit_load_status,
            ],
        )

        edit_abc.change(
            fn=build_edit_score_workspace,
            inputs=edit_abc,
            outputs=[edit_input_visual, edit_piano_roll],
            queue=False,
        )

        edit_roll_apply.click(
            fn=apply_edit_piano_roll,
            inputs=edit_roll_payload,
            outputs=[
                edit_abc,
                edit_input_visual,
                edit_piano_roll,
                edit_bpm,
                edit_key,
                edit_meter,
                edit_workspace_status,
            ],
            queue=False,
        )

        edit_randomize.click(
            fn=random_seed,
            outputs=edit_seed,
        )

        edit_generate.click(
            fn=regenerate_edited_score,
            inputs=[
                edit_source,
                edit_style,
                edit_lyrics,
                edit_abc,
                edit_cot,
                edit_seed,
                edit_bpm,
                edit_key,
                edit_meter,
            ],
            outputs=[
                edit_audio,
                edit_result_visual,
                edit_result_abc,
                edit_status,
                edit_output_folder,
                edit_result_json,
            ],
        )

        edit_open_folder.click(
            fn=open_folder,
            inputs=edit_output_folder,
            outputs=edit_status,
        )

        # The Transcribe tab is defined before Edit Score, so this cross-tab
        # wiring is attached here, once all Edit Score components exist.
        transcribe_send_edit.click(
            fn=send_transcription_to_edit,
            inputs=[transcribe_abc, transcribe_mode],
            outputs=[
                edit_abc,
                edit_cot,
                edit_bpm,
                edit_key,
                edit_meter,
                edit_source,
                edit_load_status,
            ],
            queue=False,
        )

'''
    text = text[:start] + edit_block + text[end:]

    required = [
        'EDIT_SCORE_WORKSPACE = "edit-score"',
        'with gr.Tab("🎹 Piano Roll")',
        'fn=apply_edit_piano_roll',
        'js=PIANOROLL_BRIDGE_JS',
        'css=CSS + PIANOROLL_BRIDGE_CSS',
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(f"La integración quedó incompleta: {missing}")

    APP.write_text(text, encoding="utf-8")
    py_compile.compile(str(APP), doraise=True)
    print("OK: Score Workspace integrado en Edit Score y app.py compila correctamente.")


if __name__ == "__main__":
    main()
