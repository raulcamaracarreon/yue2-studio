import base64
import hashlib
import json
import os
import random
import sys
import tempfile
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import gradio as gr
import torch
from yue2 import YuE2Pipeline


APP_DIR = Path(__file__).resolve().parent


def _load_env_file(path):
    """Load a small KEY=VALUE .env file without adding a dependency."""
    path = Path(path)
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = os.path.expandvars(os.path.expanduser(value))


def _env_path(name, default):
    return Path(os.path.expandvars(os.environ.get(name, str(default)))).expanduser()


_load_env_file(APP_DIR / ".env")

APP_VERSION = "3.3.4"
APP_NAME = f"YuE2 Studio v{APP_VERSION}"
MODEL_ID = os.environ.get("YUE2_MODEL_ID", "m-a-p/YuE2-3B")
VAE_ID = os.environ.get("YUE2_VAE_ID", "m-a-p/YuE2-Vae")
MEMORY_BUDGET_GIB = float(os.environ.get("YUE2_MEMORY_BUDGET_GIB", "12"))
SERVER_NAME = os.environ.get("YUE2_SERVER_NAME", "127.0.0.1")
SERVER_PORT = int(os.environ.get("YUE2_SERVER_PORT", "7860"))

def _default_base_dir():
    # Portable public default. Override with YUE2_STUDIO_HOME in .env or the environment.
    return Path.home() / "YuE2-Studio"


