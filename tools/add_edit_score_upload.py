#!/usr/bin/env python3
"""Añade carga directa de archivos ABC al Score Workspace de Edit Score.

Este parche temporal está pensado para ejecutarse después de
``tools/integrate_edit_workspace.py``. Usa anclas exactas, es idempotente y
compila ``app.py`` al terminar.
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

    if 'edit_upload = gr.File(' in text and 'def load_abc_file_for_edit(' in text:
        print("La carga directa de ABC ya está integrada; no se hicieron cambios.")
        return

    if 'EDIT_SCORE_WORKSPACE = "edit-score"' not in text:
        raise RuntimeError(
            "Primero ejecuta tools/integrate_edit_workspace.py; no se encontró el Score Workspace."
        )

    helper_anchor = '''\n\ndef regenerate_edited_score(\n    source_label, style, lyrics, abc_text,\n    cot, seed, bpm, key, meter\n):\n'''
    helper_code = '''\n\ndef load_abc_file_for_edit(upload_path):\n    """Carga y valida un ABC externo para usarlo como score editable."""\n    if not upload_path:\n        raise gr.Error("Selecciona o arrastra un archivo .abc/.txt.")\n\n    path = Path(upload_path)\n    try:\n        text = path.read_text(encoding="utf-8")\n        info = piano_roll_score.inspect_score(text)\n    except Exception as exc:\n        raise gr.Error(f"No se pudo cargar el ABC: {exc}")\n\n    abc = info["abc"]\n    bpm, key, meter = extract_abc_controls(abc)\n    return (\n        abc,\n        bpm,\n        key,\n        meter,\n        build_score_viewer(abc),\n        build_piano_roll_editor(\n            abc, EDIT_SCORE_WORKSPACE, interactive=True\n        ),\n        gr.update(value=None),\n        f"ABC externo cargado: `{path.name}`",\n    )\n'''
    text = replace_once(
        text,
        helper_anchor,
        helper_code + helper_anchor,
        "helper de carga ABC",
    )

    ui_old = '''        with gr.Row():\n            edit_source = gr.Dropdown(\n                choices=initial_choices,\n                value=initial_a,\n                label="Generación fuente",\n                scale=3,\n            )\n            edit_load = gr.Button("Cargar", scale=1)\n            edit_refresh = gr.Button("↻ Actualizar", scale=1)\n\n        edit_load_status = gr.Markdown("Selecciona una generación y pulsa Cargar.")\n'''
    ui_new = '''        with gr.Row():\n            edit_source = gr.Dropdown(\n                choices=initial_choices,\n                value=initial_a,\n                label="Generación fuente",\n                scale=3,\n            )\n            edit_load = gr.Button("Cargar biblioteca", scale=1)\n            edit_refresh = gr.Button("↻ Actualizar", scale=1)\n\n        with gr.Row():\n            edit_upload = gr.File(\n                label="Abrir score.abc",\n                type="filepath",\n                file_types=[".abc", ".txt"],\n                scale=3,\n            )\n            edit_upload_load = gr.Button("📄 Cargar ABC", scale=1)\n\n        edit_load_status = gr.Markdown(\n            "Carga una generación de la biblioteca o arrastra un archivo ABC."\n        )\n'''
    text = replace_once(text, ui_old, ui_new, "UI de carga directa")

    wiring_anchor = '''        edit_abc.change(\n            fn=build_edit_score_workspace,\n            inputs=edit_abc,\n            outputs=[edit_input_visual, edit_piano_roll],\n            queue=False,\n        )\n\n'''
    wiring_code = '''        edit_upload_load.click(\n            fn=load_abc_file_for_edit,\n            inputs=edit_upload,\n            outputs=[\n                edit_abc,\n                edit_bpm,\n                edit_key,\n                edit_meter,\n                edit_input_visual,\n                edit_piano_roll,\n                edit_source,\n                edit_load_status,\n            ],\n            queue=False,\n        )\n\n'''
    text = replace_once(
        text,
        wiring_anchor,
        wiring_anchor + wiring_code,
        "wiring de carga ABC",
    )

    required = [
        'edit_upload = gr.File(',
        'edit_upload_load = gr.Button("📄 Cargar ABC"',
        'def load_abc_file_for_edit(',
        'fn=load_abc_file_for_edit',
        'edit_load = gr.Button("Cargar biblioteca"',
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(f"La carga directa quedó incompleta: {missing}")

    APP.write_text(text, encoding="utf-8")
    py_compile.compile(str(APP), doraise=True)
    print("OK: carga directa de score.abc integrada en Edit Score y app.py compila correctamente.")


if __name__ == "__main__":
    main()
