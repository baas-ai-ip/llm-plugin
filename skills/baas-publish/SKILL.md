---
name: baas-publish
description: Guía la publicación de una skill (con sus scripts, plantillas y conocimiento) en el MCP de la organización de BaaS-AI-IP. Pregunta qué ruta subir, qué scripts son dependencia, de qué otras skills depende, de dónde sale el conocimiento y qué roles la verán; muestra el plan, ensaya con dry-run y publica en un solo lote verificado. Úsala solo cuando el usuario lance /baas-publish o pida subir, publicar o centralizar una skill en el MCP.
argument-hint: "[ruta de la skill]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" inspect *) Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" publish * --dry-run) Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" sync *) mcp__baas__baas_whoami mcp__baas__baas_list_skills mcp__baas__baas_get_skill_package mcp__baas__baas_list_files mcp__baas__baas_search_knowledge
---

# /baas-publish

Sube una skill al MCP como un **paquete**: `SKILL.md`, scripts, plantillas y ficheros de contexto, con los roles que la verán. Solo pueden publicar `ORG_ADMIN` y `PLATFORM_ADMIN`. Haz una entrevista corta, **una pregunta cada vez** (con `AskUserQuestion` si está disponible), proponiendo lo que ya has detectado en lugar de preguntar en abstracto. El trabajo mecánico lo hace `baas.py`; tú decides con el usuario qué se sube.

## Si el MCP dice que no: detente e informa

`AUTHORIZATION_DENIED` o `KILL_SWITCH_ACTIVE` (salida `[BAAS_MCP_…]`, código 10, o error de una tool): **detente**, cuenta al usuario qué ha pasado y qué rol o permiso hace falta, y no busques otra vía (otra organización con `--org`, otra tool, reintentos).

## Antes de empezar

Llama a `baas_whoami`. Si `organization.is_admin` no es verdadero, dile que no puede publicar y para. Anota la organización y tus `functional_roles`.

## La entrevista

1. **Ruta.** La del argumento `$ARGUMENTS` o pregúntala: una carpeta con `SKILL.md`. Si es una skill suelta con sus scripts en otra carpeta (p. ej. `.agents/skills/x` y `scripts/`), pregunta dónde están los scripts y el conocimiento.
2. **Análisis.** Ejecuta (solo lectura; añade `--scripts RUTA` y `--knowledge DIR` si ya los conoces) y resume lo importante en pocas líneas:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" inspect <ruta> --json
   ```

3. **Scripts.** Propón `scripts.suggested` (los que cita la skill y los que ellos importan o llaman) y enseña `scripts.other` como «no se subirían»; pregunta por cada duda y por lo que esté en `scripts.missing`. Cada elección es un `--include-scripts RUTA` (fichero o carpeta; viajan dentro del paquete, en `scripts/`).
4. **Rutas, estado y portabilidad.** Explica en claro `path_escapes` con `escapes: true` (rutas calculadas con `__file__`/`BASH_SOURCE` que dejarán de apuntar a lo mismo en el equipo de otro usuario), `mutable_state` (ficheros que los scripts escriben: no pueden viajar en el paquete; sugiere pasar la ruta como argumento) y `nonportable` (rutas absolutas del equipo del autor, `.js` de consola de navegador, binarios nativos). Si hay `rewrites`, enseña un par de ejemplos y pregunta si aplicarlas con `--rewrite-paths` (solo cambian la copia que se sube; el original no se toca). Nunca las apliques sin su sí.
5. **Otras skills.** De `links.skills`, comprueba cuáles están ya publicadas (`baas_list_skills` o `baas_get_skill_package`). Para las que no, ofrece publicarlas antes (repite este flujo) o seguir (el servidor avisa con `DEPENDENCY_MISSING` y no concede nada: quien no tenga acceso a la dependencia no la recibe).
6. **Conocimiento.** Por cada ruta de `links.knowledge`, **busca antes en el MCP** (`baas_list_files` con `prefix`, `baas_search_knowledge`). Si ya existe, no se sube: solo se cita. Si no existe y hay copia local, pregunta qué carpeta o fichero subir (`--knowledge DIR:PREFIJO`, se publica bajo `knowledge/PREFIJO/`) y con qué roles (`--knowledge-roles R1,R2` o `'*'`: un `_acl.yaml` en la carpeta, que vale para todo ese directorio, o la cabecera de un `.md` suelto). Sube lo mínimo: pregunta carpeta a carpeta, no vuelques un directorio entero.
7. **Datos sensibles.** Si `sensitive` trae algo, di qué tipo y en qué fichero (el informe va enmascarado: no pidas ni muestres los valores). Recomienda moverlos a un documento de conocimiento con roles restringidos; decide el administrador. `.env` y claves no se suben salvo `--include-secrets`: no lo uses sin una petición expresa.
8. **Roles.** `baas_whoami` solo da los roles del propio usuario: propón `*` (cualquier miembro de la organización) o los roles concretos que el usuario nombre; el servidor rechaza los inexistentes. Sin roles la skill queda solo para administradores. Se pasan con `--roles R1,R2` o `--roles '*'`.
9. **Plan.** Ensaya con todas las opciones elegidas (escribe `--dry-run` **al final**) y resume ficheros nuevos y cambiados, tamaño total, tráfico estimado, roles, dependencias y avisos. Avisos de tamaño (`LARGE_FILE`, `LARGE_PACKAGE`) no bloquean. Con `--prune` se retiran del servidor los ficheros que ya no están en local (la versión anterior se conserva): dilo. Pregunta explícitamente **«¿Publico?»**.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" publish <ruta> --project "${CLAUDE_PROJECT_DIR}" --roles <R> --include-scripts <S> --knowledge <DIR:PREFIJO> --dry-run
   ```

