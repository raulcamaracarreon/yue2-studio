# Guía de instalación en WSL2

Esta guía está orientada a Windows + WSL2 + NVIDIA CUDA. Ajusta las rutas a tu equipo.

## 1. Paquetes base

```bash
sudo apt update
sudo apt install -y git ffmpeg curl
```

Instala Python 3.12 para YuE2 y Python 3.11 para SheetSage2.

## 2. Entorno de YuE2

Crea y activa un entorno Python 3.12, instala la versión CUDA/PyTorch adecuada para
tu sistema siguiendo las instrucciones oficiales de YuE2 y después:

```bash
pip install -r requirements-gui.txt
```

Comprueba CUDA:

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

## 3. Entorno de SheetSage2

Crea un entorno Python 3.11 separado:

```bash
python3.11 -m venv ~/venvs/sheetsage2
source ~/venvs/sheetsage2/bin/activate
pip install huggingface-hub==0.36.0
```

Una combinación CUDA comprobada durante el desarrollo fue:

```bash
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
pip install -r /ruta/a/SheetSage2/requirements.txt
```

Prepara el renderizador:

```bash
cd /ruta/a/SheetSage2
python setup_render.py --with-deps
```

## 4. Configurar YuE2 Studio

```bash
cp .env.example .env
```

Como mínimo, verifica:

```text
SHEETSAGE2_DIR=/ruta/a/SheetSage2
SHEETSAGE2_PYTHON=/home/TU_USUARIO/venvs/sheetsage2/bin/python
```

Para datos pesados en una unidad de Windows puedes usar, por ejemplo:

```text
YUE2_STUDIO_HOME=/mnt/d/YuE2
```

Mantén el trabajo temporal de SheetSage2 en Linux:

```text
SHEETSAGE2_WORK_ROOT=~/.cache/yue2-studio/sheetsage2
```

## 5. Ejecutar

```bash
source ~/venvs/yue2/bin/activate
python app.py
```

Abre `http://127.0.0.1:7860`.

## 6. Ollama opcional

El endpoint predeterminado de Agent Edit es:

```text
http://127.0.0.1:11434/v1/chat/completions
```
