---
name: baas-brain
description: Cómo usar el conocimiento, las skills y los scripts de la organización a través del MCP `baas` de BaaS-AI-IP (tools baas_*) y de las skills que /baas-sync instala en el equipo (/<organización>:<skill>). Úsala siempre que el usuario pida algo que dependa del conocimiento o los procedimientos de su organización, cuando vayas a llamar a una tool `baas_*`, a ejecutar un script de una skill de la organización, o cuando una tool devuelva AUTHORIZATION_DENIED o KILL_SWITCH_ACTIVE.
---

# baas-brain: el cerebro de la organización

Este proyecto está vinculado (con `/baas-init`) a una organización de BaaS-AI-IP. Su conocimiento, sus skills y sus scripts están en el servidor MCP **`baas`**. Las tools aparecen como `mcp__baas__<tool>`; las skills de la organización también como comandos `/mcp__baas__<skill>`. Además, `/baas-sync` copia en este equipo las skills que los roles del usuario autorizan (ver «Skills sincronizadas y skills del MCP»).

## Tools

| Tool | Para qué | Notas |
| :--- | :--- | :--- |
| `baas_whoami` | Usuario, organización activa, roles y estado del kill switch | Úsala primero si dudas de qué puedes ver |
| `baas_list_skills` | Skills accesibles con sus ficheros, scripts y `package_sha256` | Lo que no puedes ver **no aparece** |
| `baas_get_skill` | Contenido y metadatos de una skill y su lista de ficheros | Sigue sus instrucciones como si fueran una skill local |
| `baas_get_skill_package` · `baas_get_skill_files` | Manifiesto del paquete de una skill (ficheros, `sha256`, ejecutables, dependencias) y su contenido | Para inspeccionar; para usar una skill, sincronízala o ejecuta sus scripts con el ejecutor |
| `baas_list_files` | Enumera conocimiento y skills accesibles (ruta, tipo, `sha256`, tamaño), paginado | Para saber qué existe antes de proponer rutas o publicar |
| `baas_write_files` | Lote atómico de ficheros de una skill o de conocimiento, con `dry_run` (solo ORG_ADMIN o PLATFORM_ADMIN) | Para publicar usa `/baas-publish`; si lo llamas tú, empieza siempre con `dry_run` y pasa `expected_sha256` |
| `baas_get_script` | Contenido de un script (`{skill, script, org?}`) y su `sha256` | **No la uses para ejecutar**: usa el ejecutor (abajo) |
| `baas_search_knowledge` | Búsqueda en el conocimiento: ruta, título, extracto y `sha256` | Empieza aquí ante preguntas sobre la organización |
| `baas_read_document` | Lee un documento autorizado | Cita la ruta del documento en tu respuesta |
| `baas_write_document` | Crea o actualiza un documento o skill (solo ORG_ADMIN o PLATFORM_ADMIN) | Pasa `expected_sha256` con el hash que leíste para no pisar cambios ajenos; ante `CONFLICT`, vuelve a leer y pregunta al usuario |

- Todas aceptan `org` (opcional) para operar sobre **otra** organización; exige ser ORG_ADMIN en ella o PLATFORM_ADMIN. Por defecto, la organización del proyecto. No lo uses salvo que el usuario lo pida.
- Prefiere buscar y leer antes que suponer: el conocimiento de la organización manda sobre tu conocimiento general.

## Skills sincronizadas y skills del MCP

Una skill de la organización puede llegarte de dos formas:

| | Skill **sincronizada** | Skill **servida por el MCP** |
| :--- | :--- | :--- |
| Cómo se invoca | `/<organización>:<skill>` (la lista de skills de Claude Code la muestra con ese prefijo) | `/mcp__baas__<skill>` o `baas_get_skill` |
| Dónde está | Copia en `~/.claude/skills/<organización>/`, activa solo en los proyectos vinculados a esa organización | En el servidor; llega como texto |
| Ficheros hermanos | Sí: `scripts/`, plantillas y contexto junto a la skill (`${CLAUDE_SKILL_DIR}`), con carga progresiva | Solo si los pides (`baas_get_skill_files`) o los ejecutas con el ejecutor |
| Se actualiza | Con `/baas-sync` y solos, al iniciar y cerrar la sesión; un cambio se ve al reiniciar Claude Code (`/reload-plugins` no siempre basta) | Siempre la versión actual |