10. **Publicar y verificar.** Solo con su sí, repite el mismo comando cambiando `--dry-run` por `--yes`. Se escribe en **un lote atómico** y se comprueba cada hash. **Sincroniza enseguida para quien publica**: ejecuta `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baas.py" sync --project "${CLAUDE_PROJECT_DIR}"` (activa el plugin de la organización en este proyecto). Cuéntale el resultado de la publicación y de la sincronización, y que sus compañeros la reciben con `/baas-sync` (se invoca `/<organización>:<skill>`). Dile que ahora debe **cerrar y volver a abrir Claude Code** (obligatorio; no puedes hacerlo tú, y `/reload-plugins` no basta si el plugin es nuevo). Los comandos `/mcp__baas_<skill>` que ya ve son prompts del MCP, no las skills del plugin. Si publica desde otro proyecto, `/baas-sync` hay que ejecutarlo allí para activarlo.

## Si algo falla

| Salida | Qué hacer |
| :--- | :--- |
| `2` `[BAAS_CONFIG_ERROR]` | Falta algo (ruta sin `SKILL.md`, `baas.yaml` inválido, opción mal escrita, proyecto sin `/baas-init`): muestra el mensaje y corrígelo con el usuario. |
| `10` `INVALID_ARGUMENT` | No se ha escrito nada. Lista todos los fallos (roles inexistentes, rutas): corrige y repite desde el plan. |
| `10` `CONFLICT` | Otro administrador cambió esos ficheros mientras tanto. No se ha escrito nada: vuelve a ensayar para recalcular el plan y enséñaselo. |
| `10` `AUTHORIZATION_DENIED` / `KILL_SWITCH_ACTIVE` | Detente e informa (ver arriba). |
| `6` `[BAAS_NETWORK_ERROR]` | No se sabe si el lote llegó a escribirse (es atómico: o está entero o no está). Vuelve a ensayar con `--dry-run`: lo ya publicado sale «sin cambios» y solo se envía lo que falte. |
| `9` `[BAAS_PACKAGE_HASH_MISMATCH]` | El servidor no guarda lo mismo que se subió: informa y no lo reintentes en bucle. |

Nunca leas ni muestres `~/baas-ai-ip/.env` ni la API key. Opciones sueltas de `publish`: `--name`, `--org` (otra organización; exige ser administrador en ella), `--depends-on-skills`, `--exclude PATRÓN`, `--no-default-excludes`, `--json`.
