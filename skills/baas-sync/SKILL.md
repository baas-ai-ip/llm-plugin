---
name: baas-sync
description: Sincroniza en este equipo las skills de la organización de BaaS-AI-IP que tus roles autorizan, como un plugin de Claude Code que se invoca /<organización>:<skill> (con sus scripts, plantillas y ficheros de contexto). Úsala solo cuando el usuario lance /baas-sync o pida traer, actualizar o revisar las skills de su organización.
argument-hint: "[--dry-run]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" sync *)
---

# /baas-sync

Trae al equipo las skills de la organización a la que está vinculado este proyecto (`/baas-init`). El trabajo lo hace un script; tú lo ejecutas e interpretas su salida. Es lo mismo que hacen solos los hooks al iniciar y al cerrar la sesión.

## Pasos

1. Ejecuta (añade `--dry-run` si `$ARGUMENTS` lo pide: muestra qué cambiaría sin escribir nada):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" sync --project "${CLAUDE_PROJECT_DIR}"
   ```

2. Resume al usuario, en pocas líneas, lo que dice la salida: skills nuevas (`+`), actualizadas (`~`) y retiradas (`-`), y cada línea `AVISO:` (dependencias a las que no tiene acceso, `settings.json` que no se pudo editar, etc.). **Si cambió algo**, la salida termina pidiendo cerrar y volver a abrir Claude Code: díselo **siempre**, como paso obligatorio (`/reload-plugins` solo vale si el plugin de la organización ya estaba cargado; si es la primera vez o cambió el conjunto, hay que reiniciar), y que las skills se invocan como `/<organización>:<skill>`. Sin cambios, basta con decir que todo está al día.

3. Según el código de salida y la marca entre corchetes:

   | Salida | Qué hacer |
   | :--- | :--- |
   | `0` con `KILL_SWITCH_ACTIVE` | El acceso de la organización está cortado (plataforma detenida, organización bloqueada o pausada): **se han retirado todas** sus skills del equipo. Informa y detente; no busques otra vía. Volverán con la siguiente sincronización cuando se levante. |
   | `0` con «Otra sincronización está en curso» | Hay otra en marcha (p. ej. la del hook de inicio): que repita en unos segundos. |
   | `2` | `[BAAS_KEY_MISSING]` o proyecto sin vincular: sigue las indicaciones de `/baas-init`. |
   | `5` `[BAAS_AUTH_ERROR]` | Di: «Tu conexión ya no vale: escribe /baas-init y aprueba en el navegador.» No pidas ni muestres ninguna clave. |
   | `6` `[BAAS_NETWORK_ERROR]`, `10` `[BAAS_MCP_…]` | Sin red, MCP lento o con error: **no se ha tocado nada**. Sugiere `/baas-status` y reintentar. |
   | `12` `[BAAS_SYNC_INCOMPLETE]` | Alguna skill no se pudo instalar (hash que no coincide, ruta insegura): el resto sí se aplicó y la anterior versión de esa skill sigue como estaba. Muestra qué skill y el motivo y que avise al administrador de la organización. |
   | `13` `[BAAS_SYNC_FOREIGN_DIR]` | Existe `~/.claude/skills/<organización>/` y no lo gestiona BaaS: **no se tocó nada**. Muestra el mensaje; que lo renombre o lo mueva si quiere que el plugin lo gestione. **No** lo borres tú. |

## Qué hace y qué no

- Mantiene `~/.claude/skills/<organización>/` (un plugin de usuario con `.claude-plugin/plugin.json`, `.baas-managed` y `skills/<skill>/…`). **No edites ni borres esos ficheros**: se sobrescriben en cada sincronización.
- Verifica el SHA-256 de cada fichero, confina las rutas y aplica el bit de ejecución solo a lo que la skill declara como ejecutable.
- Instala también, de forma transitiva, las skills de las que dependen las suyas **si el usuario tiene acceso**; si no, lo avisa y no concede nada.
- Deja el plugin apagado a nivel de usuario (`~/.claude/settings.json`) y lo activa solo en este proyecto (`.claude/settings.local.json`, no versionable), para que las skills de una organización no aparezcan en proyectos de otra.
- Quitar un rol, retirar una skill o activar el kill switch la elimina del equipo en la siguiente sincronización. El conocimiento (`knowledge/`) **no** se copia: se sigue sirviendo por el MCP con los roles de cada documento.
- Nunca pidas ni muestres la API key.
