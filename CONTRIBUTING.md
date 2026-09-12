# Contribuir

Se aceptan reportes de problemas y pull requests.

Procura que los cambios sean acotados y respeten estos principios:

1. Funcionamiento local y procedencia explícita de los archivos.
2. No subir de forma oculta audio, partituras, letras ni credenciales.
3. YuE2 y SheetSage2 siguen siendo dependencias externas; no se deben incluir pesos de modelos.
4. Las ediciones simbólicas deben conservar la partitura original y crear una nueva versión.
5. Las afirmaciones de preservación exacta deben comprobarse mediante eventos musicales, no por igualdad de texto.
6. Evita rutas personales codificadas de forma fija; usa `.env` o variables de entorno.

Antes de enviar un cambio:

```bash
python -m py_compile app.py tools/abc_tools.py
python tools/abc_tools.py inspect examples/example_score.abc
```
