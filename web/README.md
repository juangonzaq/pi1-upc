# Perforación IA · interfaz web

Interfaz local en Next.js 16 para probar el reconocimiento, la revisión manual y el reentrenamiento. Consume la API de `perfo serve`.

Para levantarla, desde la raíz del proyecto:

```bash
make api        # API en http://127.0.0.1:8000 (última corrida e2e del perfil smoke)
make web        # esta interfaz en http://localhost:3100
```

La guía completa está en la sección «Interfaz web local» del [README principal](../README.md): requisitos, recorrido de prueba, endpoints y problemas frecuentes.

Estructura de la carpeta:

- `app/`: páginas (panel, reconocer, documentos, banco de recortes, modelos).
- `components/`: elementos de interfaz compartidos.
- `lib/`: cliente de la API, tipos y formato.

Si la API no corre en `127.0.0.1:8000`, copie `.env.example` a `.env.local` y ajuste `NEXT_PUBLIC_PERFO_API`.
