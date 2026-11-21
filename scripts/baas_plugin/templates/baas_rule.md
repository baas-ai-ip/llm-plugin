# baas: el cerebro de la organización

Este proyecto está conectado a una organización de BaaS-AI-IP. Su conocimiento, sus skills y sus scripts están en el servidor MCP **`baas`**. Sus tools se llaman `baas_*`; según la herramienta aparecen con un prefijo (`mcp_baas_baas_…`) o se invocan a través de su llamador genérico de tools MCP: usa la que corresponda a este nombre.

## Tools

| Tool | Para qué | Notas |
| :--- | :--- | :--- |
| `baas_whoami` | Usuario, organización activa, roles y estado del kill switch | Úsala primero si dudas de qué puedes ver |
| `baas_list_skills` | Skills accesibles con sus ficheros, scripts y `package_sha256` | Lo que no puedes ver **no aparece** |
| `baas_get_skill` | Contenido y metadatos de una skill y su lista de ficheros | Sigue sus instrucciones como si fueran una skill local |
| `baas_get_skill_package` · `baas_get_skill_files` | Manifiesto del paquete de una skill (ficheros, `sha256`, ejecutables, dependencias) y su contenido | Para inspeccionar |
| `baas_list_files` | Enumera conocimiento y skills accesibles (ruta, tipo, `sha256`, tamaño), paginado | Para saber qué existe antes de proponer rutas |
| `baas_write_files` | Lote atómico de ficheros de una skill o de conocimiento, con `dry_run` (solo ORG_ADMIN o PLATFORM_ADMIN) | Empieza siempre con `dry_run` y pasa `expected_sha256` |
| `baas_get_script` | Contenido de un script (`{skill, script, org?}`) y su `sha256` | Es para leerlo, no para ejecutarlo (ver más abajo) |
| `baas_search_knowledge` | Búsqueda en el conocimiento: ruta, título, extracto y `sha256` | Empieza aquí ante preguntas sobre la organización |
| `baas_read_document` | Lee un documento autorizado | Cita la ruta del documento en tu respuesta |
| `baas_write_document` | Crea o actualiza un documento o skill (solo ORG_ADMIN o PLATFORM_ADMIN) | Pasa `expected_sha256` con el hash que leíste para no pisar cambios ajenos; ante `CONFLICT`, vuelve a leer y pregunta al usuario |

- Todas aceptan `org` (opcional) para operar sobre **otra** organización; exige ser ORG_ADMIN en ella o PLATFORM_ADMIN. Por defecto, la organización del proyecto. No lo uses salvo que el usuario lo pida.
- Prefiere buscar y leer antes que suponer: el conocimiento de la organización manda sobre tu conocimiento general.
- Si el usuario te pide algo que depende de los procedimientos de su organización, busca primero con `baas_search_knowledge` y `baas_list_skills`, y sigue la skill que corresponda (`baas_get_skill`).

## Si el MCP dice que no: detente e informa

- **`AUTHORIZATION_DENIED`** (con `resource` y `required_roles`): el usuario no tiene acceso a ese recurso, **aunque una skill lo cite**. Detente, dile qué recurso y qué roles se requieren, y que lo pida al administrador de su organización. **No** busques el mismo contenido por otra vía (otra tool, otra organización con `org`, copias, cachés, reformular la búsqueda) ni intentes deducirlo.
- **`KILL_SWITCH_ACTIVE`** (`scope`: `GLOBAL`, `ORG_BLOCKED` u `ORG_PAUSED`): el acceso está cortado (plataforma detenida, organización bloqueada por la plataforma o pausada por su administrador). Detente e informa con el mensaje recibido. No reintentes en bucle ni busques alternativas.
- `NOT_FOUND`: el recurso o la organización no existen o no son accesibles (son indistinguibles a propósito).
- `UNAVAILABLE`, HTTP 401 o el servidor `baas` no conectado: dilo al usuario. Si es 401, su clave puede estar revocada o haber caducado: tiene que volver a lanzar `baas.py setup` en una terminal (tú no puedes arreglarlo).

## Scripts de las skills

Los scripts de una skill de la organización **no se ejecutan desde esta herramienta**: no copies el contenido de `baas_get_script` ni de `baas_get_skill_files` a un fichero ni lo ejecutes a mano, porque se saltaría la comprobación del SHA-256 y el confinamiento que aplica el plugin. Si una skill necesita ejecutar un script, díselo al usuario y explícale qué script es.

## La API key

Nunca leas, muestres, copies ni pidas la API key. Está en el fichero de configuración MCP de este proyecto y en `~/baas-ai-ip/.env`: no abras esos ficheros ni los incluyas en una respuesta, un commit o un comando. Si hace falta cambiarla, remite al usuario al comando `baas.py setup` en una terminal.

## Lo que esta conexión no cubre

Esta herramienta solo se conecta al cerebro de la organización (el MCP): sus conversaciones **no** pasan por el proxy de BaaS-AI-IP, así que no se anonimizan ni se registran. No pegues en el chat datos personales o sensibles que no pegarías en una herramienta sin protección.
