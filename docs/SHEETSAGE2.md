# Integración con SheetSage2

YuE2 Studio utiliza SheetSage2 para transcribir audio a partitura y su renderizador
para producir PDF/SVG/PNG y una preescucha de piano.

## ¿Por qué un entorno separado?

YuE2 y SheetSage2 requieren pilas de dependencias diferentes. Se recomienda
mantenerlos en entornos virtuales independientes.

Una configuración comprobada usa Python 3.11 para SheetSage2 y Python 3.12 para
YuE2 Studio.

## Advertencia WSL / NTFS

SheetSage2 realiza escrituras atómicas y cambios de permisos. En rutas NTFS
montadas por WSL, como `/mnt/d/...`, `chmod` puede fallar aunque la lectura y
escritura ordinarias funcionen.

Arquitectura recomendada:

```text
modelos/caché/resultados finales: /mnt/<unidad>/...   (opcional)
trabajo temporal de SheetSage2:   ~/.cache/yue2-studio/sheetsage2
```

YuE2 Studio copia los resultados finales desde la ruta temporal Linux hacia la
raíz de salida configurada.

## Renderizado

```bash
cd /ruta/a/SheetSage2
python setup_render.py --with-deps
```

Cuando esta preparación termina correctamente, YuE2 Studio puede solicitar PDF,
SVG, PNG y preescucha de piano.
