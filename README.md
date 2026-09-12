# YuE2 Studio

**Estación de trabajo comunitaria en Gradio para YuE2 + SheetSage2.**

YuE2 Studio permite generar canciones, transcribir audio a ABC editable, crear
covers, editar partituras, transponer, reharmonizar, modificar la forma musical,
comparar versiones y usar un LLM para realizar ediciones simbólicas antes de
regenerar el audio con YuE2.

> **Proyecto no oficial.** YuE2 Studio no está afiliado ni respaldado por
> m-a-p / multimodal-art-projection. YuE2 y SheetSage2 son proyectos externos y
> conservan sus propias licencias de código y modelos.

## Funciones principales

- **Create** — letra/estilo → generación con YuE2, BPM, seed, CoT, compás verificado y control tonal simbólico.
- **Transcribe** — audio → SheetSage2 → ABC, MIDI/eventos, PDF/SVG/PNG y preescucha de piano.
- **Cover** — audio → transcripción → regeneración con YuE2 usando `cot="full"` o `cot="melody"`.
- **Edit Score** — Score Workspace con Partitura, Piano Roll y ABC sincronizados, preescucha local y regeneración con YuE2.
- **Agent Edit** — edición simbólica mediante lenguaje natural, con contratos de preservación y validación.
- **Utilidades** — transposición, cambios de tempo, reharmonización determinista y operaciones sobre forma/secciones.
- **Compare / Library** — comparación A/B y reapertura de generaciones anteriores.
- **Trabajo local** — pensado para ejecutarse en una GPU NVIDIA local; el flujo principal no requiere un servicio en la nube.

## Score Workspace y Piano Roll

`Edit Score` concentra la edición musical manual en un único espacio de trabajo:

- **Partitura** para lectura visual.
- **Piano Roll** para mover, crear, borrar y redimensionar notas de Vocal e Ins.
- **ABC** como representación simbólica editable y validable.
- Preescucha local mediante Web Audio antes de regenerar con YuE2.
- Carga directa de `score.abc` o apertura de una generación existente desde Library.

Los cambios del Piano Roll se consolidan mediante **Aplicar al ABC**, que reconstruye y valida el score antes de actualizar las demás representaciones.

El Piano Roll no se duplica en todas las pestañas: `Transcribe` mantiene su piano preview y las demás áreas envían el score a `Edit Score` cuando se requiere edición detallada.
## Control musical

Con `cot="full"` y `cot="melody"`, YuE2 Studio obtiene primero el plan ABC y lo
verifica **antes** de realizar la generación de audio.

- Si YuE2 planifica el modo solicitado en otra tónica, Studio transpone la partitura completa: armadura, alturas de notas y símbolos de acordes.
- Se conserva la escritura enarmónica correspondiente a la tonalidad objetivo.
- El BPM se fija mediante el campo ABC `Q:`.
- El compás se verifica. Studio **no** simula un cambio de compás reescribiendo únicamente `M:`.
- Un cambio mayor↔menor no se presenta falsamente como una transposición simple.
- Se guardan el ABC original del planner y la procedencia de los controles aplicados.

El selector de tonalidad expone exactamente las **30 tonalidades mayores y menores
aceptadas por el validador ABC nativo de YuE2**, desde siete bemoles hasta siete sostenidos.

## Configuración probada

El desarrollo y las pruebas de extremo a extremo se realizaron en:

**Windows 10 + WSL2 Ubuntu 24.04 + NVIDIA RTX 3060 12 GB**

Esta es una configuración probada, **no un requisito mínimo garantizado**.
Las recomendaciones oficiales de YuE2 pueden ser superiores y el comportamiento
puede variar en otras GPU o configuraciones.

## Inicio rápido

1. Instala YuE2 en un entorno Python 3.12.
2. Instala SheetSage2 en un **entorno Python 3.11 separado**.
3. Clona este repositorio y copia `.env.example` como `.env`.
4. Ajusta `SHEETSAGE2_DIR` y `SHEETSAGE2_PYTHON` si tus rutas son diferentes.
5. Inicia la aplicación:

```bash
source ~/venvs/yue2/bin/activate
python app.py
```

Abre `http://127.0.0.1:7860`.

Para la instalación completa consulta **[docs/INSTALL_WSL.md](docs/INSTALL_WSL.md)**.

## Nota importante sobre WSL y el sistema de archivos

SheetSage2 usa escrituras atómicas y `chmod`. En WSL, su **directorio temporal de
trabajo debe permanecer en el sistema de archivos Linux**, por ejemplo:

```text
~/.cache/yue2-studio/sheetsage2
```

Los modelos y los resultados finales sí pueden almacenarse en una unidad de Windows montada como `/mnt/<unidad>`.

Consulta **[docs/SHEETSAGE2.md](docs/SHEETSAGE2.md)**.

## Agent Edit

Agent Edit puede comunicarse con Ollama u otro endpoint compatible con la API de OpenAI.
El valor predeterminado es:

```text
http://127.0.0.1:11434/v1/chat/completions
```

El agente modifica **datos simbólicos de la partitura** y YuE2 regenera el audio.
No es inpainting de forma de onda y no garantiza una interpretación idéntica fuera
de los pasajes modificados.

Consulta **[docs/AGENT_EDIT.md](docs/AGENT_EDIT.md)**.

## Configuración

La aplicación lee `.env` desde la raíz del repositorio. Variables principales:

| Variable | Valor predeterminado |
|---|---|
| `YUE2_STUDIO_HOME` | `~/YuE2-Studio` |
| `YUE2_OUTPUTS_ROOT` | `<home>/outputs` |
| `SHEETSAGE2_DIR` | `<home>/models/SheetSage2` |
| `SHEETSAGE2_PYTHON` | `~/venvs/sheetsage2/bin/python` |
| `SHEETSAGE2_WORK_ROOT` | `~/.cache/yue2-studio/sheetsage2` |
| `YUE2_ABC_TOOLS` | `tools/abc_tools.py` |
| `YUE2_MEMORY_BUDGET_GIB` | `12` |
| `YUE2_SERVER_NAME` | `127.0.0.1` |
| `YUE2_SERVER_PORT` | `7860` |
| `YUE2_AGENT_ENDPOINT` | endpoint compatible con OpenAI/Ollama |
| `YUE2_AGENT_MODEL` | vacío / se selecciona en la interfaz |

Más información: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Estructura del repositorio

```text
app.py                  Aplicación principal en Gradio
launchers/              Lanzadores opcionales para WSL/Windows
tools/abc_tools.py       Verificador estructural del dialecto ABC usado por YuE2 Studio
tools/piano_roll_score.py Conversión ABC ↔ eventos del Piano Roll
tools/piano_roll_ui.py    Interfaz y reproducción del Piano Roll
docs/                   Documentación de instalación y uso
examples/               Ejemplo original de ABC
screenshots/            Capturas públicas de la interfaz
```

Los modelos, resultados, cachés, credenciales y archivos generados se excluyen
intencionalmente mediante `.gitignore`.

## Licencia

El código y la documentación de YuE2 Studio se publican bajo **Apache License 2.0**.
Esta licencia aplica únicamente al contenido propio de este repositorio. El código,
los modelos y los pesos de terceros conservan sus licencias correspondientes.

Consulta `LICENSE`, `NOTICE` y `THIRD_PARTY_NOTICES.md`.