def _default_abc_tools():
    # Public repo layout first; official YuE skill layout second.
    candidates = [
        APP_DIR / "tools" / "abc_tools.py",
        APP_DIR / "skills" / "yue2-music" / "scripts" / "abc_tools.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


BASE_DIR = _env_path("YUE2_STUDIO_HOME", _default_base_dir())
OUTPUTS_ROOT = _env_path("YUE2_OUTPUTS_ROOT", BASE_DIR / "outputs")
STUDIO_ROOT = OUTPUTS_ROOT / "studio"
TRANSCRIPTIONS_ROOT = OUTPUTS_ROOT / "transcriptions"
COVERS_ROOT = OUTPUTS_ROOT / "covers"
AGENT_EDITS_ROOT = OUTPUTS_ROOT / "agent_edits"

SHEETSAGE_DIR = _env_path("SHEETSAGE2_DIR", BASE_DIR / "models" / "SheetSage2")
SHEETSAGE_PYTHON = _env_path("SHEETSAGE2_PYTHON", Path.home() / "venvs" / "sheetsage2" / "bin" / "python")
# Keep the renderer/transcriber work directory on the Linux filesystem.
# SheetSage2 performs chmod/atomic writes that can fail on WSL-mounted NTFS.
SHEETSAGE_WORK_ROOT = _env_path("SHEETSAGE2_WORK_ROOT", Path.home() / ".cache" / "yue2-studio" / "sheetsage2")
HF_MODULES_CACHE = _env_path("HF_MODULES_CACHE", Path.home() / ".cache" / "huggingface" / "modules")
ABC_TOOLS = _env_path("YUE2_ABC_TOOLS", _default_abc_tools())
AGENT_DEFAULT_ENDPOINT = os.environ.get("YUE2_AGENT_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
AGENT_DEFAULT_MODEL = os.environ.get("YUE2_AGENT_MODEL", "")

STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
TRANSCRIPTIONS_ROOT.mkdir(parents=True, exist_ok=True)
COVERS_ROOT.mkdir(parents=True, exist_ok=True)
AGENT_EDITS_ROOT.mkdir(parents=True, exist_ok=True)
SHEETSAGE_WORK_ROOT.mkdir(parents=True, exist_ok=True)
HF_MODULES_CACHE.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("HF_HOME", str(BASE_DIR / ".cache" / "huggingface"))
os.environ.setdefault(
    "HUGGINGFACE_HUB_CACHE",
    str(BASE_DIR / ".cache" / "huggingface" / "hub"),
)
os.environ.setdefault("TORCH_HOME", str(BASE_DIR / ".cache" / "torch"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

PIPE = None
PIPE_LOCK = threading.Lock()

# Approximate semantic rate used only by Strict duration mode; Automatic remains the default.
TOKENS_PER_SECOND = 25.0

# Exact key set accepted by YuE2's official native-ABC validator
# (7 flats through 7 sharps). No silent enharmonic aliases are used.
KEY_NAMES = [
    # Major
    "Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C",
    "G", "D", "A", "E", "B", "F#", "C#",
    # Minor
    "Abm", "Ebm", "Bbm", "Fm", "Cm", "Gm", "Dm", "Am",
    "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m",
]

KEY_LABELS = {
    "Cb": "C-flat major",
    "Gb": "G-flat major",
    "Db": "D-flat major",
    "Ab": "A-flat major",
    "Eb": "E-flat major",
    "Bb": "B-flat major",
    "F": "F major",
    "C": "C major",
    "G": "G major",
    "D": "D major",
    "A": "A major",
    "E": "E major",
    "B": "B major",
    "F#": "F-sharp major",
    "C#": "C-sharp major",
    "Abm": "A-flat minor",
    "Ebm": "E-flat minor",
    "Bbm": "B-flat minor",
    "Fm": "F minor",
    "Cm": "C minor",
    "Gm": "G minor",
    "Dm": "D minor",
    "Am": "A minor",
    "Em": "E minor",
    "Bm": "B minor",
    "F#m": "F-sharp minor",
    "C#m": "C-sharp minor",
    "G#m": "G-sharp minor",
    "D#m": "D-sharp minor",
    "A#m": "A-sharp minor",
}


METERS = ["4/4", "3/4", "2/4", "6/8", "9/8", "12/8", "5/4", "7/8"]


# ---------------------------------------------------------------------------
# YuE2
# ---------------------------------------------------------------------------

def get_pipe():
    global PIPE
    if PIPE is None:
        PIPE = YuE2Pipeline.from_pretrained(
            MODEL_ID,
            vae=VAE_ID,
            device="cuda",
            memory_budget_gib=MEMORY_BUDGET_GIB,
            progress=True,
        )
    return PIPE


def unload_model():
    global PIPE
    with PIPE_LOCK:
        if PIPE is not None:
            PIPE.close()
            PIPE = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    free_gb = (
        torch.cuda.mem_get_info()[0] / 1024**3
        if torch.cuda.is_available()
        else 0
    )
    return f"Modelo liberado. VRAM libre: **{free_gb:.2f} GB**."


def gpu_status():
    if not torch.cuda.is_available():
        return "CUDA no disponible."
    free, total = torch.cuda.mem_get_info()
    return (
        f"**GPU:** {torch.cuda.get_device_name(0)}  \n"
        f"**VRAM libre:** {free / 1024**3:.2f} GB / "
        f"{total / 1024**3:.2f} GB"
    )


def random_seed():
    return random.randint(0, 2**31 - 1)


# ---------------------------------------------------------------------------
# Musical controls / ABC
# ---------------------------------------------------------------------------

def augment_style(style, bpm, key, meter):
    style = (style or "").strip().rstrip(" ,")
    controls = [
        f"{int(bpm)} BPM",
        f"key of {KEY_LABELS[key]}",
        f"{meter} meter",
    ]
    return f"{style}, " + ", ".join(controls) if style else ", ".join(controls)


def strip_ui_controls(style):
    """Remove the UI-added BPM/key/meter suffix when reloading a run."""
    style = (style or "").strip()
    pattern = (
        r",\s*\d+\s*BPM\s*,\s*key of [^,]+,\s*"
        r"\d+\s*/\s*\d+\s*meter"
        r"(?:\s*,\s*target duration approximately \d+ seconds)?\s*$"
    )
    return re.sub(pattern, "", style, flags=re.IGNORECASE).strip()


def _replace_or_insert_header(abc_text, field, value):
    replacement = f"{field}:{value}"
    pattern = rf"(?m)^{re.escape(field)}\s*:.*$"

    if re.search(pattern, abc_text):
        return re.sub(pattern, replacement, abc_text, count=1)

    lines = abc_text.splitlines()

    if field in {"M", "Q"}:
        for i, line in enumerate(lines):
            if re.match(r"^\s*K\s*:", line):
                lines.insert(i, replacement)
                return "\n".join(lines)

    if field == "K":
        header_fields = ("X:", "T:", "C:", "M:", "L:", "Q:", "P:", "K:")
        insert_at = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(header_fields):
                insert_at = i + 1
            elif line.strip() and insert_at:
                break
        lines.insert(insert_at, replacement)
        return "\n".join(lines)

    lines.insert(0, replacement)
    return "\n".join(lines)


def apply_abc_controls(abc_text, bpm, key, meter):
    """Apply safe musical controls to an existing YuE2 ABC score.

    Tempo can be changed directly. Tonality is enforced by real transposition
    of notes + chord symbols when source and target use the same major/minor
    mode. Meter is never rewritten as a header-only operation because doing
    so would invalidate measure durations.
    """
    if not (abc_text or "").strip():
        return None
    prepared, _ = prepare_abc_for_target(
        abc_text, bpm, key, meter, origin="existing ABC"
    )
    return prepared


def extract_abc_controls(abc_text):
    """Best-effort recovery of BPM, key and meter from a YuE2 ABC score."""
    bpm = 88
    key = "C"
    meter = "4/4"

    if not abc_text:
        return bpm, key, meter

    m = re.search(r"(?m)^M\s*:\s*([^\s]+)", abc_text)
    if m and m.group(1) in METERS:
        meter = m.group(1)

    q = re.search(r"(?m)^Q\s*:[^\n=]*=\s*(\d+)", abc_text)
    if q:
        bpm = max(40, min(220, int(q.group(1))))

    k = re.search(r"(?m)^K\s*:\s*([^\s]+)", abc_text)
    supported_keys = set(KEY_NAMES) | set(globals().get("KEY_SIG_COUNTS", {}))
    if k and k.group(1) in supported_keys:
        key = k.group(1)

    return bpm, key, meter


def duration_preview(seconds):
    tokens = max(1, int(round(float(seconds) * TOKENS_PER_SECOND)))
    minutes = int(seconds) // 60
    sec = int(seconds) % 60
    return (
        f"Objetivo estricto: **{minutes}:{sec:02d}** · "
        f"Límite semántico: **{tokens} tokens** "
        f"({TOKENS_PER_SECOND:.0f} tok/s)"
    )


def toggle_duration_controls(mode):
    strict = mode == "Estricto"
    return gr.update(visible=strict), gr.update(visible=strict)


def build_score_viewer(abc_text):
    if not (abc_text or "").strip():
        return """
        <div style="padding:24px;text-align:center;opacity:.7">
            La partitura aparecerá aquí cuando haya un score ABC.
        </div>
        """

    abc_json = json.dumps(abc_text)
    inner = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/abcjs@6.5.2/dist/abcjs-basic-min.js"></script>
<style>
body {{
  margin:0; padding:14px; background:#fff; color:#111;
  font-family:Arial,sans-serif;
}}
#paper {{ width:100%; overflow-x:auto; }}
#err {{ color:#a00; white-space:pre-wrap; font-family:monospace; }}
</style>
</head>
<body>
<div id="paper"></div>
<div id="err"></div>
<script>
const abc = {abc_json};
try {{
  if (!window.ABCJS) {{
    throw new Error("No se pudo cargar abcjs desde jsDelivr.");
  }}
  ABCJS.renderAbc("paper", abc, {{
    responsive: "resize",
    add_classes: true,
    staffwidth: 1050
  }});
}} catch (e) {{
  document.getElementById("err").textContent =
    "No se pudo renderizar la partitura:\\n" + e;
}}
</script>
</body>
</html>"""
    encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    return (
        '<iframe '
        f'src="data:text/html;base64,{encoded}" '
        'style="width:100%;height:720px;border:1px solid #d0d0d0;'
        'border-radius:8px;background:#fff;" loading="lazy"></iframe>'
    )


# ---------------------------------------------------------------------------
# Output library
# ---------------------------------------------------------------------------

def safe_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan_runs():
    """Find every YuE2 run under the configured outputs root that has result.json."""
    rows = []
    for result_path in OUTPUTS_ROOT.rglob("result.json"):
        run_dir = result_path.parent

        # La biblioteca principal contiene generaciones YuE2. SheetSage2 también
        # crea result.json, pero sus transcripciones se gestionan en Transcribe.
        if not (run_dir / "request.json").exists() or not (run_dir / "audio.flac").exists():
            continue

        result = safe_json(result_path)
        request = safe_json(run_dir / "request.json")

        try:
            rel = str(run_dir.relative_to(OUTPUTS_ROOT))
        except ValueError:
            rel = str(run_dir)

        audio_seconds = result.get("audio_seconds")
        if isinstance(audio_seconds, (int, float)):
            duration = f"{audio_seconds:.1f}"
        else:
            duration = ""

        seed = request.get("seed", "")
        cot = request.get("cot", "")
        style = strip_ui_controls(request.get("style", ""))
        if len(style) > 80:
            style = style[:77] + "..."

        modified = datetime.fromtimestamp(
            result_path.stat().st_mtime
        ).strftime("%Y-%m-%d %H:%M")

        rows.append(
            {
                "label": rel,
                "path": run_dir,
                "modified": modified,
                "duration": duration,
                "cot": cot,
                "seed": seed,
                "style": style,
                "mtime": result_path.stat().st_mtime,
            }
        )

    rows.sort(key=lambda x: x["mtime"], reverse=True)
    return rows


def library_choices():
    return [row["label"] for row in scan_runs()]


def library_table_data():
    return [
        [
            row["label"],
            row["modified"],
            row["duration"],
            row["cot"],
            row["seed"],
            row["style"],
        ]
        for row in scan_runs()
    ]


def resolve_run(label):
    if not label:
        raise gr.Error("Selecciona una generación.")
    path = (OUTPUTS_ROOT / label).resolve()
    root = OUTPUTS_ROOT.resolve()
    if root != path and root not in path.parents:
        raise gr.Error("Ruta de generación no válida.")
    if not path.exists():
        raise gr.Error(f"No existe: {path}")
    return path


def run_metadata_markdown(run_dir):
    result = safe_json(run_dir / "result.json")
    request = safe_json(run_dir / "request.json")
    duration = result.get("audio_seconds", 0)
    timing = result.get("timing", {})
    e2e = timing.get("e2e_seconds", "")
    truncated = result.get("truncated", {})

    lines = [
        f"**Carpeta:** `{run_dir}`",
        f"**Duración:** {duration:.1f} s" if isinstance(duration, (int, float)) else "**Duración:** —",
        f"**CoT:** `{request.get('cot', '—')}`",
        f"**Seed:** `{request.get('seed', '—')}`",
        f"**Truncamiento:** `{truncated}`",
    ]
    if isinstance(e2e, (int, float)):
        lines.append(f"**Tiempo YuE2:** {e2e:.1f} s")
    style = request.get("style", "")
    if style:
        lines.append(f"**Style:** {style}")
    return "  \n".join(lines)


def refresh_library():
    choices = library_choices()
    first = choices[0] if choices else None
    return (
        library_table_data(),
        gr.update(choices=choices, value=first),
        gr.update(choices=choices, value=first),
        gr.update(choices=choices, value=first),
        gr.update(choices=choices, value=choices[1] if len(choices) > 1 else first),
    )


def load_library_run(label):
    run_dir = resolve_run(label)
    audio = run_dir / "audio.flac"
    score = (
        (run_dir / "score.abc").read_text(encoding="utf-8")
        if (run_dir / "score.abc").exists()
        else ""
    )
    return (
        str(audio) if audio.exists() else None,
        build_score_viewer(score),
        score,
        run_metadata_markdown(run_dir),
        str(run_dir),
    )


# ---------------------------------------------------------------------------
# Desktop file-manager integration
# ---------------------------------------------------------------------------

def _open_path_native(path):
    path = Path(path).resolve()

    # WSL -> Windows Explorer.
    if shutil.which("wslpath") and shutil.which("explorer.exe"):
        win_path = subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
        subprocess.Popen(["explorer.exe", win_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return win_path

    # Native Windows.
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return str(path)

    # macOS / Linux desktop.
    if sys.platform == "darwin" and shutil.which("open"):
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(path)
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(path)

    raise RuntimeError("No supported desktop file opener was found.")


def open_folder(path_text):
    path_text = (path_text or "").strip()
    path = Path(path_text) if path_text else OUTPUTS_ROOT
    if not path.exists() or not path.is_dir():
        raise gr.Error(f"La carpeta no existe: {path}")
    try:
        opened = _open_path_native(path)
        return f"Carpeta abierta: `{opened}`"
    except Exception as exc:
        raise gr.Error(f"No pude abrir la carpeta: {exc}")


def open_selected_library_folder(label):
    return open_folder(str(resolve_run(label)))


def open_outputs_root():
    return open_folder(str(OUTPUTS_ROOT))


def open_file(path_text):
    path_text = (path_text or "").strip()
    if not path_text:
        raise gr.Error("Todavía no hay un archivo para abrir.")
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        raise gr.Error(f"El archivo no existe: {path}")
    try:
        opened = _open_path_native(path)
        return f"Archivo abierto: `{opened}`"
    except Exception as exc:
        raise gr.Error(f"No pude abrir el archivo: {exc}")


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def generate_song(
    style, lyrics, bpm, key, meter, duration_mode,
    target_duration, cot, seed, abc_text
):
    if not (style or "").strip():
        raise gr.Error("Escribe un Style Prompt.")
    if not (lyrics or "").strip():
        raise gr.Error("Escribe la letra.")

    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise gr.Error("La seed debe ser un número entero.")

    if not 0 <= seed < 2**63:
        raise gr.Error("La seed debe estar entre 0 y 2^63 - 1.")

    if key not in KEY_NAMES:
        raise gr.Error(
            f"Tonalidad no soportada por YuE2: {key}. "
            "Elige una tonalidad de la lista."
        )

    target_duration = int(target_duration)
    strict_duration = duration_mode == "Estricto"

    final_style = augment_style(style, bpm, key, meter)

    semantic_sampling = None
    token_limit = None
    if strict_duration:
        token_limit = max(
            200,
            int(round(target_duration * TOKENS_PER_SECOND)),
        )
        semantic_sampling = {"max_tokens": token_limit}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"studio-{stamp}-{seed}"
    output_dir = STUDIO_ROOT / run_id
    started = time.perf_counter()

    supplied_abc = None
    original_planner_abc = ""
    control_meta = {
        "requested_bpm": int(bpm),
        "requested_key": key,
        "requested_meter": meter,
        "cot": cot,
        "seed": seed,
        "key_enforcement": "unavailable_with_cot_off" if cot == "off" else "pending",
    }

    with PIPE_LOCK:
        pipe = get_pipe()
        free_before = torch.cuda.mem_get_info()[0] / 1024**3

        if cot != "off":
            try:
                if (abc_text or "").strip():
                    supplied_abc, abc_meta = prepare_abc_for_target(
                        abc_text,
                        bpm,
                        key,
                        meter,
                        origin="user ABC",
                    )
                    control_meta.update(abc_meta)
                    control_meta["score_origin"] = "user_abc"
                else:
                    # Plan first so the GUI can verify/correct the symbolic score
                    # before expensive semantic/NAR/VAE generation.
                    plan = pipe.plan(
                        style=final_style,
                        lyrics=lyrics,
                        cot=cot,
                        seed=seed,
                        id=run_id,
                    )
                    original_planner_abc = plan.abc or ""
                    if not original_planner_abc.strip():
                        raise ValueError("YuE2 no devolvió un score ABC verificable.")

                    supplied_abc, abc_meta = prepare_abc_for_target(
                        original_planner_abc,
                        bpm,
                        key,
                        meter,
                        origin="YuE2 planner",
                    )
                    control_meta.update(abc_meta)
                    control_meta["score_origin"] = "generated_plan"
                    control_meta["planner_timing"] = plan.timing
                    control_meta["planner_truncated"] = bool(plan.truncated)
            except ValueError as exc:
                raise gr.Error(str(exc))

        song = pipe(
            style=final_style,
            lyrics=lyrics,
            cot=cot,
            seed=seed,
            abc=supplied_abc,
            id=run_id,
            semantic_sampling=semantic_sampling,
        )

        result = song.save_artifacts(output_dir)
        free_after = torch.cuda.mem_get_info()[0] / 1024**3

    # Preserve provenance of automatic score correction.
    if original_planner_abc.strip():
        (output_dir / "planner_score_original.abc").write_text(
            original_planner_abc.rstrip() + "\n",
            encoding="utf-8",
        )
    (output_dir / "studio_controls.json").write_text(
        json.dumps(control_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["_studio_controls"] = control_meta

    elapsed = time.perf_counter() - started
    audio_seconds = float(result.get("audio_seconds", 0))
    ratio = elapsed / audio_seconds if audio_seconds > 0 else 0
    trunc = result.get("truncated", {})
    score = song.abc or ""

    if strict_duration:
        delta = audio_seconds - target_duration
        delta_pct = delta / target_duration * 100 if target_duration else 0
        duration_lines = (
            f"**Objetivo:** {target_duration:.0f} s  \n"
            f"**Obtenido:** {audio_seconds:.1f} s  \n"
            f"**Diferencia:** {delta:+.1f} s ({delta_pct:+.1f}%)  \n"
            f"**Duración:** Estricto · {token_limit} tokens  \n"
        )
    else:
        duration_lines = (
            f"**Duración final:** {audio_seconds:.1f} s  \n"
            f"**Duración:** Automática · cierre natural de YuE2  \n"
        )

    trunc_note = ""
    if strict_duration and trunc.get("semantic"):
        trunc_note = (
            "\n\n> **Nota:** la canción alcanzó el límite estricto; "
            "puede haber quedado incompleta."
        )

    if cot == "off":
        control_lines = (
            f"**BPM / tonalidad / compás solicitados:** "
            f"`{int(bpm)} / {key} / {meter}`  \n"
            "> **CoT off:** YuE2 no produce ABC; tonalidad y compás son "
            "instrucciones de prompt y no pueden verificarse simbólicamente.  \n"
        )
    else:
        shift = int(control_meta.get("key_transposition_semitones", 0))
        source_key = control_meta.get("source_key", key)
        if shift:
            key_detail = (
                f"`{source_key}` → `{key}` "
                f"({shift:+d} semitonos, notas y acordes transpuestos)"
            )
        else:
            key_detail = f"`{key}` (ya coincidía)"
        control_lines = (
            f"**BPM final:** `{int(bpm)}`  \n"
            f"**Tonalidad final:** {key_detail}  \n"
            f"**Compás verificado:** `{meter}`  \n"
        )

    status = (
        "### Generación terminada\n"
        f"{duration_lines}"
        f"**Tiempo de cómputo:** {elapsed:.1f} s  \n"
        f"**Relación cómputo/audio:** {ratio:.2f}×  \n"
        f"**Seed:** `{seed}`  \n"
        f"**CoT:** `{cot}`  \n"
        f"{control_lines}"
        f"**Truncamiento:** `{trunc}`  \n"
        f"**VRAM libre antes/después:** "
        f"{free_before:.2f} / {free_after:.2f} GB  \n"
        f"**Salida:** `{output_dir}`"
        f"{trunc_note}"
    )

    return (
        str(output_dir / "audio.flac"),
        build_score_viewer(score),
        score,
        status,
        str(output_dir),
        result,
    )


# ---------------------------------------------------------------------------
# TRANSCRIBE / SheetSage2
# ---------------------------------------------------------------------------

def _sheetsage_env():
    env = os.environ.copy()
    env["HF_HOME"] = str(BASE_DIR / ".cache" / "huggingface")
    env["HUGGINGFACE_HUB_CACHE"] = str(BASE_DIR / ".cache" / "huggingface" / "hub")
    env["HF_MODULES_CACHE"] = str(HF_MODULES_CACHE)
    env["TORCH_HOME"] = str(BASE_DIR / ".cache" / "torch")
    env.pop("TRANSFORMERS_CACHE", None)
    return env


def _run_external(command, cwd=None):
    proc = subprocess.run(
        [str(x) for x in command],
        cwd=str(cwd) if cwd else None,
        env=_sheetsage_env(),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-35:])
        raise gr.Error(
            "El proceso externo terminó con error.\n\n"
            f"Comando: {' '.join(map(str, command))}\n\n{tail}"
        )
    return proc.stdout or "", proc.stderr or ""


def _copy_tree_without_metadata(source, destination):
    """Copy files without metadata operations that can fail on mounted filesystems."""
    source = Path(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = destination / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)


def _read_text_if_exists(path, limit=1400):
    path = Path(path)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > limit:
            return text[:limit] + "…"
        return text
    except Exception:
        return ""


def _safe_job_stem(path):
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(path).stem).strip("-_.")
    return stem[:48] or "audio"


def _transcription_summary(output_dir, mode, elapsed):
    output_dir = Path(output_dir)
    key_text = _read_text_if_exists(output_dir / "key.lab", 300)
    structures = _read_text_if_exists(output_dir / "song_structures.txt", 600)
    if not structures:
        structures = _read_text_if_exists(output_dir / "structure.lab", 600)
    chords = _read_text_if_exists(output_dir / "song_chords.txt", 600)

    midi_files = sorted(p.name for p in output_dir.glob("*.mid"))
    rendered = output_dir / "rendered"
    pdf_ok = (rendered / "score.pdf").exists()
    piano_ok = (rendered / "piano_mix.wav").exists()

    lines = [
        "### Transcripción terminada",
        f"**Modo:** {mode}  ",
        f"**Tiempo total:** {elapsed:.1f} s  ",
        f"**Salida:** `{output_dir}`  ",
        f"**PDF:** {'Sí' if pdf_ok else 'No'} · "
        f"**Piano preview:** {'Sí' if piano_ok else 'No'}  ",
        f"**MIDI:** {', '.join(midi_files) if midi_files else '—'}",
    ]
    if key_text:
        lines.append(f"\n**Tonalidad detectada**\n```text\n{key_text}\n```")
    if structures:
        lines.append(f"\n**Estructura**\n```text\n{structures}\n```")
    if chords:
        lines.append(f"\n**Acordes**\n```text\n{chords}\n```")
    return "\n".join(lines)


def transcribe_audio(audio_path, mode, render_outputs):
    if not audio_path:
        raise gr.Error("Carga primero un archivo de audio.")

    if not SHEETSAGE_PYTHON.exists():
        raise gr.Error(
            f"No encuentro el entorno SheetSage2 en {SHEETSAGE_PYTHON}"
        )
    if not (SHEETSAGE_DIR / "infer.py").exists():
        raise gr.Error(
            f"No encuentro SheetSage2 en {SHEETSAGE_DIR}"
        )

    source_audio = Path(audio_path)
    if not source_audio.exists():
        raise gr.Error(f"No encuentro el audio: {source_audio}")

    # YuE2 y SheetSage2 comparten la misma GPU. Liberamos YuE2 antes
    # de lanzar la transcripción para evitar competir por VRAM.
    unload_model()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    job_id = f"transcribe-{stamp}-{_safe_job_stem(source_audio)}"
    work_dir = SHEETSAGE_WORK_ROOT / job_id
    output_dir = TRANSCRIPTIONS_ROOT / job_id

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    infer_cmd = [
        SHEETSAGE_PYTHON,
        SHEETSAGE_DIR / "infer.py",
        source_audio,
        "--output",
        work_dir,
    ]
    if mode.startswith("Solo melodía"):
        infer_cmd.append("--melody-only")

    try:
        infer_stdout, infer_stderr = _run_external(
            infer_cmd,
            cwd=SHEETSAGE_DIR,
        )

        render_stdout = ""
        render_stderr = ""
        if render_outputs:
            rendered_dir = work_dir / "rendered"
            render_cmd = [
                SHEETSAGE_PYTHON,
                SHEETSAGE_DIR / "render.py",
                "--input",
                work_dir,
                "--output",
                rendered_dir,
                "--audio",
                "--score",
                "pdf,svg,png",
            ]
            render_stdout, render_stderr = _run_external(
                render_cmd,
                cwd=SHEETSAGE_DIR,
            )

        # SheetSage2 uses atomic chmod; keep temporary work on a native Linux filesystem.
        # Cuando termina, copiamos los artefactos a F: sin metadatos Unix.
        _copy_tree_without_metadata(work_dir, output_dir)

    except Exception:
        # Conservamos el directorio Linux fallido para diagnóstico.
        raise

    elapsed = time.perf_counter() - started

    score_path = output_dir / "score.abc"
    score = _read_text_if_exists(score_path, limit=1_000_000)

    rendered_dir = output_dir / "rendered"
    piano_path = rendered_dir / "piano_mix.wav"
    pdf_path = rendered_dir / "score.pdf"
    svg_paths = sorted(rendered_dir.glob("score_*.svg"))
    png_paths = sorted(rendered_dir.glob("score_*.png"))
    midi_paths = sorted(output_dir.glob("*.mid"))

    result = safe_json(output_dir / "result.json")
    result["_studio"] = {
        "job_id": job_id,
        "mode": mode,
        "elapsed_seconds": elapsed,
        "source_audio": str(source_audio),
        "output_dir": str(output_dir),
        "rendered": bool(render_outputs),
        "infer_stdout_tail": "\n".join(infer_stdout.splitlines()[-12:]),
        "infer_stderr_tail": "\n".join(infer_stderr.splitlines()[-12:]),
    }

    status = _transcription_summary(output_dir, mode, elapsed)

    # Ya está persistido en F:, así que el working dir temporal se puede limpiar.
    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass

    return (
        build_score_viewer(score),
        score,
        str(piano_path) if piano_path.exists() else None,
        str(pdf_path) if pdf_path.exists() else None,
        [str(p) for p in svg_paths],
        [str(p) for p in png_paths],
        [str(p) for p in midi_paths],
        status,
        str(output_dir),
        result,
    )


def send_transcription_to_create(abc_text, mode):
    if not (abc_text or "").strip():
        raise gr.Error("No hay ABC para enviar.")
    bpm, key, meter = extract_abc_controls(abc_text)
    cot = "melody" if str(mode).startswith("Solo melodía") else "full"
    return abc_text, cot, bpm, key, meter


def send_transcription_to_edit(abc_text, mode):
    if not (abc_text or "").strip():
        raise gr.Error("No hay ABC para enviar.")
    bpm, key, meter = extract_abc_controls(abc_text)
    cot = "melody" if str(mode).startswith("Solo melodía") else "full"
    return (
        abc_text,
        cot,
        bpm,
        key,
        meter,
        gr.update(value=None),
        "ABC de SheetSage2 cargado. Escribe Style y Lyrics antes de regenerar.",
    )


# ---------------------------------------------------------------------------
# COVER / audio -> SheetSage2 -> ABC -> YuE2
# ---------------------------------------------------------------------------

def _cover_cot(preservation):
    return "melody" if str(preservation).startswith("Melodías") else "full"


def _cover_mode_label(cot):
    if cot == "melody":
        return "Melodías vocal + instrumental (melody)"
    return "Score completo (full)"


def _validate_cover_inputs(style, lyrics, seed):
    if not (style or "").strip():
        raise gr.Error("Escribe el Style Prompt del cover.")
    if not (lyrics or "").strip():
        raise gr.Error("Escribe la letra que cantará el cover.")
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise gr.Error("La seed debe ser un número entero.")
    if not 0 <= seed < 2**63:
        raise gr.Error("La seed debe estar entre 0 y 2^63 - 1.")
    return seed


def _transcribe_cover_source(audio_path, preservation, render_outputs=True):
    if not audio_path:
        raise gr.Error("Carga primero el audio original.")
    if not SHEETSAGE_PYTHON.exists():
        raise gr.Error(f"No encuentro el entorno SheetSage2 en {SHEETSAGE_PYTHON}")
    if not (SHEETSAGE_DIR / "infer.py").exists():
        raise gr.Error(f"No encuentro SheetSage2 en {SHEETSAGE_DIR}")

    source_audio = Path(audio_path)
    if not source_audio.exists():
        raise gr.Error(f"No encuentro el audio: {source_audio}")

    # SheetSage2 and YuE2 are used sequentially so they can share a single GPU.
    unload_model()

    cot = _cover_cot(preservation)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    job_id = f"cover-source-{stamp}-{_safe_job_stem(source_audio)}"
    work_dir = SHEETSAGE_WORK_ROOT / job_id
    cover_root = COVERS_ROOT / job_id
    source_dir = cover_root / "source_transcription"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cover_root.mkdir(parents=True, exist_ok=True)

    # Persistimos el audio fuente para que la comparación A/B siga funcionando
    # aunque Gradio limpie su archivo temporal de upload.
    suffix = source_audio.suffix if source_audio.suffix else ".audio"
    persisted_audio = cover_root / f"source_audio{suffix}"
    shutil.copyfile(source_audio, persisted_audio)

    started = time.perf_counter()
    infer_cmd = [
        SHEETSAGE_PYTHON,
        SHEETSAGE_DIR / "infer.py",
        source_audio,
        "--output",
        work_dir,
    ]
    if cot == "melody":
        infer_cmd.append("--melody-only")

    infer_stdout, infer_stderr = _run_external(infer_cmd, cwd=SHEETSAGE_DIR)

    render_stdout = ""
    render_stderr = ""
    if render_outputs:
        rendered_dir = work_dir / "rendered"
        render_cmd = [
            SHEETSAGE_PYTHON,
            SHEETSAGE_DIR / "render.py",
            "--input",
            work_dir,
            "--output",
            rendered_dir,
            "--audio",
            "--score",
            "pdf,svg,png",
        ]
        render_stdout, render_stderr = _run_external(render_cmd, cwd=SHEETSAGE_DIR)

    _copy_tree_without_metadata(work_dir, source_dir)
    elapsed = time.perf_counter() - started

    score_path = source_dir / "score.abc"
    score = _read_text_if_exists(score_path, limit=1_000_000)
    if not score.strip():
        raise gr.Error("SheetSage2 terminó, pero no produjo un score.abc utilizable.")

    metadata = {
        "job_id": job_id,
        "created_at": datetime.now().isoformat(),
        "source_audio": str(persisted_audio),
        "source_transcription": str(source_dir),
        "preservation": preservation,
        "cot": cot,
        "rendered": bool(render_outputs),
        "transcription_seconds": elapsed,
        "infer_stdout_tail": "\n".join(infer_stdout.splitlines()[-12:]),
        "infer_stderr_tail": "\n".join(infer_stderr.splitlines()[-12:]),
        "render_stdout_tail": "\n".join(render_stdout.splitlines()[-12:]),
        "render_stderr_tail": "\n".join(render_stderr.splitlines()[-12:]),
    }
    (cover_root / "cover_source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = safe_json(source_dir / "result.json")
    result["_studio_cover"] = metadata

    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass

    rendered = source_dir / "rendered"
    return {
        "cover_root": cover_root,
        "source_dir": source_dir,
        "source_audio": persisted_audio,
        "score": score,
        "piano": rendered / "piano_mix.wav",
        "pdf": rendered / "score.pdf",
        "result": result,
        "elapsed": elapsed,
        "cot": cot,
        "preservation": preservation,
    }


def _generate_cover_from_abc(
    source_dir, source_audio, abc_text, preservation,
    style, lyrics, seed,
):
    seed = _validate_cover_inputs(style, lyrics, seed)
    if not (abc_text or "").strip():
        raise gr.Error("No hay ABC fuente para generar el cover.")

    source_dir = Path(source_dir) if source_dir else None
    if not source_dir or not source_dir.exists():
        raise gr.Error("Primero transcribe/revisa el audio fuente.")

    cover_root = source_dir.parent
    source_meta = safe_json(cover_root / "cover_source.json")
    prepared_cot = source_meta.get("cot")
    requested_cot = _cover_cot(preservation)
    if prepared_cot and prepared_cot != requested_cot:
        raise gr.Error(
            "Cambiaste el modo de preservación después de transcribir. "
            "Vuelve a pulsar TRANSCRIBE / REVIEW para crear el ABC correcto."
        )

    # Muy importante para un cover: no reescribimos M:, Q: ni K: aquí.
    # YuE2 recibe exactamente el ABC revisado/transcrito por SheetSage2.
    supplied_abc = abc_text.strip() + "\n"
    cot = requested_cot

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"cover-{stamp}-{seed}"
    output_dir = cover_root / "variations" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    with PIPE_LOCK:
        pipe = get_pipe()
        free_before = torch.cuda.mem_get_info()[0] / 1024**3
        song = pipe(
            style=(style or "").strip(),
            lyrics=lyrics,
            cot=cot,
            seed=seed,
            abc=supplied_abc,
            id=run_id,
        )
        result = song.save_artifacts(output_dir)
        free_after = torch.cuda.mem_get_info()[0] / 1024**3

    elapsed = time.perf_counter() - started
    audio_seconds = float(result.get("audio_seconds", 0))
    score = song.abc or supplied_abc
    trunc = result.get("truncated", {})

    provenance = {
        "mode": "cover",
        "created_at": datetime.now().isoformat(),
        "source_audio": str(source_audio or source_meta.get("source_audio", "")),
        "source_transcription": str(source_dir),
        "preservation": preservation,
        "cot": cot,
        "seed": seed,
        "style": (style or "").strip(),
        "generation_seconds": elapsed,
        "output_dir": str(output_dir),
    }
    (output_dir / "cover_metadata.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["_studio_cover"] = provenance

    status = (
        "### Cover terminado\n"
        f"**Preservación:** {preservation}  \n"
        f"**CoT:** `{cot}`  \n"
        f"**Duración:** {audio_seconds:.1f} s  \n"
        f"**Tiempo YuE2:** {elapsed:.1f} s  \n"
        f"**Seed:** `{seed}`  \n"
        f"**Truncamiento:** `{trunc}`  \n"
        f"**VRAM libre antes/después:** {free_before:.2f} / {free_after:.2f} GB  \n"
        f"**Salida:** `{output_dir}`"
    )

    return {
        "audio": output_dir / "audio.flac",
        "score": score,
        "status": status,
        "output_dir": output_dir,
        "result": result,
        "elapsed": elapsed,
    }


def prepare_cover_source(audio_path, preservation, render_outputs):
    data = _transcribe_cover_source(audio_path, preservation, render_outputs)
    status = (
        "### Fuente preparada\n"
        f"**Preservación:** {preservation}  \n"
        f"**CoT que usará YuE2:** `{data['cot']}`  \n"
        f"**Transcripción:** {data['elapsed']:.1f} s  \n"
        f"**Carpeta:** `{data['source_dir']}`  \n\n"
        "Puedes corregir el ABC y después pulsar **GENERATE COVER FROM ABC**."
    )
    return (
        str(data["source_audio"]),
        build_score_viewer(data["score"]),
        data["score"],
        str(data["piano"]) if data["piano"].exists() else None,
        str(data["pdf"]) if data["pdf"].exists() else None,
        status,
        str(data["source_dir"]),
        data["result"],
        gr.update(interactive=True),
    )


def generate_cover_from_review(
    source_audio, source_dir, abc_text, preservation,
    style, lyrics, seed,
):
    data = _generate_cover_from_abc(
        source_dir, source_audio, abc_text, preservation,
        style, lyrics, seed,
    )
    return (
        str(data["audio"]),
        build_score_viewer(data["score"]),
        data["score"],
        data["status"],
        str(data["output_dir"]),
        data["result"],
    )


def auto_cover(
    audio_path, preservation, render_outputs,
    style, lyrics, seed,
):
    seed = _validate_cover_inputs(style, lyrics, seed)
    total_started = time.perf_counter()

    source = _transcribe_cover_source(
        audio_path,
        preservation,
        render_outputs,
    )
    generated = _generate_cover_from_abc(
        source["source_dir"],
        source["source_audio"],
        source["score"],
        preservation,
        style,
        lyrics,
        seed,
    )
    total_elapsed = time.perf_counter() - total_started

    status = (
        "### Auto Cover terminado\n"
        f"**Preservación:** {preservation}  \n"
        f"**CoT:** `{source['cot']}`  \n"
        f"**SheetSage2:** {source['elapsed']:.1f} s  \n"
        f"**YuE2:** {generated['elapsed']:.1f} s  \n"
        f"**Total:** {total_elapsed:.1f} s  \n"
        f"**Fuente transcrita:** `{source['source_dir']}`  \n"
        f"**Cover:** `{generated['output_dir']}`"
    )

    return (
        str(source["source_audio"]),
        build_score_viewer(source["score"]),
        source["score"],
        str(source["piano"]) if source["piano"].exists() else None,
        str(source["pdf"]) if source["pdf"].exists() else None,
        str(source["source_dir"]),
        source["result"],
        gr.update(interactive=True),
        str(generated["audio"]),
        build_score_viewer(generated["score"]),
        generated["score"],
        status,
        str(generated["output_dir"]),
        generated["result"],
    )


def open_cover_root(source_dir):
    if not source_dir:
        raise gr.Error("Todavía no hay una fuente preparada.")
    return open_folder(str(Path(source_dir).parent))


# ---------------------------------------------------------------------------
# AGENT EDIT + MUSICAL UTILITIES
# ---------------------------------------------------------------------------

ABC_NOTE_TOKEN = re.compile(
    r'"(?P<chord>[^"\n]*)"|\[K:(?P<key>[^\]\n]+)\]|'
    r"(?P<acc>\^\^|__|\^|_|=)?(?P<note>[A-Ga-gz])"
    r"(?P<oct>[,']*)(?P<duration>[0-9]*)(?P<tie>-?)"
)
ABC_CHORD_TOKEN = re.compile(
    r"^(?P<root>[A-G](?:bb|##|b|#)?)(?P<quality>m\(maj7\)|maj7|m7b5|dim7|7sus4|sus4|sus2|m7|m6|dim|aug|7|6|m)?"
    r"(?:/(?P<bass>[A-G](?:bb|##|b|#)?))?$"
)
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACC_VALUE = {"=": 0, "_": -1, "__": -2, "^": 1, "^^": 2}
PC_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PC_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
KEY_SIG_COUNTS = {
    "Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "Abm": -7, "Ebm": -6, "Bbm": -5, "Fm": -4, "Cm": -3, "Gm": -2, "Dm": -1,
    "Am": 0, "Em": 1, "Bm": 2, "F#m": 3, "C#m": 4, "G#m": 5, "D#m": 6, "A#m": 7,
}
KEY_BY_PC_MAJOR_FLAT = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "Gb", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
KEY_BY_PC_MAJOR_SHARP = {0: "C", 1: "C#", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
KEY_BY_PC_MINOR_FLAT = {0: "Cm", 1: "C#m", 2: "Dm", 3: "Ebm", 4: "Em", 5: "Fm", 6: "F#m", 7: "Gm", 8: "Abm", 9: "Am", 10: "Bbm", 11: "Bm"}
KEY_BY_PC_MINOR_SHARP = {0: "Cm", 1: "C#m", 2: "Dm", 3: "D#m", 4: "Em", 5: "Fm", 6: "F#m", 7: "Gm", 8: "G#m", 9: "Am", 10: "A#m", 11: "Bm"}


def _pitch_name_pc(name):
    match = re.fullmatch(r"([A-G])(bb|##|b|#)?", name or "")
    if not match:
        raise ValueError(f"Nota/chord root no soportado: {name!r}")
    letter, acc = match.groups()
    delta = {None: 0, "": 0, "b": -1, "bb": -2, "#": 1, "##": 2}[acc]
    return (NATURAL_PC[letter] + delta) % 12


def _key_root_and_mode(key):
    key = (key or "C").strip()
    minor = key.endswith("m")
    root = key[:-1] if minor else key
    return root, minor


def _key_accidentals(key):
    count = KEY_SIG_COUNTS.get(key, 0)
    result = {letter: 0 for letter in NATURAL_PC}
    order = "FCGDAEB" if count > 0 else "BEADGCF"
    for letter in order[:abs(count)]:
        result[letter] = 1 if count > 0 else -1
    return result


def _prefer_flats(key):
    # Prefer the accidental family implied by the key signature, not only
    # by the tonic spelling. Example: G minor has two flats even though
    # its tonic name contains no "b".
    if key in KEY_SIG_COUNTS:
        return KEY_SIG_COUNTS[key] < 0
    root, _ = _key_root_and_mode(key)
    return "b" in root or root in {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"}


def _transpose_key_name(key, semitones):
    root, minor = _key_root_and_mode(key)
    pc = (_pitch_name_pc(root) + int(semitones)) % 12
    flats = _prefer_flats(key)
    if minor:
        return (KEY_BY_PC_MINOR_FLAT if flats else KEY_BY_PC_MINOR_SHARP)[pc]
    return (KEY_BY_PC_MAJOR_FLAT if flats else KEY_BY_PC_MAJOR_SHARP)[pc]


def _spell_pc(pc, prefer_flats=False):
    return (PC_FLAT if prefer_flats else PC_SHARP)[pc % 12]


def _abc_note_from_midi(midi_pitch, prefer_flats=False):
    if not 0 <= midi_pitch <= 127:
        raise ValueError(f"Pitch MIDI fuera de rango: {midi_pitch}")
    name = _spell_pc(midi_pitch % 12, prefer_flats)
    match = re.fullmatch(r"([A-G])([b#]?)", name)
    letter, accidental = match.groups()
    acc_value = {"": 0, "b": -1, "#": 1}[accidental]
    octave = ((midi_pitch - acc_value - NATURAL_PC[letter]) // 12) - 1
    if octave <= 4:
        symbol = letter
        marks = "," * max(0, 4 - octave)
    else:
        symbol = letter.lower()
        marks = "'" * max(0, octave - 5)
    acc = {0: "=", -1: "_", 1: "^"}[acc_value]
    return f"{acc}{symbol}{marks}"


def _transpose_chord_symbol(chord, semitones, prefer_flats=False):
    match = ABC_CHORD_TOKEN.fullmatch(chord or "")
    if not match:
        return chord
    root = _spell_pc(_pitch_name_pc(match.group("root")) + semitones, prefer_flats)
    quality = match.group("quality") or ""
    bass = match.group("bass")
    if bass:
        bass = _spell_pc(_pitch_name_pc(bass) + semitones, prefer_flats)
        return f"{root}{quality}/{bass}"
    return f"{root}{quality}"


def _transpose_music_line(line, source_key, semitones, spelling_key=None):
    current_key = source_key
    local_accidentals = {}
    prefer_flats = _prefer_flats(spelling_key or _transpose_key_name(source_key, semitones))
    out = []
    cursor = 0

    while cursor < len(line):
        if line[cursor] == "|":
            out.append("|")
            local_accidentals = {}
            cursor += 1
            continue

        match = ABC_NOTE_TOKEN.match(line, cursor)
        if not match:
            out.append(line[cursor])
            cursor += 1
            continue

        out.append(line[cursor:match.start()])
        cursor = match.end()

        chord = match.group("chord")
        inline_key = match.group("key")
        if chord is not None:
            out.append(f'"{_transpose_chord_symbol(chord, semitones, prefer_flats)}"')
            continue
        if inline_key is not None:
            new_key = _transpose_key_name(inline_key, semitones)
            out.append(f"[K:{new_key}]")
            current_key = inline_key
            prefer_flats = _prefer_flats(new_key)
            local_accidentals = {}
            continue

        note = match.group("note")
        duration = match.group("duration") or ""
        tie = match.group("tie") or ""
        octave_marks = match.group("oct") or ""
        acc = match.group("acc")

        if note == "z":
            out.append(f"z{duration}{tie}")
            continue

        letter = note.upper()
        octave = 5 if note.islower() else 4
        octave += octave_marks.count("'") - octave_marks.count(",")
        base_midi = 12 * (octave + 1) + NATURAL_PC[letter]
        if acc:
            alteration = ACC_VALUE[acc]
            local_accidentals[letter] = alteration
        else:
            alteration = local_accidentals.get(letter, _key_accidentals(current_key).get(letter, 0))
        midi_pitch = base_midi + alteration + int(semitones)
        out.append(f"{_abc_note_from_midi(midi_pitch, prefer_flats)}{duration}{tie}")

    return "".join(out)


def transpose_score_abc(abc_text, semitones, target_key=None):
    text = (abc_text or "").strip() + "\n"
    semitones = int(semitones)
    if semitones == 0:
        return text

    lines = text.splitlines()
    header_key = None
    voice_keys = {}
    active_voice = None
    output = []

    for line in lines:
        if line.startswith("K:") and active_voice is None:
            source_key = line[2:].strip()
            header_key = source_key
            output.append(f"K:{target_key or _transpose_key_name(source_key, semitones)}")
            continue

        if line in {"V: Vocal", "V: Ins"}:
            active_voice = line.split(":", 1)[1].strip()
            voice_keys.setdefault(active_voice, header_key or "C")
            output.append(line)
            continue

        if active_voice is not None and line.startswith("M:"):
            output.append(line)
            continue

        if active_voice is not None and line.startswith("K:"):
            source_key = line[2:].strip()
            voice_keys[active_voice] = source_key
            output.append(f"K:{_transpose_key_name(source_key, semitones)}")
            continue

        if active_voice is not None:
            source_key = voice_keys.get(active_voice, header_key or "C")
            output.append(_transpose_music_line(line, source_key, semitones, spelling_key=target_key))
            active_voice = None
            continue

        output.append(line)

    return "\n".join(output).rstrip() + "\n"


def _shortest_key_transposition(source_key, target_key):
    """Return the smallest signed semitone shift between compatible keys."""
    source_root, source_minor = _key_root_and_mode(source_key)
    target_root, target_minor = _key_root_and_mode(target_key)
    if source_minor != target_minor:
        source_mode = "minor" if source_minor else "major"
        target_mode = "minor" if target_minor else "major"
        raise ValueError(
            f"La tonalidad solicitada es {target_key} ({target_mode}), pero el score "
            f"está en {source_key} ({source_mode}). Cambiar mayor↔menor no es una "
            "transposición exacta. No se generó audio: usa otra seed o Agent Edit "
            "para una conversión modal explícita."
        )
    delta = (_pitch_name_pc(target_root) - _pitch_name_pc(source_root)) % 12
    if delta > 6:
        delta -= 12
    return delta


def prepare_abc_for_target(abc_text, bpm, key, meter, origin="ABC"):
    """Make BPM/key controls musically truthful for a native YuE2 score.

    - BPM: rewritten safely in Q:.
    - Key: notes, chord symbols and key signature are really transposed.
    - Meter: must already match; changing only M: would corrupt bar durations.
    """
    text = (abc_text or "").strip()
    if not text:
        raise ValueError(f"{origin}: no hay score ABC.")

    meter_match = re.search(r"(?m)^M\s*:\s*([^\s]+)", text)
    key_match = re.search(r"(?m)^K\s*:\s*([^\s]+)", text)
    if not meter_match:
        raise ValueError(f"{origin}: falta el campo M: del compás.")
    if not key_match:
        raise ValueError(f"{origin}: falta el campo K: de tonalidad.")

    source_meter = meter_match.group(1)
    source_key = key_match.group(1)

    if source_meter != meter:
        raise ValueError(
            f"YuE2 produjo `{source_meter}` pero solicitaste `{meter}`. "
            "Cambiar solamente la cabecera M: haría inválidas las duraciones de "
            "los compases, así que YuE2 Studio detuvo la generación antes del audio. "
            "Prueba otra seed o modifica la métrica en Agent Edit."
        )

    if source_key not in KEY_SIG_COUNTS:
        raise ValueError(
            f"{origin}: tonalidad `{source_key}` no soportada por la transposición segura."
        )
    if key not in KEY_SIG_COUNTS:
        raise ValueError(
            f"Tonalidad objetivo `{key}` no soportada por la transposición segura."
        )

    semitones = _shortest_key_transposition(source_key, key)
    if semitones:
        text = transpose_score_abc(text, semitones, target_key=key)

    # Force the user's exact enharmonic spelling and requested tempo after the
    # pitch/chord transposition. Meter stays untouched because it was verified.
    text = _replace_or_insert_header(text, "K", key)
    text = _replace_or_insert_header(text, "Q", f"1/4={int(bpm)}")
    text = text.rstrip() + "\n"

    final_bpm, final_key, final_meter = extract_abc_controls(text)
    if final_key != key:
        raise ValueError(
            f"No se pudo verificar la tonalidad final: esperado `{key}`, obtenido `{final_key}`."
        )
    if final_meter != meter:
        raise ValueError(
            f"No se pudo verificar el compás final: esperado `{meter}`, obtenido `{final_meter}`."
        )

    validation = validate_abc_structure(text)
    if validation.get("available") and not validation.get("ok"):
        detail = validation.get("error") or validation.get("stderr") or validation.get("data") or "error estructural desconocido"
        raise ValueError(
            "La corrección de tonalidad produjo un ABC que no pasó la validación estructural. "
            f"No se generó audio. Detalle: {detail}"
        )

    return text, {
        "source_key": source_key,
        "target_key": key,
        "key_transposition_semitones": int(semitones),
        "source_meter": source_meter,
        "target_meter": meter,
        "target_bpm": int(bpm),
        "key_enforcement": "verified_symbolic_transposition",
        "meter_enforcement": "verified_existing_meter",
        "tempo_enforcement": "verified_header_tempo",
    }


def _music_line_indices(abc_text):
    indices = []
    active_voice = None
    lines = abc_text.splitlines()
    for i, line in enumerate(lines):
        if line in {"V: Vocal", "V: Ins"}:
            active_voice = line.split(":", 1)[1].strip()
            continue
        if active_voice is not None and line.startswith(("M:", "K:")):
            continue
        if active_voice is not None:
            indices.append((i, active_voice))
            active_voice = None
    return lines, indices


def _reharmonize_chord(chord, preset, key):
    match = ABC_CHORD_TOKEN.fullmatch(chord or "")
    if not match:
        return chord
    root = match.group("root")
    quality = match.group("quality") or ""
    bass = match.group("bass")

    if preset == "Añadir color de séptima (raíces iguales)":
        quality = {"": "maj7", "m": "m7", "dim": "m7b5"}.get(quality, quality)
    elif preset == "Séptimas diatónicas según tonalidad":
        key_root, minor = _key_root_and_mode(key)
        tonic = _pitch_name_pc(key_root)
        degree = (_pitch_name_pc(root) - tonic) % 12
        if minor:
            qualities = {0: "m(maj7)", 2: "m7b5", 3: "maj7", 5: "m7", 7: "7", 8: "maj7", 11: "dim7"}
        else:
            qualities = {0: "maj7", 2: "m7", 4: "m7", 5: "maj7", 7: "7", 9: "m7", 11: "m7b5"}
        quality = qualities.get(degree, quality)
    elif preset == "Sustitución tritonal de dominantes 7":
        if quality == "7":
            root = _spell_pc(_pitch_name_pc(root) + 6, prefer_flats=True)
    elif preset == "Quitar acordes":
        return ""

    rebuilt = f"{root}{quality}"
    if bass:
        rebuilt += f"/{bass}"
    return rebuilt


def reharmonize_score_abc(abc_text, preset):
    text = (abc_text or "").strip() + "\n"
    bpm, key, meter = extract_abc_controls(text)
    lines, music = _music_line_indices(text)
    for index, voice in music:
        if voice != "Vocal":
            continue
        def replace(match):
            chord = match.group("chord")
            if chord is None:
                return match.group(0)
            new_chord = _reharmonize_chord(chord, preset, key)
            return f'"{new_chord}"' if new_chord else ""
        lines[index] = ABC_NOTE_TOKEN.sub(replace, lines[index])
    return "\n".join(lines).rstrip() + "\n"


def change_tempo_abc(abc_text, bpm):
    text = (abc_text or "").strip()
    if not text:
        raise gr.Error("No hay ABC para cambiar tempo.")
    return _replace_or_insert_header(text, "Q", f"1/4={int(bpm)}").rstrip() + "\n"


def _split_form_sections(abc_text):
    lines = (abc_text or "").splitlines(keepends=True)
    starts = []
    in_comment_group = False
    for i, line in enumerate(lines):
        is_comment = line.startswith("% ")
        if is_comment and not in_comment_group:
            starts.append(i)
        in_comment_group = is_comment
    if not starts:
        return "".join(lines), []
    preamble = "".join(lines[:starts[0]])
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        comments = [x[2:].strip() for x in block_lines if x.startswith("% ")]
        label = " / ".join(comments) if comments else f"Section {n + 1}"
        blocks.append({"label": label, "text": "".join(block_lines)})
    return preamble, blocks


def form_section_choices(abc_text):
    _, blocks = _split_form_sections(abc_text)
    return [f"{i + 1} — {block['label']}" for i, block in enumerate(blocks)]


def transform_form_abc(abc_text, section_choice, action):
    preamble, blocks = _split_form_sections(abc_text)
    if not blocks:
        raise gr.Error("El score no tiene comentarios de sección (% ...) para editar la forma de manera segura.")
    if not section_choice:
        raise gr.Error("Selecciona una sección.")
    try:
        index = int(str(section_choice).split("—", 1)[0].strip()) - 1
    except Exception:
        raise gr.Error("No pude identificar la sección seleccionada.")
    if not 0 <= index < len(blocks):
        raise gr.Error("La sección seleccionada ya no existe. Pulsa Actualizar secciones.")

    if action == "Duplicar":
        copy_block = dict(blocks[index])
        copy_block["text"] = re.sub(
            r"(?m)^% (.+)$",
            lambda m: f"% {m.group(1)} (copy)",
            copy_block["text"],
            count=1,
        )
        copy_block["label"] += " (copy)"
        blocks.insert(index + 1, copy_block)
    elif action == "Eliminar":
        if len(blocks) <= 1:
            raise gr.Error("No puedo eliminar la única sección del score.")
        blocks.pop(index)
    elif action == "Mover arriba":
        if index == 0:
            raise gr.Error("La sección ya está al principio.")
        blocks[index - 1], blocks[index] = blocks[index], blocks[index - 1]
    elif action == "Mover abajo":
        if index == len(blocks) - 1:
            raise gr.Error("La sección ya está al final.")
        blocks[index + 1], blocks[index] = blocks[index], blocks[index + 1]
    else:
        raise gr.Error(f"Acción de forma desconocida: {action}")

    result = preamble + "".join(block["text"] for block in blocks)
    return result.rstrip() + "\n"


def _run_abc_tools(args, before_text=None, after_text=None):
    if not ABC_TOOLS.exists():
        return {
            "available": False,
            "ok": True,
            "warning": f"No encuentro el validador ABC local: {ABC_TOOLS}",
        }
    with tempfile.TemporaryDirectory(prefix="yue2-abc-") as tmp:
        tmp = Path(tmp)
        command = [sys.executable, str(ABC_TOOLS)]
        if before_text is None:
            score = tmp / "score.abc"
            score.write_text(after_text or "", encoding="utf-8")
            command += ["inspect", str(score)]
        else:
            before = tmp / "before.abc"
            after = tmp / "after.abc"
            before.write_text(before_text, encoding="utf-8")
            after.write_text(after_text or "", encoding="utf-8")
            command += ["compare", str(before), str(after), *args]
        proc = subprocess.run(command, text=True, capture_output=True)
        if proc.returncode == 2:
            return {"available": True, "ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except Exception:
            data = {"raw_stdout": proc.stdout.strip()}
        return {
            "available": True,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "data": data,
            "stderr": proc.stderr.strip(),
        }


def validate_abc_structure(abc_text):
    return _run_abc_tools([], after_text=abc_text)


def compare_abc_invariant(before_text, after_text, voices="both", allow_tempo_change=False):
    args = ["--voices", voices]
    if allow_tempo_change:
        args.append("--allow-tempo-change")
    return _run_abc_tools(args, before_text=before_text, after_text=after_text)


def _manifest_base(kind, source_abc, edited_abc, details=None):
    return {
        "kind": kind,
        "created_at": datetime.now().isoformat(),
        "source_sha256": hashlib.sha256((source_abc or "").encode()).hexdigest(),
        "edited_sha256": hashlib.sha256((edited_abc or "").encode()).hexdigest(),
        "details": details or {},
    }


def apply_transpose_utility(working_abc, semitones):
    if not (working_abc or "").strip():
        raise gr.Error("Carga un score antes de transponer.")
    semitones = int(semitones)
    edited = transpose_score_abc(working_abc, semitones)
    validation = validate_abc_structure(edited)
    if not validation.get("ok"):
        raise gr.Error(f"La transposición produjo ABC no válido: {validation.get('error') or validation}")
    manifest = _manifest_base("transpose", working_abc, edited, {"semitones": semitones, "validation": validation})
    _, new_key, _ = extract_abc_controls(edited)
    status = f"Transposición aplicada: **{semitones:+d} semitonos** · nueva tonalidad `{new_key}`."
    return edited, build_score_viewer(edited), manifest, status, gr.update(choices=form_section_choices(edited), value=None)


def apply_tempo_utility(working_abc, bpm):
    if not (working_abc or "").strip():
        raise gr.Error("Carga un score antes de cambiar tempo.")
    edited = change_tempo_abc(working_abc, bpm)
    validation = validate_abc_structure(edited)
    comparison = compare_abc_invariant(working_abc, edited, voices="both", allow_tempo_change=True)
    if not validation.get("ok") or (comparison.get("available") and not comparison.get("ok")):
        raise gr.Error("El cambio de tempo alteró algo más que el tempo o produjo ABC inválido.")
    manifest = _manifest_base("tempo", working_abc, edited, {"bpm": int(bpm), "validation": validation, "melody_invariant": comparison})
    return edited, build_score_viewer(edited), manifest, f"Tempo cambiado a **{int(bpm)} BPM** sin alterar las melodías.", gr.update(choices=form_section_choices(edited), value=None)


def apply_reharm_utility(working_abc, preset):
    if not (working_abc or "").strip():
        raise gr.Error("Carga un score antes de reharmonizar.")
    edited = reharmonize_score_abc(working_abc, preset)
    validation = validate_abc_structure(edited)
    comparison = compare_abc_invariant(working_abc, edited, voices="both", allow_tempo_change=False)
    if not validation.get("ok"):
        raise gr.Error(f"La reharmonización produjo ABC no válido: {validation.get('error') or validation}")
    if comparison.get("available") and not comparison.get("ok"):
        raise gr.Error("La reharmonización determinista alteró la melodía; se descartó el cambio.")
    manifest = _manifest_base("reharmonization", working_abc, edited, {"preset": preset, "validation": validation, "melody_invariant": comparison})
    return edited, build_score_viewer(edited), manifest, f"Preset aplicado: **{preset}**. Las melodías se conservaron exactamente.", gr.update(choices=form_section_choices(edited), value=None)


def apply_form_utility(working_abc, section_choice, action):
    if not (working_abc or "").strip():
        raise gr.Error("Carga un score antes de cambiar la forma.")
    edited = transform_form_abc(working_abc, section_choice, action)
    validation = validate_abc_structure(edited)
    if not validation.get("ok"):
        raise gr.Error(f"La operación de forma produjo ABC no válido: {validation.get('error') or validation}")
    manifest = _manifest_base("form", working_abc, edited, {"section": section_choice, "action": action, "validation": validation})
    choices = form_section_choices(edited)
    return edited, build_score_viewer(edited), manifest, f"Forma modificada: **{action}** · `{section_choice}`.", gr.update(choices=choices, value=choices[0] if choices else None)


def refresh_form_sections(abc_text):
    choices = form_section_choices(abc_text)
    return gr.update(choices=choices, value=choices[0] if choices else None)


def _agent_endpoint_candidates(endpoint):
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return []
    candidates = [endpoint]
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.hostname in {"127.0.0.1", "localhost"}:
            resolv = Path("/etc/resolv.conf")
            if resolv.exists():
                match = re.search(r"(?m)^nameserver\s+([0-9.]+)", resolv.read_text(errors="ignore"))
                if match:
                    host = match.group(1)
                    netloc = f"{host}:{parsed.port}" if parsed.port else host
                    fallback = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
                    if fallback not in candidates:
                        candidates.append(fallback)
    except Exception:
        pass
    return candidates


def _http_json(url, payload=None, api_key="", timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_agent_models(endpoint, api_key):
    errors = []
    for candidate in _agent_endpoint_candidates(endpoint):
        parsed = urllib.parse.urlsplit(candidate)
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
        for url, kind in [(base + "/api/tags", "ollama"), (base + "/v1/models", "openai")]:
            try:
                data = _http_json(url, api_key=api_key, timeout=8)
                if kind == "ollama":
                    models = [x.get("name") for x in data.get("models", []) if x.get("name")]
                else:
                    models = [x.get("id") for x in data.get("data", []) if x.get("id")]
                if models:
                    return gr.update(choices=models, value=models[0]), f"Encontré **{len(models)}** modelos en `{base}`."
            except Exception as exc:
                errors.append(f"{url}: {exc}")
    return gr.update(), "No pude descubrir modelos. Puedes escribir el nombre manualmente.\n\n" + "\n".join(errors[-2:])


def _agent_chat(endpoint, model, api_key, messages, timeout=600):
    if not model:
        raise gr.Error("Escribe o selecciona el modelo del agente.")
    last_error = None
    for candidate in _agent_endpoint_candidates(endpoint):
        try:
            if candidate.rstrip("/").endswith("/api/chat"):
                payload = {"model": model, "messages": messages, "stream": False, "format": "json", "options": {"temperature": 0.1}}
                data = _http_json(candidate, payload=payload, api_key=api_key, timeout=timeout)
                return data.get("message", {}).get("content", ""), candidate
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "stream": False,
            }
            data = _http_json(candidate, payload=payload, api_key=api_key, timeout=timeout)
            return data["choices"][0]["message"]["content"], candidate
        except Exception as exc:
            last_error = exc
    raise gr.Error(f"No pude conectar con el agente: {last_error}")


def test_agent_backend(endpoint, model, api_key):
    content, used = _agent_chat(
        endpoint, model, api_key,
        [{"role": "user", "content": 'Respond only with JSON: {"ok": true}'}],
        timeout=60,
    )
    return f"Conexión correcta con `{model}` mediante `{used}`. Respuesta: `{content[:120]}`"


def _extract_json_object(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _contract_compare_settings(contract, permissions):
    permissions = set(permissions or [])
    if "Forma" in permissions or "Compás" in permissions:
        return None, "La edición permite Forma/Compás; no se puede exigir igualdad global de la rejilla de compases."
    if contract == "Exacta: ambas melodías + ritmo":
        return "both", None
    if contract == "Exacta: Vocal; instrumental puede cambiar":
        return "Vocal", None
    if contract == "Exacta: Instrumental; vocal puede cambiar":
        return "Ins", None
    return None, "Contrato cualitativo: requiere revisión musical además de validación estructural."


def validate_agent_candidate(source_abc, edited_abc, contract, permissions):
    permissions = set(permissions or [])
    structural = validate_abc_structure(edited_abc)
    source_structural = validate_abc_structure(source_abc)
    if not structural.get("ok"):
        return False, {"structural": structural, "source_structural": source_structural, "contract": None}, "ABC estructuralmente inválido."

    permission_violations = []
    src_data = source_structural.get("data", {}) if source_structural.get("available") else {}
    edt_data = structural.get("data", {}) if structural.get("available") else {}
    if src_data and edt_data:
        if "Tempo" not in permissions and src_data.get("bpm") != edt_data.get("bpm"):
            permission_violations.append("tempo cambiado sin permiso")

        src_v = src_data.get("voices", {}).get("Vocal", {})
        edt_v = edt_data.get("voices", {}).get("Vocal", {})
        src_i = src_data.get("voices", {}).get("Ins", {})
        edt_i = edt_data.get("voices", {}).get("Ins", {})

        if "Forma" not in permissions and src_v.get("bars") != edt_v.get("bars"):
            permission_violations.append("forma/rejilla de compases cambiada sin permiso")
        if "Compás" not in permissions:
            src_meters = {json.dumps(bar[2], sort_keys=True) for bar in src_v.get("bars", []) if len(bar) >= 3}
            edt_meters = {json.dumps(bar[2], sort_keys=True) for bar in edt_v.get("bars", []) if len(bar) >= 3}
            if not edt_meters.issubset(src_meters or edt_meters):
                permission_violations.append("apareció un compás no presente en la fuente")

    # Los permisos de melodía son también límites duros cuando la forma/compás
    # permiten una comparación global exacta.
    if "Forma" not in permissions and "Compás" not in permissions:
        if "Melodía vocal" not in permissions:
            vocal_check = compare_abc_invariant(source_abc, edited_abc, voices="Vocal", allow_tempo_change="Tempo" in permissions)
            if vocal_check.get("available") and not vocal_check.get("ok"):
                permission_violations.append("melodía Vocal cambiada sin permiso")
        if "Melodía instrumental" not in permissions:
            ins_check = compare_abc_invariant(source_abc, edited_abc, voices="Ins", allow_tempo_change="Tempo" in permissions)
            if ins_check.get("available") and not ins_check.get("ok"):
                permission_violations.append("melodía instrumental cambiada sin permiso")

    voices, note = _contract_compare_settings(contract, permissions)
    contract_result = None
    if voices:
        contract_result = compare_abc_invariant(
            source_abc,
            edited_abc,
            voices=voices,
            allow_tempo_change="Tempo" in permissions,
        )
        if contract_result.get("available") and not contract_result.get("ok"):
            return False, {
                "structural": structural,
                "source_structural": source_structural,
                "contract": contract_result,
                "permission_violations": permission_violations,
            }, "El score viola el contrato de preservación exacta."

    if permission_violations:
        return False, {
            "structural": structural,
            "source_structural": source_structural,
            "contract": contract_result,
            "permission_violations": permission_violations,
        }, "Violaciones de permisos: " + "; ".join(permission_violations)

    return True, {
        "structural": structural,
        "source_structural": source_structural,
        "contract": contract_result,
        "permission_violations": [],
        "note": note,
    }, note or "Validación estructural, permisos y contrato exacto superados."


def _agent_system_prompt(contract, permissions):
    allowed = ", ".join(permissions or []) or "ningún cambio adicional salvo el pedido explícito"
    return f"""You are a bounded musical score editor for YuE2 native ABC.
Return ONLY one valid JSON object; no markdown fences and no prose outside JSON.

Native score constraints:
- Preserve X:1, blank T:, M:, L:, Q:, the exact two native voice definitions, and K:.
- Use only the native voices Vocal and Ins.
- Keep bar grouping and section comments unless Form is explicitly allowed.
- Do not add w: lyric fields, tuplets, grace notes, polyphonic note stacks, repeat signs, alternate endings, slurs, broken rhythms, custom voices or directives.
- Chord symbols must remain simple YuE2-supported symbols: triads, m, dim, aug, 7, maj7, m7, dim7, m7b5, sus4, sus2, 6, m6, 7sus4, m(maj7), optionally slash bass.
- Harmony symbols belong in the Vocal music line, not Ins.
- Produce a COMPLETE edited score, including untouched passages.
- Never claim to have listened to audio; you are editing notation and request text only.

Preservation contract: {contract}
Permitted change categories: {allowed}
The preservation contract outranks any broader permission.

Output schema exactly:
{{
  "edited_abc": "complete ABC text",
  "style": "complete revised or preserved style prompt",
  "lyrics": "complete revised or preserved lyrics",
  "rationale": "brief musical rationale grounded in actual score changes",
  "manifest": {{
    "changed_sections": [],
    "harmonic_changes": [],
    "melody_or_rhythm_changes": [],
    "tempo_meter_form_changes": [],
    "lyrics_style_changes": [],
    "preserved_invariants": []
  }}
}}
"""


def run_agent_edit(
    endpoint, model, api_key, instruction, contract, permissions,
    source_abc, working_abc, style, lyrics,
):
    if not (working_abc or "").strip():
        raise gr.Error("Carga primero un score.")
    if not (instruction or "").strip():
        raise gr.Error("Escribe la instrucción musical para el agente.")
    source_abc = source_abc or working_abc
    prompt = f"""USER EDIT REQUEST:\n{instruction.strip()}\n\nCURRENT STYLE:\n{style or ''}\n\nCURRENT LYRICS:\n{lyrics or ''}\n\nSOURCE ABC FOR CONTRACT CHECK:\n{source_abc}\n\nCURRENT WORKING ABC TO EDIT:\n{working_abc}\n"""
    content, used_endpoint = _agent_chat(
        endpoint, model, api_key,
        [
            {"role": "system", "content": _agent_system_prompt(contract, permissions)},
            {"role": "user", "content": prompt},
        ],
    )
    try:
        payload = _extract_json_object(content)
    except Exception as exc:
        raise gr.Error(f"El agente no devolvió JSON utilizable: {exc}\n\nRespuesta: {content[:1200]}")

    edited = (payload.get("edited_abc") or "").strip() + "\n"
    if not edited.strip():
        raise gr.Error("El agente no devolvió edited_abc.")
    ok, validation, validation_note = validate_agent_candidate(source_abc, edited, contract, permissions)

    returned_style = payload.get("style", style or "")
    returned_lyrics = payload.get("lyrics", lyrics or "")
    if "Style prompt" not in set(permissions or []):
        returned_style = style or ""
    if "Letra" not in set(permissions or []):
        returned_lyrics = lyrics or ""

    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    manifest.update(_manifest_base("agent_edit", source_abc, edited, {
        "instruction": instruction,
        "contract": contract,
        "permissions": permissions,
        "backend": used_endpoint,
        "model": model,
        "validation": validation,
    }))
    rationale = payload.get("rationale") or "Sin rationale."
    status_prefix = "✅" if ok else "⚠️"
    status = f"{status_prefix} **Agent Edit** · {validation_note}\n\nBackend: `{used_endpoint}` · modelo: `{model}`"
    return (
        returned_style,
        returned_lyrics,
        edited,
        build_score_viewer(edited),
        manifest,
        rationale,
        status,
        gr.update(interactive=bool(ok)),
        gr.update(choices=form_section_choices(edited), value=None),
    )


def validate_agent_working(source_abc, working_abc, contract, permissions):
    if not (working_abc or "").strip():
        raise gr.Error("No hay ABC para validar.")
    ok, validation, note = validate_agent_candidate(source_abc or working_abc, working_abc, contract, permissions)
    status = ("✅ " if ok else "⚠️ ") + note
    return build_score_viewer(working_abc), validation, status, gr.update(interactive=bool(ok)), gr.update(choices=form_section_choices(working_abc), value=None)


def load_run_for_agent(label):
    run_dir = resolve_run(label)
    request = safe_json(run_dir / "request.json")
    abc = (run_dir / "score.abc").read_text(encoding="utf-8") if (run_dir / "score.abc").exists() else ""
    if not abc.strip():
        raise gr.Error("La generación seleccionada no tiene score.abc.")
    cot = request.get("cot", "full")
    if cot == "off":
        cot = "full"
    choices = form_section_choices(abc)
    audio = run_dir / "audio.flac"
    return (
        str(audio) if audio.exists() else None,
        strip_ui_controls(request.get("style", "")),
        request.get("lyrics", ""),
        abc,
        abc,
        build_score_viewer(abc),
        cot if cot in {"full", "melody"} else "full",
        int(request.get("seed", 831001)),
        gr.update(choices=choices, value=choices[0] if choices else None),
        f"Fuente cargada: `{run_dir}`",
        {},
        "",
        gr.update(interactive=True),
    )


def reset_agent_working(source_abc):
    if not source_abc:
        raise gr.Error("Primero carga una fuente.")
    choices = form_section_choices(source_abc)
    return source_abc, build_score_viewer(source_abc), {}, "Score restaurado a la fuente original.", gr.update(choices=choices, value=choices[0] if choices else None), gr.update(interactive=True)


def refresh_agent_sources():
    choices = library_choices()
    return gr.update(choices=choices, value=choices[0] if choices else None)


def render_agent_edit(source_label, source_abc, contract, permissions, style, lyrics, abc_text, cot, seed, manifest):
    if not (abc_text or "").strip():
        raise gr.Error("No hay ABC editado para renderizar.")
    if not (style or "").strip():
        raise gr.Error("Escribe el Style Prompt.")
    if not (lyrics or "").strip():
        raise gr.Error("Escribe la letra.")
    try:
        seed = int(seed)
    except Exception:
        raise gr.Error("La seed debe ser un entero.")

    ok, validation, validation_note = validate_agent_candidate(source_abc or abc_text, abc_text, contract, permissions)
    if not ok:
        raise gr.Error(f"La edición no pasa el contrato/permisos actuales: {validation_note}")

    source_dir = resolve_run(source_label) if source_label else None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"agent-{stamp}-{seed}"
    output_dir = AGENT_EDITS_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    with PIPE_LOCK:
        pipe = get_pipe()
        free_before = torch.cuda.mem_get_info()[0] / 1024**3
        song = pipe(
            style=(style or "").strip(),
            lyrics=lyrics,
            cot=cot,
            seed=seed,
            abc=abc_text.strip() + "\n",
            id=run_id,
        )
        result = song.save_artifacts(output_dir)
        free_after = torch.cuda.mem_get_info()[0] / 1024**3
    elapsed = time.perf_counter() - started

    metadata = {
        "mode": "agent_edit",
        "created_at": datetime.now().isoformat(),
        "source_run": str(source_dir) if source_dir else "",
        "manifest": manifest or {},
        "validation_before_render": validation,
    }
    (output_dir / "agent_edit_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["_studio_agent_edit"] = metadata
    score = song.abc or abc_text
    audio_seconds = float(result.get("audio_seconds", 0))
    status = (
        "### Agent Edit renderizado\n"
        f"**Duración:** {audio_seconds:.1f} s  \n"
        f"**Tiempo YuE2:** {elapsed:.1f} s  \n"
        f"**CoT:** `{cot}`  \n"
        f"**Seed:** `{seed}`  \n"
        f"**VRAM libre antes/después:** {free_before:.2f} / {free_after:.2f} GB  \n"
        f"**Salida:** `{output_dir}`"
    )
    return str(output_dir / "audio.flac"), build_score_viewer(score), score, status, str(output_dir), result



# ---------------------------------------------------------------------------
# EDIT SCORE
# ---------------------------------------------------------------------------

def load_run_for_edit(label):
    run_dir = resolve_run(label)
    request = safe_json(run_dir / "request.json")
    score = (
        (run_dir / "score.abc").read_text(encoding="utf-8")
        if (run_dir / "score.abc").exists()
        else ""
    )

    bpm, key, meter = extract_abc_controls(score)
    cot = request.get("cot", "full")
    if cot == "off":
        cot = "full"

    return (
        strip_ui_controls(request.get("style", "")),
        request.get("lyrics", ""),
        score,
        cot,
        int(request.get("seed", 831001)),
        bpm,
        key,
        meter,
        build_score_viewer(score),
        f"Cargado para edición: `{run_dir}`",
    )


def regenerate_edited_score(
    source_label, style, lyrics, abc_text,
    cot, seed, bpm, key, meter
):
    if not (abc_text or "").strip():
        raise gr.Error("El editor necesita un score ABC.")
    if cot not in {"full", "melody"}:
        raise gr.Error("Para editar score usa CoT full o melody.")
    if not (style or "").strip():
        raise gr.Error("Escribe el Style Prompt.")
    if not (lyrics or "").strip():
        raise gr.Error("Escribe la letra.")

    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise gr.Error("La seed debe ser un número entero.")

    edited_abc = apply_abc_controls(abc_text, bpm, key, meter)
    final_style = augment_style(style, bpm, key, meter)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"edit-{stamp}-{seed}"
    output_dir = STUDIO_ROOT / run_id

    started = time.perf_counter()
    with PIPE_LOCK:
        pipe = get_pipe()
        song = pipe(
            style=final_style,
            lyrics=lyrics,
            cot=cot,
            seed=seed,
            abc=edited_abc,
            id=run_id,
        )
        result = song.save_artifacts(output_dir)

    source_dir = ""
    if source_label:
        try:
            source_dir = str(resolve_run(source_label))
        except Exception:
            source_dir = ""
    (output_dir / "edit_metadata.json").write_text(
        json.dumps(
            {
                "source_run": source_dir,
                "created_at": datetime.now().isoformat(),
                "mode": "score_edit",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    audio_seconds = float(result.get("audio_seconds", 0))
    score = song.abc or edited_abc
    trunc = result.get("truncated", {})

    status = (
        "### Versión editada terminada\n"
        f"**Fuente:** `{source_label}`  \n"
        f"**Audio:** {audio_seconds:.1f} s  \n"
        f"**Tiempo de cómputo:** {elapsed:.1f} s  \n"
        f"**Seed:** `{seed}`  \n"
        f"**CoT:** `{cot}`  \n"
        f"**BPM / tonalidad / compás:** `{int(bpm)} / {key} / {meter}`  \n"
        f"**Truncamiento:** `{trunc}`  \n"
        f"**Salida:** `{output_dir}`"
    )

    return (
        str(output_dir / "audio.flac"),
        build_score_viewer(score),
        score,
        status,
        str(output_dir),
        result,
    )


# ---------------------------------------------------------------------------
# COMPARE
# ---------------------------------------------------------------------------

def compare_runs(label_a, label_b):
    run_a = resolve_run(label_a)
    run_b = resolve_run(label_b)

    result_a = safe_json(run_a / "result.json")
    result_b = safe_json(run_b / "result.json")
    req_a = safe_json(run_a / "request.json")
    req_b = safe_json(run_b / "request.json")

    score_a = (
        (run_a / "score.abc").read_text(encoding="utf-8")
        if (run_a / "score.abc").exists()
        else ""
    )
    score_b = (
        (run_b / "score.abc").read_text(encoding="utf-8")
        if (run_b / "score.abc").exists()
        else ""
    )

    dur_a = result_a.get("audio_seconds", 0)
    dur_b = result_b.get("audio_seconds", 0)
    same_seed = req_a.get("seed") == req_b.get("seed")
    same_lyrics = req_a.get("lyrics") == req_b.get("lyrics")
    same_style = req_a.get("style") == req_b.get("style")
    same_score = score_a == score_b and bool(score_a)

    summary = (
        "### Comparación\n"
        f"**A:** `{label_a}`  \n"
        f"**B:** `{label_b}`  \n"
        f"**Duración A / B:** {dur_a:.1f} s / {dur_b:.1f} s  \n"
        f"**Diferencia:** {dur_b - dur_a:+.1f} s  \n"
        f"**Misma seed:** {'Sí' if same_seed else 'No'}  \n"
        f"**Misma letra:** {'Sí' if same_lyrics else 'No'}  \n"
        f"**Mismo style exacto:** {'Sí' if same_style else 'No'}  \n"
        f"**Mismo score ABC exacto:** {'Sí' if same_score else 'No'}"
    )

    return (
        str(run_a / "audio.flac") if (run_a / "audio.flac").exists() else None,
        run_metadata_markdown(run_a),
        build_score_viewer(score_a),
        str(run_b / "audio.flac") if (run_b / "audio.flac").exists() else None,
        run_metadata_markdown(run_b),
        build_score_viewer(score_b),
        summary,
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

CSS = """
#title {text-align:center;margin-bottom:.15rem}
#subtitle {text-align:center;opacity:.78;margin-bottom:1rem}
.gradio-container {max-width:1550px !important;}
"""

initial_choices = library_choices()
initial_a = initial_choices[0] if initial_choices else None
initial_b = initial_choices[1] if len(initial_choices) > 1 else initial_a

with gr.Blocks(title=APP_NAME, css=CSS) as demo:
    gr.Markdown(f"# {APP_NAME}", elem_id="title")
    gr.Markdown(
        "Create · Transcribe · Cover · Agent Edit · Edit Score · Compare · Library · YuE2 + SheetSage2 local",
        elem_id="subtitle",
    )

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------
    with gr.Tab("🎵 Create"):
        with gr.Row():
            with gr.Column(scale=3):
                create_style = gr.Textbox(
                    label="Style Prompt",
                    value=(
                        "retro synthwave post-punk, dark nocturnal atmosphere, "
                        "melodic bass, chorus electric guitars, analog synths"
                    ),
                    lines=3,
                )
                create_lyrics = gr.Textbox(
                    label="Lyrics",
                    lines=18,
                    placeholder="[Verse]\n...\n\n[Chorus]\n...",
                )

            with gr.Column(scale=2):
                with gr.Row():
                    create_bpm = gr.Slider(
                        40, 220, value=88, step=1, label="BPM"
                    )
                    create_key = gr.Dropdown(
                        KEY_NAMES, value="C", label="Tonalidad objetivo",
                        info="Con CoT full/melody se verifica y transpone el score antes de generar audio."
                    )

                with gr.Row():
                    create_meter = gr.Dropdown(
                        METERS, value="4/4", label="Compás objetivo",
                        info="Se verifica en el score; no se reescribe solo la cabecera si YuE2 genera otro compás."
                    )
                    create_duration_mode = gr.Radio(
                        choices=["Automática", "Estricto"],
                        value="Automática",
                        label="Duración",
                        info="Automática deja que YuE2 cierre la canción.",
                    )

                create_target_duration = gr.Slider(
                    30, 360, value=180, step=1,
                    label="Duración estricta objetivo (s)",
                    visible=False,
                )
                create_duration_info = gr.Markdown(
                    duration_preview(180),
                    visible=False,
                )

                create_duration_mode.change(
                    fn=toggle_duration_controls,
                    inputs=create_duration_mode,
                    outputs=[
                        create_target_duration,
                        create_duration_info,
                    ],
                    queue=False,
                )
                create_target_duration.change(
                    fn=duration_preview,
                    inputs=create_target_duration,
                    outputs=create_duration_info,
                    queue=False,
                )

                create_cot = gr.Radio(
                    choices=["full", "melody", "off"],
                    value="full",
                    label="CoT",
                    info="full: melodía+acordes · melody: melodía · off: sin ABC",
                )

                with gr.Row():
                    create_seed = gr.Number(
                        value=831001,
                        precision=0,
                        label="Seed",
                    )
                    create_randomize = gr.Button("🎲 Nueva seed")

                gr.Markdown(gpu_status())

        with gr.Accordion(
            "Score ABC de entrada / edición avanzada",
            open=False,
        ):
            create_abc = gr.Textbox(
                label="ABC Score opcional",
                lines=18,
                placeholder=(
                    "Déjalo vacío para que YuE2 componga el score.\n"
                    "Si pegas un ABC, se actualizarán M:, Q: y K:."
                ),
            )

        with gr.Row():
            create_generate = gr.Button(
                "🎵 GENERATE",
                variant="primary",
                scale=3,
            )
            create_unload = gr.Button("Liberar VRAM", scale=1)

        with gr.Row():
            create_audio = gr.Audio(
                label="Resultado",
                type="filepath",
            )
            create_status = gr.Markdown("Listo.")

        with gr.Row():
            create_output_folder = gr.Textbox(
                label="Carpeta de salida",
                interactive=False,
            )
            create_open_folder = gr.Button("📂 Abrir carpeta de salida")

        with gr.Tabs():
            with gr.Tab("🎼 Partitura"):
                create_score_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("ABC"):
                create_generated_abc = gr.Textbox(
                    label="ABC generado / utilizado",
                    lines=24,
                    interactive=True,
                )

        with gr.Accordion("Detalles", open=False):
            create_result_json = gr.JSON(label="result.json")

        create_randomize.click(
            fn=random_seed,
            outputs=create_seed,
        )

        create_generate.click(
            fn=generate_song,
            inputs=[
                create_style,
                create_lyrics,
                create_bpm,
                create_key,
                create_meter,
                create_duration_mode,
                create_target_duration,
                create_cot,
                create_seed,
                create_abc,
            ],
            outputs=[
                create_audio,
                create_score_visual,
                create_generated_abc,
                create_status,
                create_output_folder,
                create_result_json,
            ],
        )

        create_unload.click(
            fn=unload_model,
            outputs=create_status,
        )

        create_open_folder.click(
            fn=open_folder,
            inputs=create_output_folder,
            outputs=create_status,
        )

    # ------------------------------------------------------------------
    # TRANSCRIBE
    # ------------------------------------------------------------------
    with gr.Tab("🎼 Transcribe"):
        gr.Markdown(
            "Convierte audio en partitura editable con SheetSage2. "
            "La GPU se comparte con YuE2, así que YuE2 se libera antes de transcribir."
        )

        with gr.Row():
            with gr.Column(scale=3):
                transcribe_input = gr.Audio(
                    label="Audio a transcribir",
                    type="filepath",
                    sources=["upload"],
                )
            with gr.Column(scale=2):
                transcribe_mode = gr.Radio(
                    choices=[
                        "Completa",
                        "Solo melodía (cover)",
                    ],
                    value="Completa",
                    label="Modo",
                    info=(
                        "Completa conserva acordes. Solo melodía elimina los "
                        "acordes del ABC y es el modo recomendado para covers."
                    ),
                )
                transcribe_render = gr.Checkbox(
                    value=True,
                    label="Generar PDF, SVG, PNG y piano preview",
                )
                transcribe_run = gr.Button(
                    "🎼 TRANSCRIBE",
                    variant="primary",
                )

        transcribe_status = gr.Markdown(
            "Carga un WAV, FLAC, MP3 u otro formato compatible y pulsa TRANSCRIBE."
        )

        with gr.Tabs():
            with gr.Tab("🎼 Partitura"):
                transcribe_score_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("ABC"):
                transcribe_abc = gr.Textbox(
                    label="ABC transcrito",
                    lines=26,
                    interactive=True,
                )
            with gr.Tab("Archivos"):
                with gr.Row():
                    transcribe_pdf = gr.File(
                        label="PDF imprimible",
                        type="filepath",
                    )
                    transcribe_midi = gr.File(
                        label="MIDI",
                        type="filepath",
                        file_count="multiple",
                    )
                with gr.Row():
                    transcribe_svg = gr.File(
                        label="SVG",
                        type="filepath",
                        file_count="multiple",
                    )
                    transcribe_png = gr.File(
                        label="PNG",
                        type="filepath",
                        file_count="multiple",
                    )
            with gr.Tab("Detalles"):
                transcribe_result_json = gr.JSON(
                    label="SheetSage2 result.json"
                )

        transcribe_piano = gr.Audio(
            label="Piano preview",
            type="filepath",
        )

        with gr.Row():
            transcribe_send_create = gr.Button(
                "➡️ Enviar ABC a Create",
                scale=2,
            )
            transcribe_send_edit = gr.Button(
                "➡️ Enviar ABC a Edit Score",
                scale=2,
            )
            transcribe_open_pdf = gr.Button(
                "🖨️ Abrir PDF / imprimir",
                scale=1,
            )
            transcribe_open_folder = gr.Button(
                "📂 Abrir carpeta",
                scale=1,
            )

        transcribe_output_folder = gr.Textbox(
            label="Carpeta de salida",
            interactive=False,
        )

        transcribe_run.click(
            fn=transcribe_audio,
            inputs=[
                transcribe_input,
                transcribe_mode,
                transcribe_render,
            ],
            outputs=[
                transcribe_score_visual,
                transcribe_abc,
                transcribe_piano,
                transcribe_pdf,
                transcribe_svg,
                transcribe_png,
                transcribe_midi,
                transcribe_status,
                transcribe_output_folder,
                transcribe_result_json,
            ],
        )

        transcribe_abc.change(
            fn=build_score_viewer,
            inputs=transcribe_abc,
            outputs=transcribe_score_visual,
            queue=False,
        )

        transcribe_send_create.click(
            fn=send_transcription_to_create,
            inputs=[transcribe_abc, transcribe_mode],
            outputs=[
                create_abc,
                create_cot,
                create_bpm,
                create_key,
                create_meter,
            ],
            queue=False,
        )

        transcribe_open_pdf.click(
            fn=open_file,
            inputs=transcribe_pdf,
            outputs=transcribe_status,
            queue=False,
        )

        transcribe_open_folder.click(
            fn=open_folder,
            inputs=transcribe_output_folder,
            outputs=transcribe_status,
            queue=False,
        )

    # ------------------------------------------------------------------
    # COVER
    # ------------------------------------------------------------------
    with gr.Tab("🎙️ Cover"):
        gr.Markdown(
            "Automatiza **audio → SheetSage2 → ABC → YuE2**. "
            "`full` conserva melodías vocal/instrumental y armonía; `melody` "
            "conserva las melodías vocal e instrumental y deja más libertad al acompañamiento."
        )

        with gr.Row():
            with gr.Column(scale=3):
                cover_input = gr.Audio(
                    label="Audio original",
                    type="filepath",
                    sources=["upload"],
                )
                cover_style = gr.Textbox(
                    label="Style Prompt objetivo",
                    lines=3,
                    placeholder=(
                        "Ej.: nocturnal synthwave post-punk, melodic bass, "
                        "chorus guitars, analog synths..."
                    ),
                )
                cover_lyrics = gr.Textbox(
                    label="Lyrics del cover",
                    lines=16,
                    placeholder="[Verse]\n...\n\n[Chorus]\n...",
                )

            with gr.Column(scale=2):
                cover_preservation = gr.Radio(
                    choices=[
                        "Score completo (full)",
                        "Melodías vocal + instrumental (melody)",
                    ],
                    value="Score completo (full)",
                    label="Preservar",
                    info=(
                        "full: melodías + acordes. melody: melodías vocal e "
                        "instrumental, con acompañamiento más libre."
                    ),
                )
                cover_render = gr.Checkbox(
                    value=True,
                    label="PDF/PNG/SVG + piano preview de la fuente",
                )
                with gr.Row():
                    cover_seed = gr.Number(
                        value=831001,
                        precision=0,
                        label="Seed",
                    )
                    cover_randomize = gr.Button("🎲 Nueva seed")

                gr.Markdown(
                    "**Dos caminos:** usa **AUTO COVER** para hacerlo de una vez, "
                    "o **TRANSCRIBE / REVIEW** para corregir el ABC antes de generar."
                )
                cover_auto = gr.Button(
                    "⚡ AUTO COVER",
                    variant="primary",
                )
                cover_prepare = gr.Button("1️⃣ TRANSCRIBE / REVIEW")
                cover_generate = gr.Button(
                    "2️⃣ GENERATE COVER FROM ABC",
                    interactive=False,
                )

        cover_status = gr.Markdown("Carga el audio y elige el flujo de trabajo.")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Fuente")
                cover_source_audio = gr.Audio(
                    label="Original persistido",
                    type="filepath",
                )
                cover_piano = gr.Audio(
                    label="Piano preview de la transcripción",
                    type="filepath",
                )
            with gr.Column():
                gr.Markdown("### Cover")
                cover_audio = gr.Audio(
                    label="Nueva versión",
                    type="filepath",
                )

        with gr.Tabs():
            with gr.Tab("🎼 Score fuente"):
                cover_source_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("✏️ ABC fuente / Review"):
                cover_abc = gr.Textbox(
                    label="ABC que recibirá YuE2",
                    lines=26,
                    interactive=True,
                )
            with gr.Tab("🖨️ PDF fuente"):
                cover_pdf = gr.File(
                    label="PDF imprimible de la transcripción",
                    type="filepath",
                )
            with gr.Tab("🎼 Score cover"):
                cover_result_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("ABC cover"):
                cover_result_abc = gr.Textbox(
                    label="ABC resultante de YuE2",
                    lines=26,
                    interactive=True,
                )

        with gr.Row():
            cover_open_pdf = gr.Button("🖨️ Abrir PDF fuente / imprimir")
            cover_open_source_folder = gr.Button("📂 Abrir carpeta fuente")
            cover_open_result_folder = gr.Button("📂 Abrir carpeta cover")

        with gr.Accordion("Detalles / procedencia", open=False):
            cover_source_dir = gr.Textbox(
                label="Transcripción fuente",
                interactive=False,
            )
            cover_output_folder = gr.Textbox(
                label="Carpeta del cover",
                interactive=False,
            )
            with gr.Row():
                cover_source_json = gr.JSON(label="SheetSage2 result.json")
                cover_result_json = gr.JSON(label="YuE2 result.json")

        cover_randomize.click(fn=random_seed, outputs=cover_seed)

        cover_prepare.click(
            fn=prepare_cover_source,
            inputs=[cover_input, cover_preservation, cover_render],
            outputs=[
                cover_source_audio,
                cover_source_visual,
                cover_abc,
                cover_piano,
                cover_pdf,
                cover_status,
                cover_source_dir,
                cover_source_json,
                cover_generate,
            ],
        )

        cover_auto.click(
            fn=auto_cover,
            inputs=[
                cover_input,
                cover_preservation,
                cover_render,
                cover_style,
                cover_lyrics,
                cover_seed,
            ],
            outputs=[
                cover_source_audio,
                cover_source_visual,
                cover_abc,
                cover_piano,
                cover_pdf,
                cover_source_dir,
                cover_source_json,
                cover_generate,
                cover_audio,
                cover_result_visual,
                cover_result_abc,
                cover_status,
                cover_output_folder,
                cover_result_json,
            ],
        )

        cover_generate.click(
            fn=generate_cover_from_review,
            inputs=[
                cover_source_audio,
                cover_source_dir,
                cover_abc,
                cover_preservation,
                cover_style,
                cover_lyrics,
                cover_seed,
            ],
            outputs=[
                cover_audio,
                cover_result_visual,
                cover_result_abc,
                cover_status,
                cover_output_folder,
                cover_result_json,
            ],
        )

        cover_abc.change(
            fn=build_score_viewer,
            inputs=cover_abc,
            outputs=cover_source_visual,
            queue=False,
        )

        cover_open_pdf.click(
            fn=open_file,
            inputs=cover_pdf,
            outputs=cover_status,
            queue=False,
        )

        cover_open_source_folder.click(
            fn=open_cover_root,
            inputs=cover_source_dir,
            outputs=cover_status,
            queue=False,
        )

        cover_open_result_folder.click(
            fn=open_folder,
            inputs=cover_output_folder,
            outputs=cover_status,
            queue=False,
        )

    # ------------------------------------------------------------------
    # AGENT EDIT + UTILITIES
    # ------------------------------------------------------------------
    with gr.Tab("🧠 Agent Edit"):
        gr.Markdown(
            "Edición musical acotada sobre un score existente. Las utilidades de "
            "transposición, tempo, reharmonización y forma funcionan sin LLM. "
            "El modo Agent usa un endpoint OpenAI-compatible u Ollama y valida el ABC antes de renderizar."
        )

        with gr.Row():
            agent_source = gr.Dropdown(
                choices=initial_choices,
                value=initial_a,
                label="Generación fuente",
                scale=3,
            )
            agent_load = gr.Button("Cargar", scale=1)
            agent_refresh = gr.Button("↻ Actualizar", scale=1)
            agent_reset = gr.Button("↩ Restaurar fuente", scale=1)

        agent_source_audio = gr.Audio(
            label="Audio fuente",
            type="filepath",
        )
        agent_source_status = gr.Markdown("Selecciona una generación y pulsa Cargar.")

        with gr.Row():
            with gr.Column(scale=3):
                agent_style = gr.Textbox(
                    label="Style Prompt",
                    lines=3,
                )
                agent_lyrics = gr.Textbox(
                    label="Lyrics",
                    lines=13,
                )
                agent_working_abc = gr.Textbox(
                    label="ABC de trabajo",
                    lines=26,
                    interactive=True,
                )
                agent_source_abc = gr.State("")

            with gr.Column(scale=2):
                agent_cot = gr.Radio(
                    choices=["full", "melody"],
                    value="full",
                    label="CoT para render",
                    info="full usa la armonía escrita; melody deja el acompañamiento más libre.",
                )
                with gr.Row():
                    agent_seed = gr.Number(
                        value=831001,
                        precision=0,
                        label="Seed",
                    )
                    agent_randomize = gr.Button("🎲 Nueva seed")

                agent_contract = gr.Radio(
                    choices=[
                        "Exacta: ambas melodías + ritmo",
                        "Exacta: Vocal; instrumental puede cambiar",
                        "Exacta: Instrumental; vocal puede cambiar",
                        "Tema reconocible",
                        "Adaptación libre dentro de límites",
                    ],
                    value="Exacta: ambas melodías + ritmo",
                    label="Contrato de preservación",
                )
                agent_permissions = gr.CheckboxGroup(
                    choices=[
                        "Armonía",
                        "Tempo",
                        "Compás",
                        "Letra",
                        "Forma",
                        "Melodía vocal",
                        "Melodía instrumental",
                        "Style prompt",
                    ],
                    value=["Armonía", "Style prompt"],
                    label="Cambios permitidos",
                    info="El contrato de preservación tiene prioridad sobre estos permisos.",
                )

        agent_score_visual = gr.HTML(value=build_score_viewer(""))

        with gr.Accordion("⚙️ Utilidades deterministas", open=True):
            gr.Markdown(
                "Estas operaciones no requieren agente y generan un manifiesto verificable. "
                "La reharmonización determinista conserva exactamente ambas melodías."
            )
            with gr.Row():
                with gr.Column():
                    agent_transpose = gr.Slider(
                        -12, 12, value=0, step=1,
                        label="Transponer (semitonos)",
                    )
                    agent_transpose_apply = gr.Button("Aplicar transposición")
                with gr.Column():
                    agent_tempo = gr.Slider(
                        40, 220, value=88, step=1,
                        label="Nuevo tempo (BPM)",
                    )
                    agent_tempo_apply = gr.Button("Cambiar tempo")

            with gr.Row():
                agent_reharm = gr.Dropdown(
                    choices=[
                        "Añadir color de séptima (raíces iguales)",
                        "Séptimas diatónicas según tonalidad",
                        "Sustitución tritonal de dominantes 7",
                        "Quitar acordes",
                    ],
                    value="Añadir color de séptima (raíces iguales)",
                    label="Reharmonización rápida",
                    scale=3,
                )
                agent_reharm_apply = gr.Button("Aplicar reharmonización", scale=1)

            with gr.Row():
                agent_section = gr.Dropdown(
                    choices=[],
                    label="Sección",
                    scale=3,
                )
                agent_form_action = gr.Dropdown(
                    choices=["Duplicar", "Eliminar", "Mover arriba", "Mover abajo"],
                    value="Duplicar",
                    label="Acción de forma",
                    scale=2,
                )
                agent_sections_refresh = gr.Button("↻ Secciones", scale=1)
                agent_form_apply = gr.Button("Aplicar forma", scale=1)

            agent_utility_status = gr.Markdown("Sin cambios todavía.")

        with gr.Accordion("🤖 Agent Edit por lenguaje natural", open=True):
            with gr.Row():
                agent_endpoint = gr.Textbox(
                    value=AGENT_DEFAULT_ENDPOINT,
                    label="Endpoint",
                    info="Por defecto: Ollama OpenAI-compatible local.",
                    scale=3,
                )
                agent_model = gr.Dropdown(
                    choices=[],
                    value=AGENT_DEFAULT_MODEL or None,
                    allow_custom_value=True,
                    label="Modelo",
                    scale=2,
                )
            agent_api_key = gr.Textbox(
                value="",
                label="API key (opcional)",
                type="password",
                info="Déjalo vacío para Ollama/local. No se guarda en los artefactos.",
            )
            with gr.Row():
                agent_discover = gr.Button("Buscar modelos")
                agent_test = gr.Button("Probar conexión")
            agent_backend_status = gr.Markdown(
                "Puedes usar un servidor local Ollama o cualquier endpoint OpenAI-compatible."
            )
            agent_instruction = gr.Textbox(
                label="Instrucción musical",
                lines=5,
                placeholder=(
                    "Ej.: Reharmoniza el segundo coro como jazz nocturno, conserva exactamente "
                    "ambas melodías, usa dominantes secundarios solo donde apoyen notas sostenidas y "
                    "mantén tempo, forma y letra."
                ),
            )
            agent_run = gr.Button("🧠 RUN AGENT EDIT", variant="primary")
            agent_rationale = gr.Markdown()

        with gr.Row():
            agent_validate = gr.Button("✓ Validar ABC / contrato")
            agent_render = gr.Button(
                "🎵 RENDER EDIT WITH YuE2",
                variant="primary",
                interactive=False,
            )

        with gr.Accordion("Manifiesto y validación", open=False):
            agent_manifest = gr.JSON(label="edit_manifest")
            agent_validation = gr.JSON(label="validation")
            agent_validation_status = gr.Markdown("Carga una fuente para comenzar.")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Fuente")
                agent_compare_source = gr.Audio(
                    label="Audio fuente",
                    type="filepath",
                )
            with gr.Column():
                gr.Markdown("### Edición")
                agent_result_audio = gr.Audio(
                    label="Audio editado",
                    type="filepath",
                )

        with gr.Tabs():
            with gr.Tab("🎼 Partitura editada"):
                agent_result_visual = gr.HTML(value=build_score_viewer(""))
            with gr.Tab("ABC renderizado"):
                agent_result_abc = gr.Textbox(
                    label="ABC resultante",
                    lines=26,
                    interactive=True,
                )

        with gr.Row():
            agent_output_folder = gr.Textbox(
                label="Carpeta de salida",
                interactive=False,
                scale=3,
            )
            agent_open_folder = gr.Button("📂 Abrir carpeta", scale=1)
        agent_render_status = gr.Markdown("Aún no se ha renderizado una edición.")
        with gr.Accordion("YuE2 result.json", open=False):
            agent_result_json = gr.JSON(label="result.json")

        agent_load.click(
            fn=load_run_for_agent,
            inputs=agent_source,
            outputs=[
                agent_source_audio,
                agent_style,
                agent_lyrics,
                agent_source_abc,
                agent_working_abc,
                agent_score_visual,
                agent_cot,
                agent_seed,
                agent_section,
                agent_source_status,
                agent_manifest,
                agent_rationale,
                agent_render,
            ],
        ).then(
            fn=lambda x: x,
            inputs=agent_source_audio,
            outputs=agent_compare_source,
            queue=False,
        )

        agent_refresh.click(
            fn=refresh_agent_sources,
            outputs=agent_source,
            queue=False,
        )

        agent_reset.click(
            fn=reset_agent_working,
            inputs=agent_source_abc,
            outputs=[
                agent_working_abc,
                agent_score_visual,
                agent_manifest,
                agent_utility_status,
                agent_section,
                agent_render,
            ],
            queue=False,
        )

        agent_randomize.click(fn=random_seed, outputs=agent_seed)

        agent_working_abc.change(
            fn=build_score_viewer,
            inputs=agent_working_abc,
            outputs=agent_score_visual,
            queue=False,
        )

        agent_sections_refresh.click(
            fn=refresh_form_sections,
            inputs=agent_working_abc,
            outputs=agent_section,
            queue=False,
        )

        utility_outputs = [
            agent_working_abc,
            agent_score_visual,
            agent_manifest,
            agent_utility_status,
            agent_section,
        ]

        agent_transpose_apply.click(
            fn=apply_transpose_utility,
            inputs=[agent_working_abc, agent_transpose],
            outputs=utility_outputs,
        )
        agent_tempo_apply.click(
            fn=apply_tempo_utility,
            inputs=[agent_working_abc, agent_tempo],
            outputs=utility_outputs,
        )
        agent_reharm_apply.click(
            fn=apply_reharm_utility,
            inputs=[agent_working_abc, agent_reharm],
            outputs=utility_outputs,
        )
        agent_form_apply.click(
            fn=apply_form_utility,
            inputs=[agent_working_abc, agent_section, agent_form_action],
            outputs=utility_outputs,
        )

        agent_discover.click(
            fn=discover_agent_models,
            inputs=[agent_endpoint, agent_api_key],
            outputs=[agent_model, agent_backend_status],
            queue=False,
        )
        agent_test.click(
            fn=test_agent_backend,
            inputs=[agent_endpoint, agent_model, agent_api_key],
            outputs=agent_backend_status,
        )

        agent_run.click(
            fn=run_agent_edit,
            inputs=[
                agent_endpoint,
                agent_model,
                agent_api_key,
                agent_instruction,
                agent_contract,
                agent_permissions,
                agent_source_abc,
                agent_working_abc,
                agent_style,
                agent_lyrics,
            ],
            outputs=[
                agent_style,
                agent_lyrics,
                agent_working_abc,
                agent_score_visual,
                agent_manifest,
                agent_rationale,
                agent_validation_status,
                agent_render,
                agent_section,
            ],
        )

        agent_validate.click(
            fn=validate_agent_working,
            inputs=[agent_source_abc, agent_working_abc, agent_contract, agent_permissions],
            outputs=[
                agent_score_visual,
                agent_validation,
                agent_validation_status,
                agent_render,
                agent_section,
            ],
            queue=False,
        )

        agent_render.click(
            fn=render_agent_edit,
            inputs=[
                agent_source,
                agent_source_abc,
                agent_contract,
                agent_permissions,
                agent_style,
                agent_lyrics,
                agent_working_abc,
                agent_cot,
                agent_seed,
                agent_manifest,
            ],
            outputs=[
                agent_result_audio,
                agent_result_visual,
                agent_result_abc,
                agent_render_status,
                agent_output_folder,
                agent_result_json,
            ],
        )

        agent_open_folder.click(
            fn=open_folder,
            inputs=agent_output_folder,
            outputs=agent_render_status,
            queue=False,
        )


    # ------------------------------------------------------------------
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
                edit_abc = gr.Textbox(
                    label="ABC editable",
                    lines=24,
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

                edit_generate = gr.Button(
                    "🎛️ REGENERATE FROM SCORE",
                    variant="primary",
                )

        edit_input_visual = gr.HTML(
            value=build_score_viewer("")
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
                edit_load_status,
            ],
        )

        edit_abc.change(
            fn=build_score_viewer,
            inputs=edit_abc,
            outputs=edit_input_visual,
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

    # ------------------------------------------------------------------
    # COMPARE
    # ------------------------------------------------------------------
    with gr.Tab("⚖️ Compare"):
        with gr.Row():
            compare_a = gr.Dropdown(
                choices=initial_choices,
                value=initial_a,
                label="Versión A",
            )
            compare_b = gr.Dropdown(
                choices=initial_choices,
                value=initial_b,
                label="Versión B",
            )
            compare_refresh = gr.Button("↻ Actualizar")
            compare_load = gr.Button(
                "Comparar",
                variant="primary",
            )

        compare_summary = gr.Markdown("Selecciona dos versiones.")

        with gr.Row():
            with gr.Column():
                gr.Markdown("### A")
                compare_audio_a = gr.Audio(
                    label="Audio A",
                    type="filepath",
                )
                compare_meta_a = gr.Markdown()
                compare_score_a = gr.HTML(
                    value=build_score_viewer("")
                )

            with gr.Column():
                gr.Markdown("### B")
                compare_audio_b = gr.Audio(
                    label="Audio B",
                    type="filepath",
                )
                compare_meta_b = gr.Markdown()
                compare_score_b = gr.HTML(
                    value=build_score_viewer("")
                )

        compare_load.click(
            fn=compare_runs,
            inputs=[compare_a, compare_b],
            outputs=[
                compare_audio_a,
                compare_meta_a,
                compare_score_a,
                compare_audio_b,
                compare_meta_b,
                compare_score_b,
                compare_summary,
            ],
        )

    # ------------------------------------------------------------------
    # LIBRARY
    # ------------------------------------------------------------------
    with gr.Tab("📚 Library"):
        with gr.Row():
            library_refresh = gr.Button("↻ Actualizar biblioteca")
            library_open_root = gr.Button("📂 Abrir carpeta de outputs")

        library_table = gr.Dataframe(
            value=library_table_data(),
            headers=[
                "Generación",
                "Fecha",
                "Duración (s)",
                "CoT",
                "Seed",
                "Style",
            ],
            datatype=[
                "str", "str", "str", "str", "str", "str"
            ],
            interactive=False,
            wrap=True,
            label="Generaciones encontradas",
        )

        with gr.Row():
            library_select = gr.Dropdown(
                choices=initial_choices,
                value=initial_a,
                label="Seleccionar generación",
                scale=3,
            )
            library_load = gr.Button("Cargar", scale=1)
            library_open_selected = gr.Button("📂 Abrir carpeta", scale=1)

        library_meta = gr.Markdown()
        library_audio = gr.Audio(
            label="Audio",
            type="filepath",
        )

        with gr.Tabs():
            with gr.Tab("🎼 Partitura"):
                library_score_visual = gr.HTML(
                    value=build_score_viewer("")
                )
            with gr.Tab("ABC"):
                library_score_abc = gr.Textbox(
                    label="ABC",
                    lines=24,
                    interactive=False,
                )

        library_path = gr.Textbox(
            label="Ruta",
            interactive=False,
        )

        library_load.click(
            fn=load_library_run,
            inputs=library_select,
            outputs=[
                library_audio,
                library_score_visual,
                library_score_abc,
                library_meta,
                library_path,
            ],
        )

        library_open_selected.click(
            fn=open_selected_library_folder,
            inputs=library_select,
            outputs=library_meta,
        )

        library_open_root.click(
            fn=open_outputs_root,
            outputs=library_meta,
        )

    # One refresh function updates all selectors that depend on the library.
    refresh_outputs = [
        library_table,
        library_select,
        edit_source,
        compare_a,
        compare_b,
    ]

    library_refresh.click(
        fn=refresh_library,
        outputs=refresh_outputs,
        queue=False,
    )
    edit_refresh.click(
        fn=refresh_library,
        outputs=refresh_outputs,
        queue=False,
    )
    compare_refresh.click(
        fn=refresh_library,
        outputs=refresh_outputs,
        queue=False,
    )


if __name__ == "__main__":
    demo.queue().launch(
        server_name=SERVER_NAME,
        server_port=SERVER_PORT,
        show_error=True,
        allowed_paths=[str(OUTPUTS_ROOT)],
    )
