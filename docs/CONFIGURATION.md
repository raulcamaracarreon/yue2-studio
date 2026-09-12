# Configuración

YuE2 Studio carga `.env` desde el directorio que contiene `app.py`.
Las variables definidas en el shell tienen prioridad sobre `.env`.

## Rutas

`YUE2_STUDIO_HOME` es la raíz principal de datos. El valor público predeterminado
es `~/YuE2-Studio`.

`YUE2_OUTPUTS_ROOT` cambia únicamente la raíz de los resultados finales.

`SHEETSAGE2_DIR` apunta al directorio descargado de SheetSage2.

`SHEETSAGE2_PYTHON` debe apuntar al ejecutable Python del entorno independiente de SheetSage2.

`SHEETSAGE2_WORK_ROOT` es el directorio temporal para inferencia y renderizado.
En WSL debe permanecer en una ruta Linux nativa, por ejemplo
`~/.cache/yue2-studio/sheetsage2`.

`HF_MODULES_CACHE` usa de forma predeterminada `~/.cache/huggingface/modules`.

`YUE2_ABC_TOOLS` permite sobrescribir la ruta del verificador ABC. De forma
predeterminada se usa `tools/abc_tools.py`. Si la aplicación se coloca dentro del
checkout original de YuE, también puede detectarse
`skills/yue2-music/scripts/abc_tools.py`.

## YuE2

- `YUE2_MODEL_ID` predeterminado: `m-a-p/YuE2-3B`
- `YUE2_VAE_ID` predeterminado: `m-a-p/YuE2-Vae`
- `YUE2_MEMORY_BUDGET_GIB` predeterminado: `12`

## Servidor

- `YUE2_SERVER_NAME=127.0.0.1` mantiene la interfaz accesible solo desde la máquina local.
- `YUE2_SERVER_PORT=7860` controla el puerto de Gradio.

No uses `0.0.0.0` salvo que quieras exponer la aplicación a la red y comprendas
las implicaciones de seguridad.

## Agent Edit

- `YUE2_AGENT_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions`
- `YUE2_AGENT_MODEL=` puede dejarse vacío y seleccionarse desde la interfaz.

No publiques credenciales dentro de `.env`.
