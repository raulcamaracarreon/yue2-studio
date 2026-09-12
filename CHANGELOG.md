# Historial de cambios

## 4.0.0 — Score Workspace y Piano Roll

- `Edit Score` incorpora un Score Workspace con tres representaciones sincronizadas: Partitura, Piano Roll y ABC.
- Piano Roll editable para las voces Vocal e Ins.
- Movimiento de notas en tiempo y altura, cambio de duración, creación y eliminación de notas.
- Selección múltiple, cuantización 1/4, 1/8 y 1/16, zoom horizontal y deshacer/rehacer.
- Preescucha mediante Web Audio con reproducción, parada, volumen, selección Vocal/Ins/Ambas y playhead.
- La audición funciona sobre el estado editado antes de regenerar con YuE2.
- `Aplicar al ABC` reconstruye y valida el score antes de actualizar Partitura y ABC.
- La escritura de alteraciones respeta la armadura y el estado local del compás.
- `Edit Score` permite cargar directamente un archivo `score.abc`, además de generaciones de la biblioteca.
- El Piano Roll se concentra deliberadamente en `Edit Score`; Transcribe conserva su piano preview y las demás áreas mantienen sus visores especializados.
## 3.3.4 — control musical simbólico verificado y paquete de publicación

- `Create` obtiene primero el plan ABC antes de generar audio con `cot="full"` y `cot="melody"`.
- La tonalidad solicitada se valida contra el conjunto exacto de 30 tonalidades aceptadas por el validador ABC nativo de YuE2.
- Si YuE2 devuelve el mismo modo en otra tónica, se transpone la partitura completa: armadura, alturas de notas y símbolos de acordes.
- La transposición controlada conserva la escritura correspondiente a la tonalidad objetivo.
- El BPM se fija mediante `Q:`.
- El compás se verifica en lugar de cambiarse mediante una sustitución aislada de `M:`.
- Las discrepancias mayor/menor se detienen explícitamente en lugar de producir un resultado engañoso.
- Se conservan el ABC original del planner y la procedencia de los controles para revisión y depuración.
- Los valores públicos predeterminados son portables y no dependen de una máquina concreta.
- Documentación y lanzadores reconciliados en un único paquete público coherente.

## 3.3.1 — preparación de la primera publicación pública

- Eliminación de rutas personales y dependientes de una máquina concreta.
- Configuración mediante `.env` y variables para host, puerto, modelos, memoria y rutas.
- Ayudantes para apertura de archivos entre WSL y Windows.
- Verificador estructural ABC incluido en `tools/`.
- Documentación, lanzadores, ejemplos, licencias e higiene del repositorio.
- El servidor se enlaza a `127.0.0.1` de forma predeterminada.

## 3.3

- Agent Edit mediante endpoint compatible con OpenAI/Ollama.
- Contratos de preservación y validación simbólica.
- Transposición, tempo, reharmonización y operaciones de forma deterministas.

## 3.2

- Flujo automático audio → ABC → cover.
- Modos de cover `cot="full"` y `cot="melody"`.

## 3.1

- Integración de transcripción con SheetSage2.
- Renderizado de partitura a PDF/SVG/PNG y preescucha de piano.

## 3.0

- Flujo Create, Edit Score, Compare y Library.
