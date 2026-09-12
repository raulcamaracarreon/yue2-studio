# Seguridad

YuE2 Studio está pensado para ejecutarse localmente. De forma predeterminada,
Gradio se enlaza a `127.0.0.1`.

No expongas el servidor a una red no confiable sin controles de acceso adecuados.
No publiques claves de API, tokens de Hugging Face, letras o audio privados,
archivos `.env`, cachés de modelos ni contenido generado sensible.

Agent Edit puede enviar el prompt y la partitura actuales al endpoint configurado.
Si dicho endpoint es remoto, revisa su política de privacidad antes de utilizarlo.
