# Agent Edit

Agent Edit permite que un LLM proponga una versión revisada de una partitura ABC
de YuE2 a partir de instrucciones en lenguaje natural.

El flujo es simbólico: el agente modifica la notación, la aplicación la valida y
YuE2 regenera una nueva interpretación a partir de la partitura revisada.

## Ollama

El endpoint predeterminado es la API compatible con OpenAI de Ollama:

```text
http://127.0.0.1:11434/v1/chat/completions
```

Inicia Ollama normalmente, asegúrate de tener un modelo instalado y utiliza las
funciones de búsqueda de modelos y prueba de conexión dentro de Agent Edit.

## Contratos de preservación

La interfaz puede solicitar preservación exacta de ambas melodías, preservación
exacta de Vocal o Instrumental, conservación reconocible del tema o una adaptación
más libre.

Los permisos adicionales controlan armonía, tempo, compás, letra, forma, melodía
Vocal, melodía Instrumental y cambios en el prompt de estilo.

Para contratos exactos, el verificador local compara altura sonora, inicio y
duración, en lugar de depender de igualdad textual.

## Límites

- No es inpainting de forma de onda.
- La misma seed no garantiza audio idéntico fuera de los compases modificados.
- Los contratos cualitativos, como “tema reconocible”, requieren revisión musical humana.
- Los LLM locales varían mucho en su disciplina para producir JSON estricto y ABC válido.
- Escucha siempre el audio regenerado: una partitura simbólicamente válida no garantiza un buen resultado musical.