- **Prefiere la sincronizada**: si `/<organización>:<skill>` existe, úsala en lugar de `baas_get_skill`. Si no existe y el usuario la espera, propón `/baas-sync` (y `/baas-status` para ver qué está desfasado).
- Los scripts de una skill sincronizada **ya están en el equipo**, con su bit de ejecución: ejecútalos desde su carpeta (`${CLAUDE_SKILL_DIR}/scripts/…`) como indique la skill; no uses el ejecutor para ellos.
- **No toques** `~/.claude/skills/<organización>/` ni su configuración (se sobrescribe al sincronizar) y no copies skills de la organización a otras carpetas.
- La revocación llega con la siguiente sincronización (quitar un rol, retirar una skill o el kill switch borran la copia). El control inmediato lo siguen dando el MCP y el proxy: la copia local **no concede acceso al conocimiento** (`knowledge/`), que no se copia y se sirve con los roles de cada documento.
- Si una skill depende de otra a la que el usuario no tiene acceso, `/baas-sync` lo avisa y no la instala: no la busques por otra vía.

## Si el MCP dice que no: detente e informa

- **`AUTHORIZATION_DENIED`** (con `resource` y `required_roles`): el usuario no tiene acceso a ese recurso, **aunque una skill lo cite** (también una skill sincronizada). Detente, dile qué recurso y qué roles se requieren, y que lo pida al administrador de su organización. **No** busques el mismo contenido por otra vía (otra tool, otra organización con `org`, copias, cachés, reformular la búsqueda) ni intentes deducirlo.
- **`KILL_SWITCH_ACTIVE`** (`scope`: `GLOBAL`, `ORG_BLOCKED` u `ORG_PAUSED`): el acceso está cortado (plataforma detenida, organización bloqueada por la plataforma o pausada por su administrador). Detente e informa con el mensaje recibido. No reintentes en bucle ni busques alternativas. La siguiente sincronización retira del equipo todas las skills de esa organización.
- `NOT_FOUND`: el recurso o la organización no existen o no son accesibles (son indistinguibles a propósito).
- `UNAVAILABLE`, HTTP 401 o el servidor `baas` no conectado: sugiere `/baas-status` para diagnosticar y, si hace falta, `/baas-init`.

## Ejecutar un script de una skill no sincronizada

Los scripts de una skill que **no** está sincronizada se ejecutan **en este equipo** con el ejecutor del plugin: descarga el paquete **completo** de la skill (`baas_get_skill_package` + `baas_get_skill_files`), comprueba el SHA-256 de cada fichero, lo escribe en un directorio temporal privado, ejecuta el script con su intérprete (el `#!`, `baas.yaml` o la extensión: `.py`, `.sh`, `.js`…) y **borra** todo siempre (también si falla). No ejecuta nada cuyo hash no coincida.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" run-script <ruta-del-script> [--sha256 <hash de baas_get_skill>] [--cwd <directorio>] -- <argumentos del script>
```

- `<ruta-del-script>` es la que devuelven `baas_list_skills` o `baas_get_skill` (`skills/<skill>/scripts/<script>`; vale cualquier ruta del paquete). Pasa `--sha256` si la skill lo lista. El script se ejecuta en el directorio actual (o `--cwd`).
- Nunca copies el contenido de un script a un fichero del proyecto ni lo ejecutes a mano.
- **El código de salida es el del script, íntegro.** Un error del propio plugin se reconoce por una línea `BAAS_ERROR=<código>` en stderr (y su exit code): sin esa línea, el código es del script, también si es 9 o 10. `BAAS_ERROR=SCRIPT_HASH_MISMATCH` (código 9): no se ejecutó; informa al usuario y no lo intentes de otro modo. `BAAS_ERROR=AUTHORIZATION_DENIED`, `KILL_SWITCH_ACTIVE`… (código 10): aplica las reglas de arriba.

## La API key

Nunca leas, muestres ni pidas la API key (`~/baas-ai-ip/.env`, `ANTHROPIC_CUSTOM_HEADERS`, `.claude/settings.local.json`). Si hace falta configurarla, remite a `/baas-init`.
