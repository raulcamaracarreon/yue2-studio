# Avisos de terceros

YuE2 Studio es un proyecto comunitario independiente que interopera con:

- **YuE2** de m-a-p / multimodal-art-projection — proyecto original: `multimodal-art-projection/YuE`.
- **SheetSage2** de m-a-p — utilizado para transcripción de audio a partitura y renderizado.
- **Gradio** — interfaz web local.
- **abcjs** — cargado desde jsDelivr en tiempo de ejecución para mostrar partituras ABC en el navegador.
- **PyTorch**, **Transformers**, **Hugging Face Hub** y dependencias relacionadas instaladas por el usuario.
- Opcionalmente **Ollama** u otro endpoint local/remoto compatible con la API de OpenAI para Agent Edit.

Este repositorio no incluye pesos de YuE2 ni SheetSage2. Sus licencias y condiciones
se mantienen separadas de la licencia Apache-2.0 de YuE2 Studio.

El verificador de compatibilidad incluido en `tools/abc_tools.py` implementa las
reglas ABC restringidas que requiere esta aplicación. No pretende ser un analizador
ABC de propósito general.
