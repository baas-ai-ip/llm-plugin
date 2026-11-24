# Pruebas de punta a punta

Scripts de `dev/` que recorren una pila **real** de BaaS-AI-IP (la de `deploy/compose.dev.yml`). No se
distribuyen con el plugin.

- [Hito H2 (épica E4)](#hito-h2-épica-e4): `dev/e2e_h2.py`, el plugin de punta a punta.
- [Hito H3 (AI-IP)](#hito-h3-ai-ip): `dev/e2e_h3.py`, anonimización y rehidratación por el proxy.
- [Hito H4 (observación y eliminación de organizaciones)](#hito-h4-observación-y-eliminación-de-organizaciones):
  `dev/e2e_h4.py`, el anonimizador apagado, el interruptor, eliminar una organización, la prueba canario de H4-02
  (los originales no llegan al disco de Redis), qué pasa con los usuarios que se quedan sin ninguna, el límite de
  claves no válidas por IP a través de Caddy (H4-13), el consumo de la plataforma por organización (Fase 2, grupo
  `consumo`), el NER (L2, grupo `ner`, pasos 15.x) y el catálogo de administración de skills y conocimiento (grupo
  `catalogo`, pasos 17.x).
- [`/baas-init` guiado](#baas-init-guiado-conexión-por-aprobación-en-el-navegador): `tests/test_e2e_connect.py`, la
  conexión por aprobación en el navegador sobre la pila simulada.
- [Mediciones de H4](#mediciones-de-h4): `dev/measure_h4.py` y `dev/measure_frozen_redis.py`.
- [E9 (roles funcionales)](#e9-roles-funcionales): `dev/e9_roles.py`, que los roles aíslan el contenido aunque una skill lo cite.

## En la CI

`e2e_h4.py` (todos sus grupos por omisión, `consumo` incluido; el grupo `ner` necesita la pila con el servicio `ner`) y `e9_roles.py` corren en GitHub Actions con el workflow `e2e` del repositorio
`deploy`: levanta la pila con el proveedor falso, descarga `main` de cada repositorio (o la rama que se pida con
`plugin_ref`, `webapp_ref`, `proxy_ref` o `mcp_ref`) y sube un resumen y el diagnóstico. Se lanza a mano, de
momento (`gh workflow run e2e.yml -R baas-ai-ip/deploy`). Cómo funciona, qué necesita y cómo repetirlo en local:
[`deploy/docs/e2e.md`](https://github.com/baas-ai-ip/deploy/blob/main/docs/e2e.md).

Qué implica para estos scripts: se ejecutan con `--inspect docker` (en local, con podman) y con `--json` (el informe
de pasos que lee el resumen del job); `e2e_h4.py` se ejecuta tal cual, sin cambios para la CI. Las medidas
(`measure_*.py`) no corren en la CI: en un runner compartido los tiempos no son fiables.

Desde la Fase 2, los grupos por omisión de `e2e_h4.py` incluyen `ner` (pasos 15.x): la pila de `deploy` de la CI tiene
que llevar el servicio `ner` y `BAAS_NER_URL` en el proxy, o el grupo falla. 15.5 para y vuelve a arrancar `ner` y 15.4
reinicia el proxy (unos 50 s entre los dos). Orden previsto de fusión: `llm-plugin` va el último, cuando `deploy` ya
tiene el servicio.

## Hito H2 (épica E4)

> **Estado**: en verde contra la pila de desarrollo (2026-11-17, `upstream/main` de `webapp`, `mcp-server`,
> `llm-interceptor-proxy` y `deploy`; Claude Code 2.1.325). Tarea L1 del plan del hito H2; diseño §5.4.

`dev/e2e_h2.py` recorre lo que hace un usuario con el plugin contra una pila **real** de BaaS-AI-IP, desde un
`HOME`, un proyecto (`git init`) y un `TMPDIR` temporales y limpios, y sale con `0` si todo pasa, `1` si falla
algún paso y `2` si los argumentos no valen. No se distribuye con el plugin.

### 1. Cómo lanzarla

1. Levanta la pila de `deploy` con el proveedor falso:

   ```bash
   cd ../deploy && ./scripts/dev-up.sh --fake-upstream
   ```

   Puertos, nombre del proyecto de compose y carpeta de los repositorios se cambian con variables de entorno o
   con `deploy/.env` (`env/dev.env.example`): `COMPOSE_PROJECT_NAME`, `BAAS_DEV_HTTP_PORT`,
   `BAAS_DEV_POSTGRES_PORT`, `BAAS_DEV_REDIS_PORT`, `BAAS_SRC_DIR`.

2. Lanza la prueba con la URL de la webapp de esa pila (las del MCP y del proxy salen del *handshake*, como
   en el plugin):

   ```bash
   uv run python dev/e2e_h2.py --app-url http://app.localhost:8080
   ```

| Parámetro | Por defecto | Para qué |
| :--- | :--- | :--- |
| `--app-url` | `$BAAS_E2E_APP_URL` o `http://app.localhost:8080` | Webapp de la pila |
| `--email` | `analista@demo.test` | Usuario de desarrollo; su API key es la determinista del contrato §8 |
| `--password` · `--totp-secret` | Los del contrato §8 | Sesión en la webapp para leer las trazas |
| `--org` | `demo` | Organización que se vincula |
| `--no-claude` | — | No registra el MCP con el CLI `claude` (sin Claude Code instalado) |
| `--trace-timeout` | `30` | Segundos de espera de la traza |
| `--keep` | — | Conserva el `HOME` y el proyecto temporales |
| `--json <fichero>` | — | Informe en JSON (pasos, estado, duración y tiempos de `/baas-init`) |

### 2. Qué comprueba

| Paso | Comprobación |
| :--- | :--- |
| 0 | `GET <app>/healthz` → `200` |
| 1 | `/baas-init` sin interacción: `baas.py set-key --stdin --app-url …` + `baas.py init --org demo`. **Cronometrado** (objetivo < 120 s, sin contar la descarga del plugin). `settings.local.json` (0600) con `ANTHROPIC_BASE_URL` = `proxy_anthropic_url` y `X-Baas-Api-Key`; `baas.local.json` sin la clave; ambos en `.gitignore`; `~/baas-ai-ip/.env` 0600; la clave no aparece en la salida |
| 1b | Servidor MCP `baas` en el ámbito local de `~/.claude.json` (`type: http`, `mcp_url`, `headersHelper`) **sin** la clave. Con `--no-claude`, se omite |
| 2 | `baas.py status`: usuario, organización, roles y «MCP `baas`: accesible» |
| 3 | `baas_search_knowledge` («indicadores de agenda»): aparece `knowledge/operaciones/indicadores-de-agenda.md` y no el documento de `revisor` |
| 4 | `baas_read_document` de ese documento; su `sha256` coincide con el del contenido |
| 5 | `baas_read_document` de `knowledge/calidad/criterios-de-revision.md` → `AUTHORIZATION_DENIED` con `revisor` en `required_roles` (criterio de aceptación 6) |
| 6 | `baas_get_script` (`analisis-de-citas`, `resumen_citas.py`): `path` y `sha256` correctos. `baas.py run-script … --sha256 <hash>` sobre un CSV da la salida esperada; con un `sha256` falso sale con `9` (y una línea `BAAS_ERROR=SCRIPT_HASH_MISMATCH` en stderr) y no se ejecuta; no quedan temporales `baas-script-*`. *(Desde H5 el ejecutor descarga el paquete completo con `baas_get_skill_package` y `baas_get_skill_files`: este paso necesita un MCP con las tools de H5.)* |
| 7a | Sesión en la webapp como el mismo usuario: contraseña y TOTP (RFC 6238 con la biblioteca estándar); si el inicio de sesión ya da la cookie de sesión (`BAAS_DEV_SKIP_TOTP=1`), solo contraseña. Se anotan las conversaciones previas |
| 7b | `POST <ANTHROPIC_BASE_URL>/v1/messages` con las cabeceras de `ANTHROPIC_CUSTOM_HEADERS` (leídas de `.claude/settings.local.json`) y una credencial de proveedor ficticia → `200` con el texto del proveedor falso. Sin `X-Baas-Api-Key` → `401 authentication_error` |
| 8 | `GET /api/v1/conversations?org=demo`: aparece una conversación nueva (o con más turnos) de Anthropic del usuario, con al menos un turno |

Los pasos que dependen de uno fallido se marcan como `SALTADO` (y también cuentan como fallo).

### 3. Resultado (2026-11-17)

Pila `h2-plugin` (`dev-up.sh --fake-upstream`, puertos `48080`/`45432`/`46379`) construida desde `upstream/main`:

| Paso | Estado | Tiempo | Detalle |
| :--- | :---: | ---: | :--- |
| 0. webapp accesible | OK | 0,00 s | |
| 1. `/baas-init` sin interacción | OK | **0,40 s** | `set-key` 0,06 s + `init` 0,33 s (incluye `claude mcp add-json`); objetivo < 120 s |
| 1b. MCP `baas` registrado sin la clave | OK | 0,00 s | |
| 2. `/baas-status` | OK | 0,07 s | `analista@demo.test`, `demo`, USER, `analista`, «MCP `baas`: accesible» |
| 3. `baas_search_knowledge` | OK | 0,01 s | 3 resultados: `indicadores-de-agenda.md`, `skills/analisis-de-citas/SKILL.md`, `protocolo-de-citas.md` |
| 4. `baas_read_document` | OK | 0,01 s | `sha256` verificado |
| 5. lectura denegada (criterio 6) | OK | 0,01 s | `AUTHORIZATION_DENIED` · `required_roles: ["revisor"]` |
| 6. `baas_get_script` + `run-script` | OK | 0,18 s | «cardiologia: 50 citas, 20.0 % no presentados»; `sha256` falso → código 9 |
| 7a. sesión en la webapp | OK | 5,43 s | Contraseña + TOTP (la espera es la de la ventana de 30 s cuando el código anterior ya se usó) |
| 7b. petición por el proxy | OK | 0,01 s | `200`; sin cabecera, `401 authentication_error` |
| 8. traza | OK | 0,28 s | `ANTHROPIC` · `MESSAGES` · `claude-fake` · cliente `baas-e2e-h2/1` · 1 turno · 12/9 *tokens*; visible 0,29 s después de la petición |

El tiempo de `/baas-init` medido es el de los scripts del plugin (lo que ejecuta la skill); no incluye la
descarga del plugin ni el tiempo de respuesta del modelo en Claude Code. También pasa con `--no-claude`.

### 4. Observaciones

- La API de conversaciones devuelve `provider` y `api_flavor` **en mayúsculas** (`ANTHROPIC`, `MESSAGES`); la
  prueba compara sin distinguir mayúsculas.
- La conversación aparece primero con `turn_count = 0` y el turno se registra un instante después; por eso la
  prueba exige al menos un turno.
- Una petición mínima (sin `system` ni herramientas) queda marcada como `auxiliary: true`.
- El TOTP no admite reutilizar un código: dos ejecuciones seguidas en la misma ventana de 30 s hacen que la
  segunda espere a la siguiente (lo resuelve `BAAS_DEV_SKIP_TOTP=1`, tarea W1).

## Hito H3 (AI-IP)

> **Estado**: escrita y probada contra la pila simulada (`dev/fake_h3_stack.py`, 2026-11-17). Falta ejecutarla
> contra la pila real en la integración (C2 del plan): el proxy con AI-IP, la webapp con la organización `privada`
> y el borrado, y el proveedor falso con eco de `deploy` se desarrollan a la vez en otras ramas. Tarea L1 del plan
> del hito H3; diseño §5.4.

`dev/e2e_h3.py` comprueba AI-IP de punta a punta **por HTTP directo al proxy** (sin Claude Code), con la clave
determinista de `analista@privada.test` (contrato §8), en Messages, Chat Completions y Responses, con y sin
*streaming*. Sale con `0` si todo pasa, `1` si falla algún paso (dice cuál y por qué) y `2` si los argumentos no
valen. No se distribuye con el plugin y solo usa la biblioteca estándar.

### 1. Cómo lanzarla

1. Levanta la pila de `deploy` con el proveedor falso con eco (tarea D1), con un nombre de proyecto y puertos
   propios para no pisar `baas-dev`:

   ```bash
   cd ../deploy
   COMPOSE_PROJECT_NAME=h3-plugin BAAS_DEV_HTTP_PORT=48080 BAAS_DEV_POSTGRES_PORT=45432 \
   BAAS_DEV_REDIS_PORT=46379 BAAS_DEV_FAKE_UPSTREAM_PORT=48090 \
   BAAS_COMPOSE_CMD="uvx podman-compose --parallel 1" ./scripts/dev-up.sh --fake-upstream
   ```

2. Lanza la prueba con la webapp y el proveedor falso de esa pila (las URLs del proxy salen del *handshake*):

   ```bash
   uv run python dev/e2e_h3.py --app-url http://app.localhost:48080 --fake-url http://127.0.0.1:48090
   ```

   Con las mismas variables `BAAS_DEV_HTTP_PORT` y `BAAS_DEV_FAKE_UPSTREAM_PORT` exportadas basta con
   `uv run python dev/e2e_h3.py`; contra la pila por defecto (`8080` y `18090`), también.

| Parámetro | Por defecto | Para qué |
| :--- | :--- | :--- |
| `--app-url` | `$BAAS_E2E_APP_URL` o `http://app.localhost:${BAAS_DEV_HTTP_PORT:-8080}` | Webapp de la pila |
| `--fake-url` | `$BAAS_E2E_FAKE_URL` o `http://127.0.0.1:${BAAS_DEV_FAKE_UPSTREAM_PORT:-18090}` | Proveedor falso publicado en el host (`/_debug/requests`) |
| `--proxy-url` | `$BAAS_E2E_PROXY_URL` o las URLs del *handshake* | Cambia solo el origen del proxy y conserva `/org/<slug>/anthropic` y `/org/<slug>/openai/v1` |
| `--org` · `--email` | `privada` · `analista@privada.test` | Organización con AI-IP activo y usuario cuya clave determinista se usa |
| `--admin-email` | `orgadmin@privada.test` | ORG_ADMIN que lee las trazas y borra la conversación |
| `--bypass-org` · `--bypass-email` | `demo` · `analista@demo.test` | Organización con AI-IP apagado y su usuario |
| `--password` · `--totp-secret` | Los del contrato §8 | Sesión en la webapp (con `BAAS_DEV_SKIP_TOTP=1` basta la contraseña) |
| `--only` | `aiip,bypass,borrado` | Partes que se ejecutan (separadas por comas) |
| `--apis` | `messages,chat,responses` | APIs que se prueban |
| `--trace-timeout` | `30` | Segundos de espera de cada traza en la webapp |
| `--purge-wait` | `1` | Segundos entre el borrado y el turno siguiente: `conversation.purge` llega al proxy por Pub/Sub |
| `--settle` | `2` | Segundos antes de comprobar que las conversaciones borradas no reaparecen |
| `--timeout` | `60` | Tiempo máximo de cada petición HTTP |
| `--json <fichero>` | — | Informe en JSON (pasos, estado, duración, detalle, identificador de la ejecución) |

### 2. Qué comprueba

Cada caso envía como último mensaje del usuario `<etiqueta>. DNI 12345678Z, email ana.perez@privada.test,
IBAN ES91 2100 0418 4502 0005 1332.` La etiqueta (`caso-<ejecución>-<grupo>-<api>-<modo>`) solo tiene letras y
guiones, para que ninguna regla la reconozca, y el `model` también es único (`e2e-h3-<ejecución>-…`). Así cada
caso es una conversación nueva (tokens desde `001`), su petición se localiza en `/_debug/requests` y su
conversación en la webapp (`GET /api/v1/conversations?org=privada&q=<ejecución>`).

| Paso | Comprobación |
| :--- | :--- |
| 0 | `GET <app>/healthz` → `200` |
| 1 | `GET <fake>/_debug/requests` → `{"requests": […]}`; `DELETE` lo vacía |
| 2a · 2b | *Handshake* de `analista@privada.test` (`privada`, `ai_ip_enabled: true`) y de `analista@demo.test` (`demo`, `ai_ip_enabled: false`), con las URLs del proxy |
| 3 | Sesión en la webapp como `orgadmin@privada.test` |
| 4.1–4.6 | Texto, en cada API y modo: `200`; ningún `[[` en el cuerpo; el texto reconstruido de **cada copia** (deltas y, en Responses, `output_text.done`, `content_part.done`, `output_item.done` y `response.completed`) trae los tres valores y ningún `[[`; en *streaming*, `Content-Type: text/event-stream`. El proveedor recibió la petición en su ruta (`/v1/messages`, `/v1/chat/completions`, `/v1/responses`) con `[[DNI_001]]`, `[[EMAIL_001]]` e `[[IBAN_001]]` y ninguno de los valores |
| 5.1–5.6 | Lo mismo pidiendo la herramienta (`usa la herramienta echo`, con su definición en `tools`): hay una llamada a `echo` y sus argumentos son un objeto JSON válido con los tres valores y sin `[[` en cada copia (`input`, `input_json_delta`, `tool_calls[].function.arguments`, deltas y copias de `function_call`) |
| 6 | Cada conversación de 4 y 5 aparece en `GET /api/v1/conversations` con `ai_ip_enabled: true` y `verification_status: CLEAN`; su último turno (`GET …/turns/{n}`) tiene `verification.status: CLEAN`, `request_replacements_by_type` y `response_replacements_by_type` con `DNI`, `EMAIL` e `IBAN`, y ningún token sin resolver, huérfano ni coincidencia residual |
| 7.1–7.6 | Bypass con `analista@demo.test`: lo que recibió el proveedor es idéntico a lo que envió el cliente (como JSON, sin confundir `true` con `1`; se informa si además es idéntico byte a byte, lo que depende de cómo guarde el cuerpo el proveedor falso) y la respuesta trae el eco |
| 8.1–8.3 | Borrado, en cada API: primer turno → la conversación aparece en la webapp → `POST /api/v1/conversations/{id}/delete` `{"reason"}` → `200` con `propagated` → `GET` de la conversación → `404` → (tras `--purge-wait`) el turno siguiente con historial (la respuesta anterior del asistente; en Responses, `previous_response_id`) → `403`: `permission_error` con «eliminada» en el mensaje (Anthropic) o `permission_error` · `baas_conversation_deleted` (OpenAI); el proveedor no lo recibió |
| 8.4 | Tras `--settle` segundos, ninguna conversación borrada reaparece (el turno rechazado no emite eventos) |
| 9 | El mismo primer mensaje, sin historial, recibe `200` con los valores restaurados y abre una conversación **nueva** (id distinto del borrado) |
| 10 | Ninguna petición de `/_debug/requests` contiene los valores en claro, salvo las de bypass |

Los pasos que dependen de uno fallido quedan `SALTADO` (y cuentan como fallo). `/_debug/requests` guarda las 50
últimas peticiones; una ejecución completa envía 22 al proveedor.

### 3. Probarla sin la pila: `dev/fake_h3_stack.py`

`dev/fake_h3_stack.py` simula en un solo proceso la webapp, un proxy que anonimiza y rehidrata email, IBAN y DNI
(con los patrones del contrato §11, retención de los tokens partidos entre eventos, lápidas y
`previous_response_id`) y el proveedor falso con eco del plan (D1). No es una implementación de referencia: es un
doble para probar la prueba.

```bash
python3 dev/fake_h3_stack.py                       # imprime las URLs y el comando de la prueba
python3 dev/fake_h3_stack.py --fault leak          # con un fallo inyectado: la prueba sale con 1
python3 dev/fake_h3_stack.py --debug-body text     # el proveedor guarda los cuerpos como texto
```

`tests/test_e2e_h3.py` (con `unittest`; la CI lo ejecuta con `pytest`, y también vale
`python -m unittest discover -s tests -p test_e2e_h3.py`) prueba las utilidades y el recorrido completo: todo en
verde con el cuerpo guardado como objeto o como texto, y un fallo por cada caso que la prueba tiene que detectar:

| Fallo (`--fault`) | Paso que falla |
| :--- | :--- |
| `leak` (valores en claro hacia el proveedor) | 4, 5 y 10 |
| `no-rehydrate` (tokens sin restaurar) | 4 y 5 |
| `responses-done-unrestored` (copias completas de Responses sin restaurar) | 4.6 y 5.6 |
| `bad-tool-json` (valor insertado sin escapar en los argumentos) | 5.2–5.6 |
| `warning` (verificación distinta de `CLEAN`) | 6 |
| `no-trace` (el turno no llega a la webapp) | 6 y 8 |
| `aiip-off` (`privada` sin AI-IP) | 2a (y los que dependen de él, `SALTADO`) |
| `bypass-mutates` (el proxy altera el cuerpo con AI-IP apagado) | 7 |
| `delete-not-blocking` · `delete-wrong-code` (el borrado no bloquea o no da `baas_conversation_deleted`) | 8.1–8.3 |
| `delete-not-removed` (la webapp no borra) | 8.1–8.3 |
| `blocked-emits-events` (el turno rechazado vuelve a crear la conversación) | 8.4 |
| `fresh-blocked` (una conversación nueva con la misma huella se bloquea) | 9 |

### 4. Supuestos sobre los demás componentes

Fijados por el plan y el contrato; conviene confirmarlos al integrar:

- **Proveedor falso (D1)**: el eco es del último mensaje del usuario, también cuando su contenido es una lista de
  partes (`input_text` en Responses); con `usa la herramienta` responde **solo** con la llamada a `echo` (si además
  trae texto, se exige que no tenga `[[`); devuelve un `id` en Responses sin *streaming*; `body` de
  `/_debug/requests` es el objeto JSON o el texto recibido (se admiten los dos); `DELETE` responde `2xx`.
- **Proxy**: no anonimiza `model` ni la etiqueta (solo letras y guiones); con AI-IP apagado reenvía el cuerpo sin
  cambios; una conversación borrada rechaza también la cadena de Responses que parte de una de sus respuestas.
- **Webapp**: `GET /api/v1/conversations` filtra por `q` sobre el modelo y devuelve `model`, `turn_count`,
  `ai_ip_enabled` y `verification_status`; `GET /api/v1/conversations/{id}` devuelve `turns` y
  `GET …/turns/{n}`, `verification`; tras el borrado, `GET /api/v1/conversations/{id}` → `404`.

### 5. Resultado

Pendiente de la integración (C2). Contra la pila simulada, la ejecución completa pasa en menos de un segundo.

## Hito H4 (observación y eliminación de organizaciones)

> **Estado**: escrita y probada **solo contra la pila simulada** (`dev/fake_h4_stack.py`, 2026-11-17). **No se ha
> ejecutado contra la pila real**: la webapp con el interruptor y la eliminación, el proxy con el modo observación, el
> MCP con la cuarentena y `deploy` con el informe del monitor se desarrollan a la vez en otras ramas. El coordinador la
> ejecuta entera sobre `h4-int` en la tarea C2 del plan del hito H4. Tarea L1 del plan; diseño §5.5 y §6.
> El grupo `originales` (la prueba canario de H4-02: dos instancias de Redis) se añadió el 2026-11-18, también probado
> solo contra la pila simulada y, para el inspector de Redis, contra contenedores `redis:7` desechables.
>
> La parte `huerfanos` (pasos 11.x, usuarios que se quedan sin organización al eliminar una) se escribió después, **solo
> contra el contrato** (§6 de `knowledge/02-arquitectura/contratos-fase-1.md`: `delete-preview`, `disable_orphans`,
> `user.disabled`, `USER_DISABLED`, `user.reactivated`) y contra la pila simulada: la webapp que lo implementa se
> desarrolla a la vez. Los supuestos que conviene verificar al ejecutarla de verdad están en el apartado 5.
>
> El grupo `limite` (pasos 14.x, H4-13: claves no válidas frenadas por IP en el proxy y el MCP) se añadió el
> 2026-11-19 y se ejecutó contra una pila real (`h413-int`) con las ramas `feat/h4-13-limite-claves` del proxy y del MCP.
>
> El grupo `ner` (pasos 15.x, Fase 2, L2: el NER sustituye nombres, empresas y direcciones) se añadió el 2026-11-20 y se
> ejecutó contra una pila real (`f2ner`) construida con las ramas sin fusionar de todos los componentes. **Necesita una
> pila con el servicio `ner`** (`BAAS_NER_URL` en el proxy); contra una pila de la Fase 1, `--only` sin `ner`.
>
> El grupo `consumo` (pasos 16.0 a 16.8, Fase 2: consumo de la plataforma por organización) se añadió el 2026-11-21 y se
> ejecutó contra una pila real (`consumo-int`) con las ramas `feat/f2-consumo-*` de `webapp` (backend y frontend),
> `mcp-server` y `deploy` y `main` del proxy. Su contrato es `knowledge/02-arquitectura/contrato-fase-2-consumo.md` (§9).

`dev/e2e_h4.py` comprueba por **HTTP directo** (sin Claude Code) las novedades de H4 de punta a punta, con las
claves deterministas del contrato §8 de `observador@observacion.test`, `admin@baas.test` (PLATFORM_ADMIN),
`orgadmin@demo.test` y `analista@demo.test`. Sale con `0` si todo pasa, `1` si falla algún paso (dice cuál y por qué) y
`2` si los argumentos no valen. No se distribuye con el plugin y solo usa la biblioteca estándar.

### 1. Cómo lanzarla

1. Levanta la pila de `deploy` con el proveedor falso con eco, con un proyecto y puertos propios (la prueba cambia el
   interruptor de `observacion` y crea y elimina una organización: no la lances contra una pila con datos que importen):

   ```bash
   cd ../deploy
   COMPOSE_PROJECT_NAME=h4-plugin BAAS_DEV_HTTP_PORT=48080 BAAS_DEV_POSTGRES_PORT=45432 \
   BAAS_DEV_REDIS_PORT=46379 BAAS_DEV_FAKE_UPSTREAM_PORT=48090 \
   BAAS_COMPOSE_CMD="uvx podman-compose --parallel 1" ./scripts/dev-up.sh --fake-upstream
   ```

2. Lánzala con la webapp y el proveedor falso de esa pila (las URLs del proxy y del MCP salen del *handshake*) y el
   nombre del proyecto de compose, para mirar dentro de sus contenedores:

   ```bash
   COMPOSE_PROJECT_NAME=h4-plugin uv run python dev/e2e_h4.py \
     --app-url http://app.localhost:48080 --fake-url http://127.0.0.1:48090
   ```

   Con `BAAS_DEV_HTTP_PORT` y `BAAS_DEV_FAKE_UPSTREAM_PORT` exportados bastan `COMPOSE_PROJECT_NAME=h4-plugin uv run python
   dev/e2e_h4.py`; contra la pila por defecto (`8080`, `18090` y `baas-dev`), también.

| Parámetro | Por defecto | Para qué |
| :--- | :--- | :--- |
| `--app-url` | `$BAAS_E2E_APP_URL` o `http://app.localhost:${BAAS_DEV_HTTP_PORT:-8080}` | Webapp de la pila |
| `--fake-url` | `$BAAS_E2E_FAKE_URL` o `http://127.0.0.1:${BAAS_DEV_FAKE_UPSTREAM_PORT:-18090}` | Proveedor falso publicado en el host (`/_debug/requests`) |
| `--proxy-url` | `$BAAS_E2E_PROXY_URL` o las URLs del *handshake* | Cambia solo el origen del proxy y conserva `/org/<slug>/anthropic` y `/org/<slug>/openai/v1` |
| `--org` · `--email` | `observacion` · `observador@observacion.test` | Organización con AI-IP activo y anonimizador apagado, y su usuario (USER, dueño de las conversaciones) |
| `--admin-email` | `admin@baas.test` | PLATFORM_ADMIN que cambia el interruptor, crea, bloquea y elimina, y al que se le comprueba que no ve contenido |
| `--orgadmin-email` | `orgadmin@demo.test` | ORG_ADMIN de otra organización: debe recibir `404` al cambiar el interruptor y es el primer miembro de la organización que se elimina |
| `--bypass-org` · `--bypass-email` | `demo` · `analista@demo.test` | Organización con AI-IP apagado |
| `--anon-org` · `--anon-email` | `privada` · `analista@privada.test` | Organización con AI-IP y anonimizador **encendidos** (la de H3) y su usuario: el tercer modo del grupo `originales` |
| `--only` | `observacion,interruptor,eliminar,originales,huerfanos,uso,acl,limite,ner,consumo,catalogo` | Partes que se ejecutan (separadas por comas). `huerfanos` crea sus propias organizaciones y usuarios (`huerf-<ejecución>-…`) y los activa por la API: **necesita `BAAS_DEV_SKIP_TOTP=1`**. `uso` (Fase 4) necesita la pila con la observabilidad de uso y `env/dev/mcp-server.env` de `deploy` (ventana de 5 s, foto de disco cada 30 s). `acl` (H4-05) necesita la pila con la ACL de Redis de `deploy` y `--inspect`. `limite` (H4-13) necesita el proxy y el MCP con el límite de claves no válidas y el contenedor `fake-upstream` (`--fake-upstream`). `consumo` (Fase 2) necesita la webapp con el informe de consumo y los informes globales (T2, T3 y T7: sin `period` en `GET /platform/usage`, 16.0 falla y los demás 16.x quedan `SALTADO`), el MCP con el recuento de skills y áreas en la foto (T4) y la foto de disco cada 30 s; **no** necesita `--inspect`. 16.8 se salta sin NER salvo que se declare con `--ner` (ver más abajo). `ner` (Fase 2) necesita el servicio `ner` y la webapp con las API del NER; 15.4 y 15.5 necesitan `--inspect` (la 15.5 **para y vuelve a arrancar** `ner`: no la lances contra una pila que otros estén usando). `catalogo` (catálogo de skills y conocimiento) necesita la webapp con `/api/v1/orgs/<org>/catalog/…` y el MCP con `/internal/v1` (sin ellos, 17.0 falla y los demás 17.x quedan `SALTADO`); **no** necesita `--inspect` |
| `--apis` | `messages,chat,responses` | APIs que se prueban |
| `--inspect` | `auto` | `podman`, `docker` o `auto` (el primero que vea el proyecto) para mirar el vault, las dos instancias de Redis (claves y disco) y la cuarentena del MCP, y para enviar las peticiones del grupo `limite` desde el contenedor `fake-upstream`; `none` omite esos pasos (9.1b, 9.8, todo el grupo `originales`, 12.4, 12.5, 12.7, 12.8 y 12.9, todos los grupos `acl` y `limite`, y 15.4 y 15.5; el grupo `consumo` no mira dentro de los contenedores y se ejecuta igual), que no se registran (con `--only originales`, `uso`, `acl`, `limite` o `ner` lo dice en una nota) |
| `--compose-project` | `$COMPOSE_PROJECT_NAME` o `baas-dev` | Proyecto de compose de la pila |
| `--mcp-data-dir` | `/data` | `BAAS_DATA_DIR` del contenedor del MCP |
| `--trace-timeout` | `30` | Segundos de espera de cada traza o de lo que se propaga (bloqueo, eliminación, cuarentena) |
| `--flush-wait` | `2` | Segundos que se espera antes de mirar el AOF vivo en 10.4 (con `appendfsync everysec` Redis puede retrasar hasta 2 s escribir lo que ya recibió) |
| `--propagation-wait` | `1.5` | Segundos entre cambiar el anonimizador y el turno siguiente (`ai_ip.changed` llega al proxy por Pub/Sub) |
| `--timeout` | `60` | Tiempo máximo de cada petición HTTP |
| `--usage-timeout` | `90` | Grupos `uso` y `consumo`: espera del uso del MCP (ventana de 5 s), de la foto de disco (cada 30 s; 12.5 y 16.2, que espera dos) y de un servicio que se vuelve a arrancar |
| `--ner` | `$BAAS_E2E_NER` (`1`, `true` o `yes`) o apagado | Grupo `consumo` (16.8): **declara que la pila trae el NER**. Sin ello, 16.8 se salta (`OK` con el detalle `SALTADO …`) si ningún turno trae `verification.l2`; con ello, que no llegue ningún `l2` es un **fallo** (un NER desplegado que dejara de emitirlo sería una regresión, no una pila sin NER). `e2e-ci.sh` y los valores por omisión no lo activan: sin NER, la CI sigue en verde. Se pasa a `e2e-ci.sh` tras `--` o con la variable de entorno |
| `--overhead-n` | `200` | Grupo `uso` (12.10): pares de peticiones (directo y por el proxy) que se miden |
| `--overhead-p95-ms` | `10` | Grupo `uso` (12.10): umbral del p95 del sobrecoste (el objetivo de `fase-1-h4-mediciones.md`); se puede subir en un ejecutor lento de la CI sin tocar el código |
| `--usage-window` | `5` | Grupo `uso`: `BAAS_USAGE_WINDOW_SECONDS` del MCP de la pila (5 en desarrollo); 12.3 espera una ventana más 1 s antes de dar el recuento por cerrado |
| `--auth-fail-burst` | `30` | Grupo `limite`: `BAAS_AUTH_FAIL_BURST` del proxy y del MCP de la pila (claves desconocidas que un cliente puede probar seguidas antes del `429`) |
| `--auth-fail-per-minute` | `10` | Grupo `limite`: `BAAS_AUTH_FAIL_PER_MINUTE` de la pila; con él se calcula cuántas fichas pudieron volver durante una ráfaga lenta y cuánto esperar a que se llene el cubo (`burst × 60 / ritmo` + 1 s, 181 s) si una ejecución anterior lo dejó a medias |
| `--ner-timeout` | `150` | Grupo `ner` (15.5): espera a que `ner` vuelva a estar sano y a que el disyuntor del proxy (30 s abierto, `BAAS_NER_BREAKER_OPEN`) se cierre |
| `--json <fichero>` | — | Informe en JSON (pasos, estado, duración, detalle, identificador de la ejecución) |

### 2. Qué comprueba

Cada caso envía como último mensaje del usuario `<etiqueta>. DNI 12345678Z, email ana.perez@privada.test,
IBAN ES91 2100 0418 4502 0005 1332.` con una etiqueta única de la ejecución (solo letras y guiones) y un `model` único
(`e2e-h4-<ejecución>-…`), como en H3: cada caso es una conversación nueva y se localiza en `/_debug/requests` y en la
webapp (`?q=<ejecución>`).

| Paso | Comprobación |
| :--- | :--- |
| 0 · 1 | `GET <app>/healthz` → `200`; `GET <fake>/_debug/requests` responde y se vacía |
| 2 · 2b · 3 | *Handshake* de `observador@observacion.test` (`observacion`, AI-IP ✔) y de `analista@demo.test` (`demo`, AI-IP ✘); sesiones de `admin@baas.test`, `observador@observacion.test`, `analista@demo.test` y `orgadmin@demo.test` |
| 4 | `GET /api/v1/orgs/observacion` → AI-IP ✔ y `anonymizer_enabled: false`; si estaba encendido (una ejecución anterior que se cortó), se apaga con motivo y se anota |
| 5.1–5.6 | **Anonimizador apagado**, en Messages, Chat Completions y Responses, con y sin *streaming*: `200`; cada copia del texto de la respuesta trae los tres valores, ningún `[[` y el texto enviado (eco completo); el proveedor recibió el DNI, el email y el IBAN **en claro**, ningún token y un cuerpo **idéntico** al que envió el cliente (como JSON; byte a byte si el proveedor guarda texto) |
| 6 | Como dueño: cada caso es una conversación con AI-IP ✔ y `anonymizer_enabled: false` (**la causa**), `verification_status: WARNING`; su turno trae `verification.status: WARNING`, reemplazos de ida y de vuelta vacíos y `residual_matches` con `DNI`, `EMAIL` e `IBAN`, ningún token sin resolver ni huérfano; **control positivo**: el dueño sí lee el texto enviado en claro |
| 7.1 | Como `admin@baas.test` (PLATFORM_ADMIN, no miembro de `observacion`): la lista, el detalle, cada turno y el revelado de cada caso. `content_available: false`, sin `request_sent`, `response_received`, `request_original` ni `response_returned`, sin valores en `replacements`, con las métricas (`verification`); ninguna respuesta contiene los valores, la etiqueta del caso ni el eco; el revelado no responde `200` |
| 7.2 | Lo mismo con **AI-IP apagado** (`demo`): el texto enviado es el original y el PLATFORM_ADMIN tampoco lo ve (el turno no tiene `verification`) |
| 7.3 | **WebSocket** `/api/v1/ws/traces?org=observacion` (cliente mínimo con la biblioteca estándar, con la cookie y `Origin`) como PLATFORM_ADMIN y como dueño durante un turno nuevo: el PLATFORM_ADMIN recibe tramas de esa conversación y ninguna lleva valores, etiqueta, eco ni contenido en los turnos; se anota si el dueño sí recibió el contenido (control positivo) |
| 8.1 | Permisos y validaciones del interruptor (`PUT /api/v1/orgs/observacion/anonymizer`): sin motivo y con un motivo de 2 caracteres → `422 REASON_REQUIRED`; `observador@observacion.test` (USER) → `403`; `orgadmin@demo.test` (ORG_ADMIN de otra) → `404`; en `demo` (AI-IP apagado) → `409 AI_IP_DISABLED`; ninguna de ellas cambia el valor |
| 8.2–8.8 | **El interruptor en los dos sentidos**, por API (sin *streaming*; Responses con `previous_response_id`): la conversación **A** empieza con el anonimizador apagado → `admin@baas.test` lo **enciende** (con `propagated`) → **B**, nueva, sale tokenizada (solo `[[DNI_001]]`, `[[EMAIL_001]]` e `[[IBAN_001]]` hasta el proveedor y valores restaurados para el cliente) y **A**, con historial, **sube a anonimizada en su siguiente turno** (trinquete, H4-08: el proveedor ya no recibe ningún valor, tampoco los del historial) → lo **apaga** → **B sigue tokenizada** y restaurada, **C**, nueva, sale en claro y **A no baja** (8.9: con el historial completo sigue tokenizada) |
| 8.10 | En la webapp: A con el anonimizador ✘ al empezar, `WARNING` (su turno 1) y `protected_from_turn` = 2; C con ✘ y `WARNING`, sin `protected_from_turn`; B con ✔ y `CLEAN` |
| 8.11 | El PLATFORM_ADMIN ve **por turno** la conversación A (H4-08): el turno 1 (salió en claro) sin contenido ni valores (`content_available: false`) y los siguientes (salieron anonimizados) con `content_available: true`, solo con tokens, sin `request_original`, sin valores y sin poder revelarlos |
| 8.12 | El interruptor vuelve a su valor inicial (apagado), también si algún paso falló |
| 9.1 | Como PLATFORM_ADMIN: crear `e2e-<ejecución>`, invitar a `orgadmin@demo.test` como ORG_ADMIN, activar AI-IP; un turno con valores (el anonimizador está encendido por defecto) y un documento escrito por el MCP (`baas_write_document`), para que haya vault y directorio |
| 9.1b | Con el motor de contenedores: el vault tiene filas de esa organización antes de eliminarla (si no, «vacío» después no probaría nada) y se anotan sus conversaciones (`conversation_state` y `vault_entries` de `baas_vault`); se dice cuántas claves de ellas guarda `redis-ephemeral` (la copia caliente del vault; **informativo**: con un solo turno el proxy puede no haberla creado) |
| 9.2 | Condiciones: sin bloquear → `409 ORG_NOT_BLOCKED`; su ORG_ADMIN → `403`; la organización meta (`baas-ai-ip`, sin bloquear) → `403 ORG_PROTECTED`; sin motivo → `422 REASON_REQUIRED`; la organización sigue existiendo |
| 9.3 | Bloquearla (`PUT …/block`): el proxy responde `403 baas_access_blocked` a su ORG_ADMIN; con otro slug de confirmación → `422 SLUG_MISMATCH` |
| 9.4 | `POST /api/v1/orgs/e2e-<ejecución>/delete` `{"reason", "confirm_slug"}` → `200` con `propagated` |
| 9.5 | Webapp: `404` (y `404` la segunda eliminación, sin conversaciones listadas); proxy: `403 baas_access_denied`; MCP: `404`; el *handshake* de su ORG_ADMIN sigue funcionando y ya no la lista (los usuarios no se eliminan) |
| 9.6 | Crear otra vez el mismo slug → `409 SLUG_TAKEN`, el mismo error que un slug en uso |
| 9.7 | `GET /api/v1/audit?action=org.deleted`: la entrada de la eliminación, con el slug y el motivo, sobrevive a la organización |
| 9.8 | Con el motor de contenedores: el vault no tiene filas de la organización (`vault_entries`, `vault_counters` y `conversation_state` en `baas_vault`), **ninguna de las dos instancias de Redis** tiene `baas:policy:org:<slug>` ni `baas:member:*:<slug>`, `redis-ephemeral` no conserva ninguna `baas:conv:<id>:*` de las conversaciones anotadas en 9.1b (H4-02: `org.deleted` borra en cada instancia lo suyo) y el directorio del MCP ya no está en `orgs/` sino en `.quarantine/<slug>-<fecha>`. Si la pila no tiene `redis-ephemeral` (antigua), solo mira `redis` y lo dice en el detalle |

**Grupo `originales` (H4-02, la prueba canario).** Lo que cierra H4-02 es que un valor centinela enviado por el proxy
**no aparezca en ningún fichero de `/data` de ningún Redis**, ni siquiera tras forzar la reescritura del AOF. Se usa el
mismo texto de siempre (`<etiqueta>. DNI 12345678Z, email ana.perez@privada.test, IBAN ES91 2100 0418 4502 0005 1332.`),
cuya etiqueta lleva la cadena `caso-<ejecución>`, única de la ejecución. Todo el grupo necesita el motor de contenedores
y se omite con `--inspect none`. No cambia el estado de la pila de forma persistente: lee ficheros con `grep` y
`find` **dentro** de los contenedores (nada se copia al host), consulta `CONFIG GET`, pide `BGREWRITEAOF` a la
persistente (una reescritura normal del AOF, como la que Redis hace solo al crecer) y solo escribe una clave `baas:e2e:escaner-<ejecución>` que
borra, con caducidad de 300 s por si la limpieza fallara.

| Paso | Comprobación |
| :--- | :--- |
| 10.0 | **Sin residuo de una pila anterior a A′** en la persistente: ninguna clave `baas:conv:*:vault` (copias calientes del vault, con valores originales) ni `baas:conv:*:events` (se buscan con `SCAN`, nunca con `KEYS`) y `baas:traces` ausente o con `XLEN` 0. Una pila que **venía de H4-08** conserva en `redis` esas copias (en una pila de integración, casi un millar), que 10.6 vería como una fuga. Si hay, **falla** con cuántas claves y de qué tipo y con la receta: «residuo de una pila anterior a A′ (H4-02): ejecuta `deploy/scripts/purge-redis-originals.sh` (y vuelve a lanzar la prueba)». Depende de 10.1 y **se ejecuta, junto con 10.1, antes que cualquier otro paso con tráfico** (justo después de 1): así lo que escriban en la persistente los grupos 5 a 9 si el proxy o la webapp siguen con una sola instancia no se confunde con residuo, y lo delatan 10.4 y 10.6 |
| 10.1 | La pila tiene `redis` **y `redis-ephemeral`**. Si no (una pila anterior a H4-02), **falla** con «sin redis-ephemeral: los originales siguen llegando al AOF» y el resto del grupo queda `SALTADO` |
| 10.2 | Un turno por **modo** (bypass en `demo` con `analista@demo.test`, observación en `observacion` con `observador@observacion.test`, AI-IP con anonimizador en `privada` con `analista@privada.test`), **API** (Messages, Chat Completions y Responses) y forma (con y sin *streaming*): 18 conversaciones, todas `200`. Se eligen estas tres organizaciones, que ya existen en la semilla, para no crear ni cambiar nada; la tercera pasa por el vault (la copia caliente con los valores) y las tres por las trazas |
| 10.3 | La webapp **consume** las trazas: cada conversación aparece en la lista de su dueño (si no, las trazas podrían seguir en el stream y el resto no probaría lo que debe) |
| 10.4 | **AOF vivo de la persistente, sin reescribir nada**: `caso-<ejecución>` no está en ningún fichero de `/data` de `redis`, y `redis` escribe en `/data`. Es la comprobación que delata el problema original: la traza se escribió y se borró (la webapp la consume en milisegundos), pero el texto sigue en el fichero hasta que se reescribe |
| 10.5 | **Control positivo del escáner** (autocontenido): escribe `baas:e2e:escaner-<ejecución>` en la persistente con un valor propio, comprueba que el escáner lo **encuentra en el AOF vivo y después de forzar la reescritura** (en la base binaria del AOF, un preámbulo RDB), y **en un `finally`** borra la clave, vuelve a reescribir y comprueba que el valor ya no está. Si el escáner no ve el disco, «ningún original» no probaría nada: falla por eso |
| 10.6 | **Tras `BGREWRITEAOF`** de la persistente (esperando a `aof_rewrite_in_progress:0`): ni los tres valores (el IBAN, también sin espacios) ni `caso-<ejecución>` en ningún fichero. Después de reescribir el AOF es lo que Redis tiene en memoria, así que esto delata lo que sigue vivo (la copia caliente del vault). Redis borra los ficheros viejos un instante después: se repite hasta `min(--trace-timeout, 5)` s |
| 10.7 | **`redis-ephemeral`**: `appendonly no`, `save ""`, `dir /data`, **ningún fichero** en `/data` (sin volumen, `tmpfs`) y ningún centinela en ellos |

Los centinelas se buscan **tal cual y con cualquier mezcla de los escapes de JSON habituales** carácter a carácter
(`\"`, `\\`, `\/`, `\uXXXX` de Python, Go o PHP para todo lo que no sea alfanumérico ASCII), con `grep -rEa` dentro
del contenedor y con los centinelas como **argumentos** (ni el script ni el *shell* del host los interpretan). Límites
conocidos: solo mira `/data` (10.4 y 10.7 comprueban que `dir` es `/data`); un texto largo y repetitivo en el
preámbulo RDB de la base del AOF puede ir comprimido con LZF y no encontrarse literalmente (la primera copia de cada
centinela siempre va sin comprimir, y el control positivo prueba que la base se lee); **10.4 solo vale si 10.5 pasa**
(un escáner ciego daría «no encontrado» en los dos), y por eso 10.6 y 10.7 dependen de 10.5. 10.4 y 10.6 dependen además de 10.0: con residuo no se pronuncian (quedan `SALTADO`), pero 10.5 y 10.7 sí se ejecutan.

**Limpiar el residuo de una pila real.** Al actualizar una pila de H4-08 a A′, la persistente conserva las copias calientes del vault y las trazas pendientes que escribió el proxy antes: `deploy/scripts/purge-redis-originals.sh` las borra (con `--check` solo comprueba, sin borrar nada). Después, el AOF puede seguir teniendo esos originales hasta que se reescriba: el grupo reescribe el AOF en 10.5 y 10.6 y comprueba que ya no están. Si 10.0 vuelve a fallar tras purgar, algo sigue escribiendo originales en la persistente (¿falta `BAAS_REDIS_EPHEMERAL_URL` en el proxy o en la webapp?): lanza `--only originales` para ver qué paso lo delata.

**Usuarios que se quedan sin organización** (parte `huerfanos`; contrato §6). Todo con nombres únicos de la ejecución
(`huerf-<ejecución>-…`): organizaciones `…-o1` a `…-o4` y usuarios `…-a` a `…-g@huerf.test`, creados y activados por la API
como PLATFORM_ADMIN (`POST …/members` → enlace de activación → `POST /api/v1/auth/activation/<token>/password`, que con
`BAAS_DEV_SKIP_TOTP=1` termina con `200 {"next": "DONE"}`) y con una clave personal cada uno (`POST /api/v1/api-keys`).
**O1**: A (USER, solo O1), B (ORG_ADMIN de O1 y USER de O2) y el PLATFORM_ADMIN. **O2**: X (ORG_ADMIN) y B. **O3**: C y E
(E nunca activa la cuenta: `INVITED`). **O4**: F y G (G, `INVITED`). «Clave que vale» = handshake `200`, proxy `200`
(Chat Completions, AI-IP apagado en esas organizaciones) y MCP (`baas_whoami`); «clave rechazada» = handshake `401`, proxy
`401 baas_invalid_api_key` y MCP `401`.

| Paso | Comprobación |
| :--- | :--- |
| 11.1 | Crear O1 a O4; invitar a los siete usuarios (nacen `INVITED` con enlace); activar a A, B, X, C y F; invitar a B a O2 (ya activa: sin enlace) y al PLATFORM_ADMIN a O1; sesión y clave personal de A, B, C y F |
| 11.2 | Las claves valen: A en O1, B en O1 y O2, C en O3 y F en O4 (el handshake de cada uno lista exactamente sus organizaciones) |
| 11.3 | `GET /api/v1/orgs/<O1>/delete-preview` como PLATFORM_ADMIN con O1 **sin bloquear** → `200` con `members: 3` y `orphans` con **solo** A (`id`, `email`, `name`, `status: ACTIVE`, `personal_keys: 1`): ni B (tiene O2) ni el PLATFORM_ADMIN |
| 11.4 | La misma vista previa: A (USER de O1) y B (ORG_ADMIN de O1) → `403`; X (ORG_ADMIN de otra) → `403` o `404`; sin sesión → `401`; organización inexistente → `404`; ninguna respuesta lista a A; la vista previa no cambió nada (O1 existe, A sigue `ACTIVE` y con su sesión) |
| 11.5 | Bloquear O1 y `POST …/delete` con `"disable_orphans": true` → `200` con `propagated` y **`disabled_users: 1`**; O1 → `404` |
| 11.6 | A: no inicia sesión con su contraseña (`401`/`403`, sin cookie de sesión), su sesión de antes está cerrada (`GET /api/v1/me` → `401`), la clave se rechaza (handshake, proxy y MCP) |
| 11.7 | B no cambia: su sesión de antes sigue (`ACTIVE`, solo con O2), inicia sesión, su clave sigue vigente en `GET /api/v1/api-keys` y vale en O2 (handshake lista solo O2, proxy `200`, MCP); el PLATFORM_ADMIN, X, C y F siguen `ACTIVE` |
| 11.8 | `GET /api/v1/audit?action=user.disabled`: una fila de A con `actor` = el PLATFORM_ADMIN, `reason` = el motivo de la eliminación, sin organización, `metadata` `{cause: "org.deleted", org_slug: <O1>, previous_status: "ACTIVE", keys_revoked ≥ 1}` y **sin el email ni el nombre de A en ningún campo** (salvo `target_label`: la webapp lo deriva al leer, ver apartado 5); ninguna fila de B, X, C ni F |
| 11.9 | O3 con `"disable_orphans": false` → `disabled_users: 0`: C sigue `ACTIVE` (sin organización), inicia sesión y su clave vale (handshake `200` sin organizaciones; proxy `403 baas_access_denied` y MCP `404`, no `401`); el enlace de activación de E sigue valiendo; ninguna fila `user.disabled` |
| 11.10 | O4: la vista previa lista a F (`ACTIVE`, 1 clave) y a G (`INVITED`, 0). `POST …/delete` **sin** el campo `disable_orphans` → `disabled_users: 2` (el valor por omisión es `true`): F como A en 11.6; el enlace de G ya no vale ni activa la cuenta; una fila `user.disabled` por cada uno (`previous_status` `ACTIVE` e `INVITED`, `keys_revoked` ≥ 1 y 0), sin email ni nombre |
| 11.11 | X (ORG_ADMIN de O2) invita a A, desactivado, a O2 → `409 USER_DISABLED`: A no figura entre los miembros de O2 y sigue sin poder iniciar sesión |
| 11.12 | El PLATFORM_ADMIN invita a A a O2 → `201`, `member.status: ACTIVE`, sin enlace. A inicia sesión con la contraseña de siempre (solo O2); su clave de antes sigue **revocada** (`GET /api/v1/api-keys`, handshake `401`, proxy `401` y MCP `401` aunque ya sea miembro de O2: solo puede ser por la revocación) y una clave nueva vale; auditoría `user.reactivated` (`actor`, `org_slug: <O2>` —la fila lleva `org_id` de la organización a la que se invita— y `metadata` `{org_slug: <O2>, new_status: "ACTIVE"}`, sin `previous_status`) y `member.invited` en O2 |
| 11.12b | Visibilidad en la auditoría (W10), tras reactivar a A: X (ORG_ADMIN de O2) ve en `GET /api/v1/audit` la fila `user.reactivated` de A (la de 11.12) y **ninguna** fila `user.disabled`, ni en la lista ni filtrando `action=user.disabled`; el PLATFORM_ADMIN ve las dos |
| 11.13 | El PLATFORM_ADMIN invita a G (desactivado y sin activar) a O2 → `201`, `member.status: INVITED` con un enlace **nuevo**; el de antes no vale, el nuevo sí; con él G activa la cuenta, inicia sesión y queda `ACTIVE` en O2; auditoría `user.reactivated` con `metadata` `{org_slug: <O2>, new_status: "INVITED"}` |
| 11.14 | **Limpieza**: bloquear y eliminar O2 (la última) → `disabled_users: 4` (A, B, X y G ya no tienen otra organización); A, desactivado otra vez, ya no inicia sesión y su clave nueva se rechaza. Las cuentas no se eliminan: quedan `huerf-<ejecución>-*` (C `ACTIVE` y E `INVITED` sin organización; A, B, X, F y G `DISABLED`) |

**Observabilidad de uso** (grupo `uso`, Fase 4; `dev/e2e_uso.py`, contrato `contrato-fase-4-uso.md` §11). Cada paso crea
su organización `uso-<ejecución>-<paso>` con usuarios de la semilla como miembros (`orgadmin@demo.test` ORG_ADMIN,
`analista@demo.test` USER; en 12.6 también `orgadmin@privada.test` y `analista@privada.test`) y la elimina al terminar
(sin desactivar a nadie). Las esperas sondean cada segundo, como mucho `--trace-timeout` (trazas) o `--usage-timeout`.

| Paso | Qué comprueba |
| :--- | :--- |
| 12.0 | La pila tiene la Fase 4: `GET /api/v1/orgs/baas-ai-ip/usage/traffic` (PLATFORM_ADMIN) → `200`. Con `404`, falla con «pila sin la Fase 4 (observabilidad de uso): reconstruye con los repos de la Fase 4» y los pasos 12.x quedan `SALTADO` |
| 12.1 | N peticiones (Messages, Messages en *stream*, Chat y Responses) → `usage/traffic`: `requests` N, `tokens_in/out` = los que declara el proveedor, `bytes_in` = la longitud exacta de los cuerpos enviados y `bytes_out` = la de los recibidos; la franja local (`hours`, zona de la organización) da N y la complementaria 0; `group_by` `hour`, `weekday_hour` (168 celdas), `user` y `model` (con su proveedor) |
| 12.2 | `<command-name>/<org>:facturacion</command-name>` (como lo envía Claude Code: el envoltorio en un bloque y la petición en otro) y «usa la herramienta skill `<org>:informes`» (el proveedor falso responde con `tool_use` de `Skill`), cada una en una conversación de **dos turnos correlacionados** (comprobado: `turn_count` 2). En el del comando, el `<command-name>` va en el historial del **cuerpo** que envía el cliente (no en el `request_original` del segundo turno, que es la respuesta anterior y la pregunta nueva); en el de la herramienta, el `request_original` del segundo turno empieza por el `[tool_use: Skill]` y trae el `tool_result` «Launching skill: …» (precondición: se lee `GET …/turns/<n>` como dueño y, si no están, el paso falla) → `INVOKED` 1 cada una (un extractor que contara la herramienta también en `request_original` contaría 2); otro plugin (`otro:cosa`), otra organización (`demo:cosa-<ejecución>`, que tampoco aparece en `demo`: **depende de la organización `demo` de la semilla de desarrollo**), un slug que empieza igual, el listado de skills y `/<org>:x` sin envoltorio → 0 |
| 12.3 | Una sincronización como la del plugin (`baas_list_skills`, `baas_get_skill_package`, `baas_get_skill_files`) → `SYNCED` 1 y todo lo demás 0 |
| 12.4 | Lecturas de `knowledge/<área>/<subcarpeta-centinela>/<secreto-centinela>.md` y de `knowledge/<raíz-centinela>.md` y una búsqueda centinela → el evento `content.usage` (leído en `baas:usage` con la webapp en pausa) y `group_by=area` cuentan el área y la raíz; ningún centinela en el evento, en las tablas de uso, en las respuestas de la API (JSON y CSV de todas las agrupaciones, disco y plataforma) ni en los logs de `webapp-api` y `mcp-server`; `EXISTS baas:usage` = 0 en `redis-ephemeral` y 1 en `redis` (con el grupo `webapp`) |
| 12.5 | Tras escribir una skill con un script y un esquema y un documento, la siguiente foto (`usage/storage`) coincide con `find` sobre `/data/orgs/<slug>` del MCP clasificado como el contrato §4: mismos ficheros y cada categoría y el total con margen del 1 % (si una foto no cuadra, se espera a la siguiente) |
| 12.6 | Sin sesión `401`, USER `403`, ORG_ADMIN de otra organización `404` (tráfico, contenido y disco) y `403` en `/platform/usage`; cada ORG_ADMIN ve solo lo suyo (y `users` de otra organización da 0); el PLATFORM_ADMIN ve las dos y `/platform/usage`; `usage.viewed` solo con el detalle por persona del PLATFORM_ADMIN, visible en W10 para el ORG_ADMIN |
| 12.7 | `podman stop` de `webapp-api` y `mcp-server` (y `podman start` de cada uno aunque el otro falle, esperando a que respondan antes de eliminar la organización): 30 peticiones por el proxy responden `200` con la misma respuesta y una latencia sin degradarse frente a las de antes (×1,5 y +5 ms en la mediana, +10 ms en el p95); «datos hasta» (`usage_source_status`) no avanza; al volver (`podman start`), `data_until` se pone al día y las peticiones y la búsqueda pendientes cuentan una sola vez. «Se queda atrás» se lee en la base con el consumidor parado; lo que importa es la puesta al día exacta. La tolerancia de latencia detecta un bloqueo o un tiempo de espera, no una degradación fina |
| 12.8 | Filas de tráfico, contenido y foto de hace 61 y de hace 59 meses (`psql`); retención a 60 meses (el máximo) por la API y purga (se ejecuta al arrancar `webapp-api`): las de 61 meses se borran y las de 59 no; una conversación borrada sigue contando; eliminar la organización deja 0 filas suyas en el acto; la retención vuelve a su valor (también si el paso falla). **La purga es global**: con 60 meses no borra nada que la retención normal (13) conserve, pero cualquier arranque de `webapp-api` (también en 12.7) purga con la retención vigente las métricas más antiguas de toda la pila |
| 12.9 | Con la webapp en pausa se leen un `content.usage` y un `turn.completed` reales; tras consumirlos, se reinyectan con `XADD` (mismo `event_id`) y no cambian nada; lo mismo con un `storage.snapshot` propio inyectado dos veces |
| 12.10 | 200 pares intercalados de ~2 KB (Messages, bypass) directos al proveedor falso y por el proxy con conexión persistente: el p95 del sobrecoste debe quedar por debajo de 10 ms (objetivo de `fase-1-h4-mediciones.md`). Es un **control grueso**: el proveedor falso añade ~40 ms en muchas respuestas con conexión persistente (Nagle y ACK retardado). Que el proxy **no empeora respecto a la referencia** lo comprueban sus pruebas de sobrecoste en proceso, que no forman parte de este e2e: en `llm-interceptor-proxy`, `GO_IN_CONTAINER=1 scripts/go.sh go test -count=1 -run 'TestBypassOverheadP95\|TestAIIPOverheadP95\|TestObserveOverheadP95' -v ./internal/proxy/` |

**ACL de Redis** (grupo `acl`, H4-05; `dev/e2e_acl.py`, diseño `fase-1-h4-acl-redis-diseno.md` §4 y §8). Con las credenciales
reales de cada usuario (`redis-cli --user <usuario>` dentro del contenedor de Redis, con `$BAAS_REDIS_<USUARIO>_PASSWORD` de su
entorno) se intenta lo que la matriz prohíbe y Redis debe responder `NOPERM`. Necesita `--inspect` y la pila con la ACL de `deploy`;
sin `--inspect` el grupo se omite con una nota. Cada intento va por una conexión llamada `e2e-acl` (`CLIENT SETNAME` antes de la
orden): así `deploy/scripts/e2e-ci.sh` ignora en `ACL LOG` los rechazos que provoca a propósito este grupo y falla con cualquier
otro de `webapp`, `proxy`, `mcp` o `monitor`. Los intentos que Redis acepta (un fallo del paso) dejan escrita la clave de prueba
(`baas:key:e2e-acl`, `baas:member:e2e-acl:demo`…): bórrala como `default`.

| Paso | Qué comprueba |
| :--- | :--- |
| 13.1 | `CLIENT LIST` de las dos instancias (sin contar las conexiones desde `127.0.0.1`/`::1`: el *healthcheck* y el `redis-cli` del e2e entran como `default` dentro del contenedor): la persistente tiene conexiones de `webapp`, `proxy` y `mcp`; la efímera, de `webapp` y `proxy`; ninguna como `default` |
| 13.2 | Como `mcp` en la persistente: `HSET baas:policy:org:demo blocked 0`, `HSET baas:key:e2e-acl user_id x`, `HSET baas:member:e2e-acl:demo governance_role ORG_ADMIN`, `SET baas:platform:status x` y `PUBLISH baas:control x` → todos `NOPERM` |
| 13.3 | Los mismos cinco intentos como `proxy` → `NOPERM` |
| 13.4 | `XADD baas:traces * data x` como `webapp` en la efímera → `NOPERM` (solo el proxy escribe trazas); `ACL GETUSER mcp` como `default` en la efímera → vacío (el MCP no tiene usuario allí) |
| 13.5 | Como `monitor`: `GET baas:platform:status` y `HGETALL baas:policy:org:demo` → `NOPERM` (solo escribe su informe) |

**Límite de claves no válidas por IP** (grupo `limite`, H4-13; contrato §7.5, filas H4-13 de §7.1 y §7.2). Las
peticiones de 14.1 a 14.3 salen **desde dentro del contenedor `fake-upstream`** (`podman exec -i … python3 -c`, con
`urllib`; las claves van por la entrada estándar, no en la línea de órdenes) hacia Caddy por la red de compose
(`http://caddy:80`, con el `Host` de las URLs del *handshake*: `proxy.localhost` o `mcp.localhost`), así que tienen
otra IP que el resto de la prueba y no gastan fichas del equipo. Las claves desconocidas son distintas y bien formadas
(`baas_` y 43 caracteres base64url): el proxy guarda 30 s en caché una clave inválida repetida y no la cuenta, y una mal
formada se rechaza sin consultar. Todas van a la organización sin AI-IP (`--bypass-org`, `demo`) de `--bypass-email`.

| Paso | Qué comprueba |
| :--- | :--- |
| 14.1 | **Proxy**: 31 claves desconocidas distintas a Chat Completions y 5 de más (por si vuelven fichas durante la ráfaga), cada una con un `X-Forwarded-For` aleatorio distinto → las 30 primeras `401 baas_invalid_api_key` y la 31.ª `429` con `Retry-After` (entero ≥ 1), `error.type: requests`, `error.code: baas_auth_rate_limited` y el mensaje con esa espera; una más a Messages → `429` `rate_limit_error`. Prueba que el remitente no elige su cubo con `X-Forwarded-For`: si el cliente saliera de la cabecera, cada clave tendría su cubo y ninguna recibiría `429` |
| 14.2 | **Proxy**: desde el equipo, la clave de `--bypass-email` → `200` (el proxy la resuelve como válida); desde el contenedor, tres desconocidas (la última **debe** recibir `429`: la IP sigue frenada) y después esa clave → `200` contra el proveedor falso, no `429` |
| 14.3 | **MCP**: desde el equipo, `baas_whoami` con la clave de `--bypass-email` (el MCP la resuelve como válida); desde el contenedor, 31 `initialize` (y 5 de más) con claves desconocidas distintas y un `X-Forwarded-For` falso distinto → 30 × `401` y la 31.ª `429` `{"error": "RATE_LIMITED"}` con `Retry-After` y la espera en el mensaje (`Accept-Language: es`); después, la clave ya válida → `200` desde la IP frenada. El MCP no guarda los «no existe»: cada intento cuenta |
| 14.4 | Desde el equipo (otra IP), una clave desconocida → `401` en el proxy (`baas_invalid_api_key`) y en el MCP, sin `429`: el límite es por cliente |

**Si la IP del contenedor no tiene el cubo lleno** (otra ejecución del grupo hace menos de 3 min: 30 fichas a 10 por
minuto), el `429` llega antes de la 31.ª: el paso lo dice (`N × 401 antes del primer 429`), espera a que se llene
(`--auth-fail-burst × 60 / --auth-fail-per-minute` + 1 s, 181 s) y repite la ráfaga **una vez**; la segunda tiene que
dar 30 × `401` y el `429`. Un límite con otra capacidad falla igual tras la espera.

**Si la ráfaga es lenta** (un ejecutor cargado), vuelven fichas mientras dura: el primer `429` vale en cualquier
posición entre la 31.ª y la 31.ª + `floor(elapsed × --auth-fail-per-minute / 60)`, con `elapsed` lo que tardó la
ráfaga medido dentro del contenedor (las 5 claves de más cubren una ráfaga de hasta 36 s), sin esperar ni repetir. Una
`401` de más que no cabe en ese margen falla («sin límite, o el cliente sale del `X-Forwarded-For`»). Si la de
Messages, la última, se llevó una ficha que acababa de volver (recibe `401` y las `401` caben en el margen), se envía
otra justo después, que tiene que recibir el `429`. Mientras espera 14.1 se llena también el cubo del MCP, así que
una segunda ejecución seguida espera una sola vez. Un reinicio del proxy o del MCP vacía sus cubos.

**NER (L2)** (grupo `ner`, Fase 2; `dev/e2e_ner.py`; diseño `fase-2-ner-diseno.md` §8, criterios 1, 2, 4, 5, 8, 9 y 13;
contrato §5.2, §6 y §7.2). Cada paso crea su organización (`ner-<ejecución>-<paso>`, con `--bypass-email` como miembro
USER y el PLATFORM_ADMIN para los ajustes) y la elimina al terminar, así que no depende de la semilla. Una organización
nueva nace con `ner_enabled: true`, `ner_mode: OBSERVE` y `ner_on_failure: DEGRADE`; los pasos que sustituyen la pasan a
`ACTIVE`. Los nombres son inventados y el modelo los detecta de forma fiable («Ana Gómez», «Telefónica», «Valencia»;
«Luis Fernández», «Sevilla»; «Pedro Martínez», «Repsol», «Bilbao»). **Cada caso lleva un sufijo propio** (`(caso …)`):
el mismo primer mensaje del mismo usuario es una sola conversación, también entre Chat y Responses, y la caché del NER
es de la organización; sin el sufijo, un caso reutilizaría el vault y la caché del anterior.

| Paso | Qué comprueba |
| :--- | :--- |
| 15.1 | Organización nueva: NER encendido, `OBSERVE`, `DEGRADE`. `PUT /ner` con AI-IP apagado → `409 AI_IP_DISABLED`; sin motivo → `422 REASON_REQUIRED`; sin campos o con un valor fuera del contrato → `422`; un USER → `403`. Con motivo cambia solo lo que lleva (`mode`, `on_failure`) y la auditoría tiene `org.ner.changed` con motivo, antes y después. Con el anonimizador apagado → `409 ANONYMIZER_DISABLED` sin cambiar nada. Apagar y volver a encender el NER conserva el modo (criterio 13) |
| 15.2 | Con `ACTIVE`, en las tres APIs, con y sin *streaming*: el proveedor recibe `[[PERSON_001]]`, `[[ORG_001]]` y `[[LOCATION_001]]` y ninguno de los tres valores; el cliente recibe los tres restaurados y ningún `[[`; el turno de la webapp lleva los mismos tokens en `request_sent`, `l2.status` `APPLIED` y un reemplazo de cada tipo (criterio 1) |
| 15.3 | Segundo turno (con el historial y la respuesta del modelo tal como el cliente la reenvía), en cada API: `l2.analyzed_bytes` es solo el texto nuevo, `l2.cached_bytes` es todo lo ya visto (sistema y primer mensaje) y `unanalyzed_bytes` 0; lo que recibe el proveedor empieza por lo que recibió en el primer turno, mensaje a mensaje idéntico (se compara la serialización con el orden de claves de la petición: el proveedor falso guarda el cuerpo ya interpretado, no los bytes), sigue con la respuesta del modelo (el eco con sus tokens) y termina con lo nuevo, con tokens que continúan la numeración (`[[PERSON_002]]`, `[[LOCATION_002]]`) (criterio 2) |
| 15.4 | *(necesita `--inspect`)* Tras dos turnos se borran de la efímera **solo** `baas:ner:<org_id>:*` (la caché del NER de la organización: antes tiene que haber claves) y `baas:conv:<id>:vault` (la copia caliente del vault de la conversación); **no** `FLUSHALL`: la efímera de la pila también guarda trazas aún sin consumir y las cachés de otras organizaciones, y el usuario `default` puede borrar con alcance. Después se **reinicia el proxy** (`stop` y `start` del contenedor, espera a `healthy` y a que Caddy lo sirva): el LRU en memoria del proxy (`BAAS_NER_CACHE_LOCAL_MB`) sobrevive al borrado de Redis y, sin reiniciarlo, lo visto saldría de él. Así lo visto ha perdido todas las capas. El tercer turno sale 200 en menos de 1,5 s (una llamada interactiva al NER con margen: si esperara al NER por lo visto, tardaría más), con el historial idéntico al del segundo, los tokens nuevos siguiendo la numeración (el vault se recargó de PostgreSQL), `l2.status` `APPLIED`, `cached_bytes` 0, `unanalyzed_bytes` los mensajes ya vistos (sin esperar al NER) y `analyzed_bytes` lo nuevo **más el texto de `system`** (Messages y Responses): no es un mensaje de la correlación, es «posiblemente nuevo» (diseño §5.3) y se espera en la misma llamada que lo nuevo; el NER vuelve a escribir su caché (criterio 4) |
| 15.5 | *(necesita `--inspect`)* Se **para** el contenedor `ner` (`podman stop`). Con `DEGRADE`: el turno sale (el proveedor lo recibe), con `l2.status` `DEGRADED` y una causa válida y el turno `WARNING`; se repite hasta que el disyuntor se abre (`BREAKER_OPEN`, `BAAS_NER_BREAKER_FAILURES` = 5) y ese turno sale en menos de 1 s. Con `REJECT`: `503`, turno `BLOCKED` con `block_reason` `NER_UNAVAILABLE` y nada en `/_debug/requests`. **Siempre** (también si un paso falla) se vuelve a arrancar `ner`, se espera a que esté `healthy` y a que el proxy sustituya otra vez (con `REJECT`, un `200` es que el disyuntor se cerró y el NER respondió: unos 30 s), hasta `--ner-timeout`; si no vuelve, el paso falla (criterio 5) |
| 15.6 | Observación, en las tres APIs y en tres casos, cada uno en su organización: NER apagado, NER encendido en `OBSERVE` y anonimizador apagado. El proveedor recibe el cuerpo idéntico al enviado y el cliente recibe lo mismo; el turno sale `OK` y `CLEAN` (sin cambio de estado), sin reemplazos ni coincidencias residuales, con `l2.status` `OBSERVED`, `l2.enabled` el de la organización y `l2.observed` 3; `observed_detections` trae, por orden de aparición, `PERSON` «Ana Gómez», `ORG` «Telefónica» y `LOCATION` «Valencia» con su `rule_id` y `count`; la vista del PLATFORM_ADMIN trae los mismos tipos **sin** `original_value` (criterio 8) |
| 15.7 | Prueba en vivo (`POST /api/v1/orgs/{slug}/ai-ip/rules/test`): `ner.status` `OK`; las tres coincidencias con `layer: NER`, `score`, desplazamientos que apuntan al valor (puntos de código) y el token `REPLACED`; con «Telefónica» en la *allowlist* de la organización, esa coincidencia es `ALLOWLISTED` sin token, el texto anonimizado la conserva y las otras dos siguen `REPLACED`; al quitar el término, vuelve a `REPLACED` (criterio 9) |
| 15.8 | W8 (#71): con el NER observando, «Telefónica» sale en `observed_detections`; se añade a la *allowlist* de la organización (`POST /api/v1/orgs/{slug}/ai-ip/allowlist`, el del botón de W8) y, en una conversación nueva, el turno ya no lo observa y sigue observando la persona y la dirección; al terminar se quita el término |

**Consumo de la plataforma** (grupo `consumo`, Fase 2; `dev/e2e_consumo.py`, contrato `contrato-fase-2-consumo.md` §9).
Cada paso con datos crea su organización `consumo-<ejecución>-<paso>` con usuarios de la semilla como miembros y la
elimina al terminar (sin desactivar a nadie); las de 16.5 a 16.8 pasan a zona `UTC` con
`PATCH /api/v1/orgs/{slug}` nada más crearse, porque el informe de plataforma habla en UTC y el de cada organización en
su zona (contrato §11.2: solo coinciden con `UTC`). No mira dentro de los contenedores (`--inspect none` basta), reutiliza
los ayudantes del grupo `uso` (`e2e_uso.UsoGroup`) y envía por el proxy unas 26 peticiones. Los informes se leen **como
PLATFORM_ADMIN** y los de la propia organización, como su ORG_ADMIN, y se repiten hasta `--trace-timeout` s mientras las
cifras no lleguen: lo que no cuadra al final, falla con la diferencia.

| Paso | Qué comprueba |
| :--- | :--- |
| 16.0 | La pila tiene el consumo: `GET /api/v1/platform/usage?month=<mes en curso UTC>` como PLATFORM_ADMIN → `200` con `period` y `data_until_by_source`. Sin ellos falla con «pila sin el informe de consumo de la Fase 2…» y los demás 16.x quedan `SALTADO` |
| 16.1 | **Turnos por modo**: 1 turno en *bypass* (AI-IP apagado), 2 en observación (AI-IP sin anonimizador) y 3 anonimizados, repartidos entre las APIs de `--apis`, cada uno una conversación nueva. La fila de la organización: `requests` 6, `anonymizer.turns_by_mode` con los tres modos (1, 2 y 3; el interruptor se comprueba por turno, como lo ve su dueño en la webapp), `anonymizer.turns` = `requests` = Σ `turns_by_mode`, los mismos `tokens_in/out` y `bytes_in` que el tráfico, las sustituciones de L1 (`replaced_in`, `restored_out`) = la suma de los `*_replacements_by_type` del turno y `verified_clean/warning/error` = el `verification.status` de cada turno |
| 16.2 | **Disco**: una skill con un script y un esquema y un documento por el MCP; con la foto siguiente, `disk.last` = la suma de las cinco categorías facturables de `latest` del informe de disco de la organización (sin el índice), `disk.index_last` = `latest.bytes.index` y `disk.last + disk.index_last` = `latest.total_bytes` (el «Total con índice» de W15, §11.20); `storage_bytes` = `disk.last`; `avg ≤ max`, `last ≤ max`; `skills` 1, `areas` 1 sin `capped`. Luego crece el disco y, con la foto siguiente: `max` = `last`, `avg < max`, `snapshots ≥ 2`. Espera dos fotos (~60 s, `--usage-timeout`) |
| 16.3 | **CSV = JSON** (un rango y un mes, en peticiones seguidas): BOM, `\r\n`, `text/csv`, `Content-Disposition` con `consumo-plataforma-<from>-<to>.csv`, la cabecera exacta de las 46 columnas del §7.9, una fila por organización en el orden del JSON y **sin totales**, y cada celda igual a la del JSON (un nombre que empieza por `=` sale con apóstrofo) |
| 16.4 | **Permisos**: en `GET /platform/usage` **y en las cuatro rutas globales** (`/platform/usage/{traffic,content,storage,anonymizer}`), `403 FORBIDDEN` a un ORG_ADMIN (`--orgadmin-email`), a un USER (`--bypass-email`) y a un USER de otra organización (`--anon-email`) y `401 UNAUTHENTICATED` sin sesión, en JSON, en CSV y con parámetros no válidos (`month` o `group_by=hour`, y `from` > `to`: los permisos van antes que la validación); la respuesta no lleva `orgs`, `totals`, `period` ni `series`. Control: el PLATFORM_ADMIN recibe `200` y `422` con `field` `month` (informe) o `group_by` (globales) |
| 16.5 | **Global = suma de las organizaciones**: dos organizaciones de zona `UTC` con 3 y 5 peticiones (2 y 1 personas activas). Cada fila de `GET /platform/usage/traffic?group_by=org` es el informe de tráfico de su organización (`requests`, `blocked`, `errors`, `tokens_in/out`, `bytes_in/out`, `latency_p50_ms`, `latency_p95_ms`, `ttft_avg_ms` iguales; `active_users` = las personas con peticiones de su `group_by=user`) y su fila de `GET /platform/usage`; los `totals` son la suma de las series (y `orgs` su número); `group_by=org`, `day` y `org_day` dicen los mismos totales; `day` trae cada día del rango; en `org_day`, `top` no pasa de 10 y las parejas de cada una de las nuestras que están en él suman sus peticiones. Compara las **nuestras** y la suma de **todas**: las demás organizaciones de la pila no se tocan |
| 16.6 | **Anonimizador de organización = el de plataforma**: una organización `UTC` con 1 turno en *bypass*, 2 en observación y 3 anonimizados. `totals` de `GET /orgs/{slug}/usage/anonymizer` = el bloque `anonymizer` de su fila de `GET /platform/usage` = su elemento de `GET /platform/usage/anonymizer?group_by=org`, en las 19 columnas (con las diez `l2_*`) y en cada modo de `turns_by_mode` (y los modos son los enviados); sus 2 días y sus 48 horas (`group_by=hour`) suman el total y no falta ninguno. Permisos del §7.2 en JSON y en CSV: PLATFORM_ADMIN (no miembro) y ORG_ADMIN de la organización → `200`, USER → `403`, ORG_ADMIN de otra organización (no miembro) → `404`, sin sesión → `401` |
| 16.7 | **Sin `usage.viewed`**: una organización donde el PLATFORM_ADMIN **solo es USER**. Como PLATFORM_ADMIN: `GET /platform/usage` (JSON y CSV), las cuatro rutas globales con `org`, `day`, `org_day` y CSV y el anonimizador de esa organización (día, hora y CSV): todas `200` y, después, ninguna fila `usage.viewed` nueva ni una agregada que haya crecido en la auditoría de la plataforma (`GET /api/v1/audit?action=usage.viewed`, antes y después) ni ninguna en la de la organización. **Control positivo**: su detalle por persona (`usage/traffic?group_by=user`) sí deja exactamente una fila (`traffic`, `group_by=user`); sin él, «ninguna fila» no probaría nada |
| 16.8 | **L2 con el NER**: 2 turnos anonimizados y 1 en observación con un texto que el NER reconoce (un nombre, una empresa y una ciudad). Saltarlo depende de una **capacidad explícita**: sin `--ner` (ni `BAAS_E2E_NER=1`), si **ningún** turno trae `verification.l2` (la pila no tiene el NER) el paso se **salta**: termina en `OK` con el detalle `SALTADO (la pila no trae el NER): …` (un `SALTADO` de dependencia cuenta como fallo; este no, para que la CI sin NER siga en verde). **Con `--ner`** (o `BAAS_E2E_NER=1`) el mismo caso es un **fallo** que lo dice (el NER no está desplegado, no está encendido por omisión o el proxy ha dejado de emitir `l2`: una regresión que sin la declaración se habría dado por «pila sin NER»). Si algún turno trae `verification.l2` el paso comprueba siempre, con o sin la declaración: las diez columnas `l2_*` del informe de plataforma, de `…/platform/usage/anonymizer` y del anonimizador de la organización suman lo que dice cada turno (`l2_turns` por cada `verification.l2`, `l2_enabled` si `enabled` es `true`, un turno en la columna de su `status`, y los bytes y detecciones que declara), y se llenan: `l2_turns > 0` y `l2_observed_turns` o `l2_applied` > 0 |

Si una ejecución se corta en el grupo `consumo`, puede quedar una organización `consumo-<ejecución>-<paso>` (se bloquea y
elimina igual desde W3). 16.2 depende de la cadencia de fotos de la pila de desarrollo (cada 30 s); 16.8 solo prueba la
rama con NER contra la pila simulada con `ner=True` (`tests/test_e2e_h4.py`): contra la pila real, hasta que el NER
esté en `main`, se salta. **En cuanto la pila de la CI traiga el NER, hay que ejecutar con `--ner`** (o con
`BAAS_E2E_NER=1` en el flujo de `e2e`): así 16.8 deja de poder saltarse y un `l2` que dejara de emitirse falla en vez de
pasar por «pila sin NER».

**Catálogo de skills y conocimiento** (grupo `catalogo`, `dev/e2e_catalogo.py`; contrato del catálogo, §9). Pasos 17.0 a
17.10 (el 13 es de `acl`, el 14 de `limite`, el 15 de `ner` y el 16 de `consumo`). No mira
dentro de los contenedores (no necesita `--inspect`) y se lanza con `--only catalogo`. Cada paso crea su organización
`cat-<ejecución>-<paso>` con un ORG_ADMIN (`--orgadmin-email`) y un USER con el rol funcional `ventas`
(`--bypass-email`), y otro rol, `soporte`, para quitárselo; escribe por MCP (como ORG_ADMIN) las skills `base` (sin
`baas.yaml`, con un script) e `informe` (con `baas.yaml`: depende de `base`, de un documento y de otro que no existe) y el
documento `knowledge/ventas/guia.md`, todos con un centinela en el cuerpo, y la elimina al terminar. Los pasos
17.7, 17.9 y 17.10 se ejecutan en ese orden y los dos últimos guardan en la lista de respuestas vistas sus `200` con
contenido (las exentas de 17.7): por eso 17.7 cuenta solo las exentas que él mismo provoca. Solo hace un
*handshake* por persona (la webapp admite 10 por clave y minuto): la URL del MCP de cada organización es
`<origen>/org/<slug>/mcp`.

| Paso | Qué comprueba |
| :--- | :--- |
| 17.0 | La pila tiene la API: `GET /api/v1/orgs/baas-ai-ip/catalog/skills` (PLATFORM_ADMIN) → `200`. Con `404` falla con «pila sin el catálogo de administración…», con `503` dice que el MCP no ofrece `/internal/v1` o que falta `BAAS_MCP_ADMIN_URL`; los pasos 15.x quedan `SALTADO` |
| 17.1 | El ORG_ADMIN (sin el rol `ventas`) lista las dos skills (`base`: `skill_md`; `informe`: `baas.yaml`) y el documento, con roles, estado y huella; el PLATFORM_ADMIN ve lo mismo |
| 17.2 | Ficha de `informe`: roles y origen, `file_list` con huellas, `depends_on` (`base` existe y `active` con sus roles; el documento existe con título; el otro `exists: false`), `usage` a cero; `base` y el documento dicen «usada por» `informe`; el resumen lleva solo nombres; skill inexistente → `404 NOT_FOUND` |
| 17.3 | En las siete rutas (cuatro `GET` y tres `PUT`): USER → `403 FORBIDDEN`, sin sesión → `401`, ORG_ADMIN de otra organización → `404`; organización inexistente → `404`; lo rechazado no cambia nada |
| 17.4 | `PUT` de roles de `base` (sin `baas.yaml`, `expected_sha256: null`): el USER pasa de leer con `baas_get_skill` a `AUTHORIZATION_DENIED` y deja de verla en `baas_list_skills`; el ORG_ADMIN sigue leyéndola; al devolver el rol vuelve a leer; rol inexistente y `*` con otros → `422 INVALID_ROLES` (`field: roles`). Lo mismo con el documento y `baas_read_document`, con el cuerpo byte a byte intacto |
| 17.5 | Dos `PUT` con el mismo `expected_sha256` (skill, documento y estado): el segundo da `409 CONFLICT` y no pisa nada; `null` sobre un `baas.yaml` que existe, también `409` |
| 17.6 | `GET <mcp>/internal/v1/orgs/<org>/skills` por Caddy → `404` exacto, sin token y con el token interno (`--internal-token`: así se ve que lo bloquea Caddy y no el token); y 18 variantes (`/INTERNAL`, `%69nternal`, `%2F`, `%2e%2e`, `//`, `/./`, `/mcp/../`, rutas a ficheros del equipo) con y sin token: nunca `200` ni datos |
| 17.7 | Las respuestas de la prueba (API del catálogo, las tres escrituras —que deben dar `200`—, errores y Caddy) no contienen los centinelas del cuerpo de `SKILL.md`, del script ni del documento, ni claves de contenido (`content`, `body`, `text`, `excerpt`, `data`, `chunks`), **salvo las `200` de las dos rutas de contenido**: la de ficheros de una skill (`GET …/catalog/skills/<nombre>/files/<ruta>`, contrato §3.4) y la del contenido de un documento (`GET …/catalog/knowledge-content/<ruta>`, §3.5), que llevan su contenido a propósito. Son las únicas exentas y se reconocen por la ruta entera (anclada, sin `?` ni `#`), el método `GET` y el estado `200` (el paso comprueba que son exactamente dos —`skills/base/files/SKILL.md` y `knowledge-content/ventas/guia.md`— y que llevan el centinela); los errores de ambas rutas —`403` del USER, `404`, `422` con traversal y con `_acl.yaml`, `413` de un fichero o documento de más de 256 KiB y `415` de un binario, que el paso provoca— se escanean como el resto. Controles positivos: la respuesta de `baas_get_skill` del MCP y las dos `200` sí llevan el centinela |
| 17.8 | Antes, en `base` (sin `baas.yaml`), `PUT` de estado `active` (el que ya tiene) → `200` con `roles_sha256: null` y sin escribir ningún `baas.yaml` (regresión: la webapp lo rechazaba con `503`). `PUT` de estado `deprecated` (cambio real: huella `str`; repetido, idempotente con la misma huella): el USER recibe `NOT_FOUND` en `baas_get_skill` y no la ve en `baas_list_skills`; el ORG_ADMIN la ve `deprecated` con sus roles y `informe` la ve igual en `depends_on`; el `sync` del plugin (`sync_organization` sobre carpetas temporales) la había traído y el siguiente la retira del disco; al reactivar, el USER la vuelve a ver y un tercer `sync` la restaura; una huella cualquiera → `409`; la auditoría `skill.status_changed` suma solo los dos cambios reales |
| 17.9 | Contenido de un fichero de una skill (§3.4), sobre `base` con tres ficheros más escritos por MCP con `encoding: base64`: `scripts/limite.txt` (262144 bytes), `scripts/grande.txt` (262145), `scripts/binario.bin` (con NUL y bytes que no son UTF-8) y `scripts/_acl.yaml` (todos en `file_list`). El ORG_ADMIN abre `SKILL.md`, `scripts/hola.py` y el de 262144 bytes por `GET /api/v1/orgs/<org>/catalog/skills/base/files/<ruta>`: `200` con exactamente las ocho claves del contrato, `content` igual a lo escrito, `size` y `sha256` los del disco (los de `file_list`), `language` (`markdown`, `python`, `text`), `Cache-Control: no-store` y `Pragma: no-cache`. USER → `403 FORBIDDEN`, sin sesión → `401`, ORG_ADMIN de otra organización → `404`, organización inexistente → `404`. Once rutas con traversal (`..`, `%2e%2e`, `..%2f`), barra inicial, segmentos vacíos o `.` y `_acl.yaml` (también `scripts/_acl.yaml` y `%5Facl.yaml`) → `422 INVALID_ARGUMENT` (`field: path`: la webapp valida antes de llamar al MCP); fichero o skill inexistente → `404`; 262145 bytes → `413 CONTENT_TOO_LARGE` con `size` y `max_bytes`; binario → `415 CONTENT_NOT_TEXT`. Auditoría (`GET /api/v1/audit?action=skill.file_read&org=<slug>`, contada por diferencia en la organización del paso): una fila por lectura correcta (cuatro: `SKILL.md` dos veces, el script y el de 262144 bytes; no agregadas, `count` 1), con `actor`, `target_type` `skill`, `target_id` `base` y `metadata` con `path`, `sha256` y `size`; ninguna por las 19 peticiones fallidas; el ORG_ADMIN de la organización ve las mismas filas que el PLATFORM_ADMIN; ninguna respuesta de la auditoría contiene los centinelas ni claves de contenido, y los errores de la ruta de ficheros tampoco |
| 17.10 | Contenido de un documento de conocimiento (§3.5), sobre `knowledge/ventas/` con cinco ficheros más escritos por MCP con `encoding: base64`: `consulta.sql`, `guía-ñandú.md` (nombre con tildes en NFC), `limite.txt` (262144 bytes), `grande.txt` (262145), `binario.bin` (con NUL y bytes que no son UTF-8) y un `_acl.yaml` de subcarpeta. El ORG_ADMIN abre por `GET /api/v1/orgs/<org>/catalog/knowledge-content/<ruta>` el `.md` del escenario (en forma corta `ventas/guia.md`, en completa `knowledge/ventas/guia.md` y otra vez en corta), el `.sql`, el de 262144 bytes y el de las tildes (la ruta con sus caracteres codificados): `200` con exactamente las seis claves del contrato, `content` igual a lo escrito (los bytes que el escenario envió, no lo que contesta la API), `size` y `sha256` los del disco (los del listado de conocimiento), `path` la ruta completa canónica `knowledge/…` con las dos formas de la petición, `language` (`markdown`, `sql`, `text`), `Cache-Control: no-store` y `Pragma: no-cache`. USER → `403 FORBIDDEN`, sin sesión → `401`, ORG_ADMIN de otra organización → `404`, organización inexistente → `404`. Veintiuna rutas con traversal (`..`, `%2e%2e`, `..%2f`), barra inicial, segmentos vacíos o `.`, barra invertida (`%5C` y sin codificar), la raíz (`knowledge`, `knowledge/`, `knowledge/.`), un directorio, `_acl.yaml` en cualquier nivel (también `ventas/_acl.yaml`, que está escrito, y `%5Facl.yaml`) y el nombre con tildes en NFD → `422 INVALID_ARGUMENT` (`field: path`); documento inexistente y `skills/base/SKILL.md` (se lee como `knowledge/skills/…`) → `404`; 262145 bytes → `413 CONTENT_TOO_LARGE` con `size` y `max_bytes`; binario → `415 CONTENT_NOT_TEXT`. Auditoría (`GET /api/v1/audit?action=knowledge.file_read&org=<slug>`, sin filtrar por `target_id`, contada por diferencia en la organización del paso): seis filas, una por lectura correcta (el `.md` tres, con el mismo `target_id` —la ruta completa canónica— en las dos formas; el `.sql`, el de 262144 bytes y el de las tildes, una cada uno; no agregadas, `count` 1), ninguna de otro documento y ninguna por las 29 peticiones fallidas (se cuentan antes y después de ellas); cada fila con `actor`, `target_type` `knowledge` y `metadata` exactamente `{sha256, size}`; el ORG_ADMIN de la organización ve las mismas filas que el PLATFORM_ADMIN; ninguna respuesta de la auditoría contiene los centinelas ni claves de contenido (control positivo: el mismo escáner ve el centinela en la `200` de la ruta y en una auditoría simulada que lo llevase), y los errores de la ruta tampoco |

Con podman, `podman start` de `webapp-api` vuelve a ejecutar sus dependencias ya terminadas (`webapp-migrate` y
`webapp-seed`, idempotentes); `podman restart` no sirve con `podman-compose` (las da por «no arrancadas»). Si una ejecución se corta en el grupo `uso`, puede quedar una organización `uso-<ejecución>-<paso>` (se bloquea y elimina igual desde W3), un servicio en pausa o parado (`podman unpause`/`podman start` de `webapp-api` o `mcp-server`) y, si se corta en 12.8, la retención de métricas en 60 meses (la siguiente ejecución de 12.8 lo avisa; se restaura en Plataforma → Uso). La pila
simulada (`dev/fake_h4_stack.py`) no simula este grupo: sus utilidades e inspector se prueban en `tests/test_e2e_uso.py` (y las del grupo `acl`, en `tests/test_e2e_acl.py`).

Los pasos que dependen de uno fallido quedan `SALTADO` (y cuentan como fallo). El bloqueo, la eliminación y la
cuarentena se esperan hasta `--trace-timeout` s porque llegan a proxy y MCP por Pub/Sub; igual la revocación de claves y
la pérdida de la membresía en la parte `huerfanos`. Una ejecución completa envía unas 45 peticiones al proveedor, más
siete de la parte `huerfanos`, dos del grupo `limite` (14.2), 26 del grupo `consumo`, unas 530 del grupo `uso` (sobre todo 12.7 y 12.10) y unas
31 del grupo `ner` (6 en 15.2, 6 en 15.3, 3 en 15.4, 7 en 15.5 —las que salen con el NER parado más la sonda final— y 9 en 15.6) (`/_debug/requests` guarda las 50 últimas; el grupo `originales` no lo lee, y `uso` y `consumo` tampoco). Si una ejecución se corta entre 9.1 y 9.4,
queda una organización `e2e-<ejecución>` que se puede bloquear y eliminar a mano desde W3; si se corta la parte
`huerfanos`, quedan organizaciones `huerf-<ejecución>-o1…o4` (se eliminan igual). Los inicios de sesión están limitados a
20 por minuto y por IP: la parte `huerfanos` inicia sesión unas 15 veces y espera lo que diga `retry_after_seconds` si
recibe `429`.

### 3. Qué se ejecutó de verdad y qué no

| Qué | Estado |
| :--- | :--- |
| Utilidades puras (SSE, comprobaciones de contenido, cliente WebSocket, inspector) | **Ejecutado**: `tests/test_e2e_h4.py` (la CI) |
| Recorrido completo contra `dev/fake_h4_stack.py` (webapp, proxy, MCP y proveedor simulados) | **Ejecutado**: todos los pasos en verde con el cuerpo del proveedor guardado como objeto y como texto, y dos ejecuciones seguidas |
| Cada fallo inyectable de la pila simulada (79) detectado por el paso que le corresponde | **Ejecutado** (tabla de abajo) |
| El inspector de Redis (`CONFIG GET`, `find`, `BGREWRITEAOF` y su espera, la búsqueda con `grep`) | **Ejecutado** contra un motor falso que **corre de verdad el script de búsqueda** sobre un directorio temporal (`tests/fake_engine.py`) y, aparte, a mano contra dos contenedores `redis:7` desechables (uno con AOF y otro con `tmpfs` y sin AOF) con `podman`, sin tocar ninguna pila: el control positivo, la reescritura, la fuga detectada y la limpieza (2026-11-18) |
| Contra la pila real de H4 (`h4-int`): proxy real con el modo observación y el estado por conversación, webapp real con el interruptor, la eliminación, la vista del PLATFORM_ADMIN y el WebSocket, MCP real con la cuarentena, vault y Redis reales, **el proxy y la webapp con `BAAS_REDIS_EPHEMERAL_URL`** (grupo `originales` y 9.8 con las dos instancias) y el 11 (usuarios sin organización: la webapp, el proxy y el MCP con la revocación de claves) | **Ejecutado el 2026-11-18** en `h4-int` con H4-08, A′ y los huérfanos juntos: `e2e_h4.py` completo (los cinco grupos) y `e9_roles.py` |
| Las peticiones del grupo `limite` desde un contenedor (`caddy_requests`) | **Ejecutado**: el motor falso corre de verdad el script de `python3 -c` contra un servidor local que hace de Caddy (`Host`, `X-Forwarded-For`, estado, `Retry-After`, cuerpo, conexión rechazada y entradas inseguras rechazadas antes de lanzar nada) |
| El grupo `limite` contra la pila real (`h413-int`, 2026-11-19: proxy y MCP de `feat/h4-13-limite-claves`, `webapp` y `deploy` de `main`) | **Ejecutado**: 14.1 a 14.4 en verde a la primera, y otra vez justo después (con la espera de 181 s); el `WARN` del proxy y del MCP lleva la IP del contenedor, no las falsas. En la misma pila, `e2e_h4.py` completo (los siete grupos, 76 pasos) y `e9_roles.py` (12 pasos) en verde: ningún paso anterior recibe `429`. Repetido tras la revisión final (ramas rebasadas sobre `main`, con el episodio del `WARN` hasta que el cubo se llena y la tolerancia a una ráfaga lenta): otra vez 76 + 12 pasos en verde; un `--only limite` justo después encontró 4 fichas, esperó 181 s y pasó, y el proxy no repitió el `WARN` con esas 4 fichas, solo tras llenarse el cubo |
| El grupo `ner` (utilidades puras e inspector) | **Ejecutado**: `tests/test_e2e_ner.py` (la CI), con un motor falso para `ephemeral_scan`, `ephemeral_delete`, `ner_control` y `ner_health` (patrones y claves seguros, lotes de 100, solo `stop` y `start`). La pila simulada de `dev/fake_h4_stack.py` **no** tiene NER: el grupo se prueba contra la pila real |
| El grupo `ner` contra la pila real (`f2ner`, 2026-11-20: ramas sin fusionar de la webapp, el proxy con el servicio `ner`, `deploy` y el plugin) | **Ejecutado**: 15.1 a 15.7 en verde con `--only ner` |
| El grupo `consumo` contra la pila real (`consumo-int`, 2026-11-21, reconstruida desde cero: `webapp` con las ramas `feat/f2-consumo-webapp-*` fusionadas, `mcp-server` y `deploy` de `feat/f2-consumo-*` y `main` del proxy) | **Ejecutado**: 16.0 a 16.8 en verde a la primera, con 16.8 **saltado** (la pila no tiene NER: ningún turno trae `verification.l2`); `e2e_h4.py` completo (todos los grupos, 90 pasos) y `e9_roles.py` (12 pasos) en verde. La rama con NER de 16.8 solo se ha ejecutado contra la pila simulada con `ner=True` |
| El grupo `consumo` contra la pila real **tras fusionar `upstream/main` de la webapp y renumerar la migración a `0012`** (`consumo-fin`, 2026-11-21, reconstruida desde cero: `webapp` backend `969acd6` + frontend `7a13e89` fusionados, `mcp-server` `dac93c4`, `deploy` `d47425e` y `main` del proxy `223efbb`) | **Ejecutado**: la migración llegó a `0012`; `e2e_h4.py` completo (todos los grupos, 90 pasos) y `e9_roles.py` (12 pasos) en verde, con 16.8 **saltado** por defecto; y `--only consumo --ner` en esa pila (sin NER) → 16.8 **falla** con «se declaró el NER (--ner o BAAS_E2E_NER=1) y ningún turno trae verification.l2…» y los demás pasos del grupo en verde |
| La interfaz web (W3, W7, W8, W11 en el navegador, en `es` y `en`) y «el PLATFORM_ADMIN no ve el contenido **por la UI**» | **Fuera de esta prueba.** La UI pinta lo que devuelve la API que se comprueba aquí (`content_available`); el recorrido en el navegador es de C2 |
| Claude Code real y Codex real | Fuera de esta prueba (prueba manual del usuario, C2; `docs/codex.md`) |

### 4. Probarla sin la pila: `dev/fake_h4_stack.py`

`dev/fake_h4_stack.py` simula en un solo proceso la webapp (con el interruptor, crear, bloquear, invitar y eliminar
organizaciones, usuarios nuevos con enlace de activación, claves personales, la vista previa de la eliminación con
`disable_orphans`, la reactivación, la auditoría con la forma de `GET /api/v1/audit` —`target_label` incluido—, la vista
de verificación del PLATFORM_ADMIN y el WebSocket), un proxy que sustituye y
restaura email, IBAN y DNI **solo con el anonimizador encendido** y que en observación reenvía el cuerpo tal cual con
`WARNING` y `residual_matches`, un MCP con `baas_write_document` y la cuarentena, y el proveedor con eco de H3. Proxy y
MCP limitan las claves desconocidas por cliente (H4-13) con un cubo de fichas cuyo reloj adelanta la prueba en vez de
esperar; las peticiones del grupo `limite` van del inspector simulado a la pila con el contenedor como cliente. Para el
grupo `consumo` sirve (con `dev/fake_consumo_reports.py`, funciones puras sobre los turnos que el proxy simulado ha
servido) el informe de consumo y los informes globales de tráfico y anonimizador (contenido y disco, con la forma y sin
datos), el anonimizador y el tráfico de una organización con los permisos del contrato y `usage.viewed` del detalle por
persona, `PATCH /orgs/{slug}` con la zona y el filtro `org` de la auditoría; todo en UTC, y con `FakeH4Stack(ner=True)`
(o el fallo `consumo-l2-empty`) los turnos con AI-IP traen `verification.l2`. **No simula el disco ni el CSV del informe
de consumo**: 16.2 y 16.3 solo se prueban contra la pila real (sus utilidades, en `tests/test_e2e_consumo.py`). Importa
de `dev/fake_h3_stack.py` el proveedor, el vault y la rehidratación sin tocarlo. No es una implementación de referencia:
es un doble para probar la prueba.

```bash
python3 dev/fake_h4_stack.py                          # imprime las URLs y el comando de la prueba
python3 dev/fake_h4_stack.py --fault observe-rewrites # con un fallo inyectado: la prueba sale con 1
```

`tests/test_e2e_h4.py` ejecuta el recorrido completo y, para cada fallo, comprueba el código `1`, el paso que falla y su
motivo; `test_every_fault_is_detected` falla si se añade un fallo sin su caso.

| Fallo (`--fault`) | Paso que falla |
| :--- | :--- |
| `observe-rewrites` (el proxy altera el cuerpo con el anonimizador apagado) · `observe-anonymizes` (sustituye igualmente) | 5 |
| `observe-clean` (el turno en observación sale `CLEAN` sin coincidencias) | 6 y 8.10 |
| `no-cause` (la webapp no dice con qué anonimizador empezó la conversación) | 6 y 8.10 |
| `state-follows-switch` (una conversación abierta adopta el valor actual del interruptor, en los dos sentidos) | 8.7 y 8.9 |
| `no-ratchet` (una conversación abierta en claro no sube al encender el anonimizador: el comportamiento de antes de H4-08) | 8.5 |
| `ratchet-lowers` (apagar el anonimizador baja una conversación que ya estaba anonimizada) | 8.9 |
| `no-protected-from` (la webapp no informa de `protected_from_turn`) | 8.10 |
| `admin-sees-pre-raise` (el PLATFORM_ADMIN ve el texto de los turnos que salieron en claro) · `admin-hides-raised` (no ve los que ya salieron anonimizados) | 8.11 |
| `toggle-no-propagation` (el proxy no se entera del cambio) | 8.4 |
| `admin-sees-content-api` (el PLATFORM_ADMIN recibe el texto en turnos, lista y WebSocket) · `admin-sees-bypass` (también con AI-IP apagado) | 7.1, 7.2 y 7.3 · 7.2 |
| `admin-detail-leak` (el detalle trae texto) · `admin-reveal` (puede revelar) · `ws-leak` · `ws-silent` | 7.1 · 7.1 · 7.3 · 7.3 |
| `toggle-no-reason` · `toggle-user-allowed` · `toggle-foreign-403` · `toggle-ai-ip-off-allowed` | 8.1 |
| `toggle-not-applied` (responde `200` y no cambia nada) | 8.3 |
| `delete-no-block-check` · `delete-meta-allowed` · `delete-orgadmin-allowed` | 9.2 |
| `delete-no-slug-check` · `block-not-applied` | 9.3 |
| `delete-twice-ok` · `proxy-still-serves` · `mcp-still-serves` · `delete-removes-users` | 9.5 |
| `slug-reusable` · `audit-missing` | 9.6 · 9.7 |
| `vault-kept` · `redis-kept` · `no-quarantine` | 9.8 |
| `ephemeral-keys-kept` (eliminar la organización no borra la copia caliente de sus conversaciones en `redis-ephemeral`) | 9.8 |
| `no-ephemeral` (la pila no tiene `redis-ephemeral`: una antigua, con los originales en el AOF) | 10.1 (el resto del grupo, `SALTADO`) |
| `traces-in-aof` (el proxy publica las trazas en la persistente; el vault caliente va bien a la efímera) | 10.4 (solo el AOF vivo lo delata: la traza ya no está en memoria) |
| `originals-in-aof` (las trazas **y** la copia caliente del vault van a la persistente) | 10.4 y 10.6 |
| `scanner-blind` (el escáner no lee la base binaria del AOF) | 10.5; 10.6 y 10.7 quedan `SALTADO` |
| `ephemeral-persists` (`redis-ephemeral` con `appendonly yes` y ficheros en `/data`) | 10.7 |
| `residue-in-persistent` (la persistente conserva copias calientes del vault, claves de eventos y trazas de una pila anterior a A′) | 10.0 (10.4 y 10.6, `SALTADO`) |
| `preview-lists-all` (lista a todos los miembros) · `preview-needs-block` (exige la organización bloqueada) | 11.3 |
| `preview-open` (la pide un ORG_ADMIN o un USER) | 11.4 |
| `orphans-kept` (no desactiva a nadie) · `orphans-other-org` (desactiva también a quien conserva otra organización) | 11.5 |
| `orphans-keys-kept` (no revoca sus claves) | 11.6, 11.8 y 11.10 |
| `orphans-sessions-kept` (no cierra sus sesiones) · `disabled-can-login` (un desactivado inicia sesión) | 11.6 y 11.10 |
| `orphans-audit-missing` (sin fila `user.disabled`) · `orphans-audit-email` (la fila lleva email y nombre) | 11.8 y 11.10 |
| `orphans-ignore-flag` (ignora `disable_orphans: false`) | 11.9 |
| `orphans-default-off` (sin el campo no desactiva) · `orphans-invited-skipped` (deja fuera a los `INVITED`) | 11.10 |
| `reactivate-orgadmin` (un ORG_ADMIN reactiva) | 11.11 |
| `reactivate-no-audit` (sin `user.reactivated`) · `reactivate-restores-keys` (vuelven a valer las claves revocadas) · `reactivate-audit-no-org` (la fila `user.reactivated` no lleva la organización) | 11.12 |
| `audit-orgadmin-sees-disabled` (el ORG_ADMIN ve `user.disabled`) · `audit-orgadmin-blind` (no ve ni `user.reactivated`) | 11.12b |
| `orphans-links-kept` (no invalida los enlaces de activación sin usar) | 11.13 |
| `limit-off-proxy` (el proxy no limita) | 14.1 (14.2, `SALTADO`) |
| `limit-off-mcp` (el MCP no limita) | 14.3 |
| `limit-trusts-xff` (el cliente sale del `X-Forwarded-For` de la petición: el remitente elige su cubo) | 14.1 y 14.3 |
| `limit-blocks-known` (una clave ya válida recibe `429` desde un cliente frenado) | 14.2 y 14.3 |
| `limit-global` (un solo cubo para todos: el equipo queda frenado con el contenedor) | 14.4 |
| `limit-no-retry-after` (el `429` sin `Retry-After`) | 14.1 y 14.3 |
| `limit-shape` (el `429` del proxy con la forma del MCP, sin `baas_auth_rate_limited` ni `rate_limit_error`) | 14.1 |
| `consumo-absent` (la pila no trae el informe de consumo: `GET /platform/usage` es el de la Fase 4) | 16.0 (16.1 a 16.8, `SALTADO`) |
| `consumo-mode-wrong` (el informe cuenta como anonimizados los turnos en observación) | 16.1 |
| `consumo-global-open` (las cuatro rutas globales no exigen PLATFORM_ADMIN) | 16.4 |
| `consumo-global-wrong` (el tráfico global cuenta una petición de más en cada organización con tráfico) | 16.5 |
| `consumo-anonymizer-wrong` (el anonimizador de la organización no cuenta los turnos en observación por modo) | 16.6 |
| `consumo-anonymizer-open` (un USER ve el anonimizador de su organización) | 16.6 |
| `consumo-viewed-written` (las consultas globales y las del anonimizador escriben `usage.viewed`) | 16.7 |
| `consumo-l2-empty` (hay NER, pero el consumidor no cuenta `verification.l2`: las `l2_*` salen a cero) | 16.8 |

### 5. Supuestos sobre los demás componentes

Fijados por el contrato y el plan, y contrastados con las ramas de los demás componentes en el momento de escribir esto
(`webapp` `feat/h4-webapp` en `3203758`, `llm-interceptor-proxy` `feat/h4-observacion` en `681a454`); conviene
confirmarlos al integrar:

- **Webapp**: `PUT /api/v1/orgs/{slug}/anonymizer` y `POST …/delete` responden como `PUT …/ai-ip` (`organization` y
  `propagated`; se acepta también la organización en la raíz); la organización (`GET /api/v1/orgs/{slug}`) y la
  conversación (lista y detalle) traen `anonymizer_enabled` (se acepta también `anonymizer_enabled_at_start`) y, desde
  H4-08, `protected_from_turn`; la conversación y el turno traen `content_available`, falso para el PLATFORM_ADMIN en una
  organización ajena en los turnos que salieron con AI-IP o el anonimizador apagados (por turno desde H4-08); `GET /api/v1/audit?action=org.deleted` lista la entrada con el slug y el motivo en
  cualquier campo; `POST /api/v1/orgs/{slug}/members` con un usuario existente lo añade sin pasos más; el WebSocket
  `/api/v1/ws/traces?org=<slug>` envía primero `{"type": "hello"}` y después mensajes `turn.completed` con la
  conversación y el turno ya filtrados por espectador. Las comprobaciones de la eliminación siguen el orden de la
  webapp (`404` → `403` → `ORG_PROTECTED` → `REASON_REQUIRED` → `SLUG_MISMATCH` → `ORG_NOT_BLOCKED`); cada paso prueba
  una sola condición, así que no depende de él.
- **Webapp (usuarios sin organización, contrato §6)**: `GET /api/v1/orgs/{slug}/delete-preview` →
  `{members, conversations, orphans: [{id, email, name, status, personal_keys}]}` para el PLATFORM_ADMIN aunque la
  organización no esté bloqueada, `403` para su ORG_ADMIN y su USER, `404` para quien no la ve; `members` cuenta todas las
  membresías (también la del PLATFORM_ADMIN). `POST …/delete` responde `{"propagated", "disabled_users"}` (entero) y
  `disable_orphans` vale `true` si falta. Un desactivado recibe `401`/`403` al iniciar sesión con su contraseña correcta,
  su sesión anterior responde `401` en `GET /api/v1/me` y el handshake con su clave, `401`. El enlace de activación de un
  `INVITED` desactivado ya no vale (`GET /api/v1/auth/activation/<token>` → `status` distinto de `VALID`) y el de uno
  reactivado como `INVITED` es nuevo (`activation_url` en la respuesta de invitar). `GET /api/v1/api-keys?scope=mine`
  trae `id` y `revoked_at`; `GET /api/v1/me`, `status` y `memberships[].org_slug`; `GET /api/v1/orgs/{slug}/members`,
  `members[].email`. La auditoría (`GET /api/v1/audit?action=…&limit=200`) trae `actor.email`, `org_slug`, `reason`,
  `target_id`, `target_label` y `metadata`. `user.disabled` no tiene organización (`org_slug` nulo) y su `metadata` es
  `{cause, org_slug, previous_status, keys_revoked}`; `user.reactivated` lleva `org_slug` = la organización a la que se invita
  (su ORG_ADMIN la ve) y `metadata` `{org_slug, new_status}` (`ACTIVE` si el usuario ya tenía contraseña, `INVITED` si
  nunca activó), sin `previous_status`. Un ORG_ADMIN que lee la auditoría ve las filas de sus organizaciones y ninguna
  `user.disabled`. `target_label` (decidido: se deja así): la API lo resuelve al leer con el email de la cuenta
  (`target_type: user`), la fila guardada no lo lleva; la prueba lo excluye en 11.8 y 11.10 y comprueba todo lo demás.
  Un PLATFORM_ADMIN que invita a un usuario existente que es miembro de otra organización lo añade sin enlace (`201`).
- **Proxy y MCP (claves revocadas)**: una clave personal revocada se rechaza con `401 baas_invalid_api_key` (proxy) y `401`
  (MCP) en cuanto llega la revocación por Pub/Sub; una clave vigente de un usuario sin organización recibe
  `403 baas_access_denied` (proxy) y `404` (MCP).
- **Proxy**: con AI-IP activo y el anonimizador apagado, el cuerpo sale idéntico (la prueba lo compara con lo que guarda
  `/_debug/requests`), `residual_matches` trae una entrada por regla con `token_type` y `count` (las de `DNI`, `EMAIL`
  e `IBAN` al menos; otras reglas, como la de tarjeta, pueden añadir las suyas), y una organización bloqueada o eliminada
  responde `403` con `baas_access_blocked` o `baas_access_denied` (Chat Completions).
- **MCP**: `…/org/<slug>/mcp` → `404` tras eliminar la organización; `baas_write_document` en un documento
  `knowledge/e2e/e2e-<ejecución>.md` con `roles: []` lo acepta un ORG_ADMIN; la cuarentena es `<datos>/.quarantine/<slug>-<fecha>`
  (`quarantine.py`) y el directorio de datos de su contenedor es `/data` (`--mcp-data-dir`).
- **Pila (H4-02, dos instancias de Redis)**: hay un servicio `redis-ephemeral` (`redis:7`, sin AOF ni RDB y sin volumen: un
  `tmpfs` en `/data`) además de `redis` (AOF, volumen en `/data`); **los dos tienen `BAAS_REDIS_PASSWORD` en su entorno**
  (la prueba se autentica con la del propio contenedor), `sh`, `grep` (con `-r`, `-E`, `-a`, `-q`, `-s`) y `find`;
  el proxy y la webapp llevan `BAAS_REDIS_EPHEMERAL_URL`; `baas_vault` tiene `conversation_state(conversation_id,
  org_slug)` y `vault_entries(conversation_id, org_slug)`; la organización `privada` (AI-IP y anonimizador encendidos)
  y `analista@privada.test` están en la semilla de desarrollo (los de H3); `deploy/scripts/purge-redis-originals.sh`
  existe (lo cita 10.0). En una pila sin `redis-ephemeral` los pasos 9.1b y 9.8 miran solo `redis` y el grupo
  `originales` falla en 10.1.
- **Proxy y MCP (límite de claves no válidas, H4-13)**: con `BAAS_AUTH_FAIL_BURST` 30 y `BAAS_AUTH_FAIL_PER_MINUTE` 10
  (los valores por omisión; otros, con `--auth-fail-burst` y `--auth-fail-per-minute`), el cliente es la **última**
  entrada de `X-Forwarded-For` y Caddy (sin `trusted_proxies`) sustituye la que envía el cliente por la IP de quien
  conecta. `429` del proxy: OpenAI `error.type: requests` · `error.code: baas_auth_rate_limited`, Anthropic
  `rate_limit_error`, mensaje `BaaS-AI-IP: demasiadas API keys no válidas desde tu red; vuelve a intentarlo en N s` y
  `Retry-After: N`; del MCP, `{"error": "RATE_LIMITED", "message": "…N s."}` y `Retry-After: N`. Una clave que la
  instancia ya resolvió como válida no pasa por el limitador. La pila tiene el servicio `caddy` (escucha en el 80 de la
  red de compose) y `fake-upstream` (`python:3.13-slim`, con `python3`) en la misma red.
- **Pila**: el contenedor de PostgreSQL se llama por su servicio (`com.docker.compose.service=postgres`) y admite
  `psql -U postgres` sin contraseña desde dentro; el de Redis tiene `BAAS_REDIS_PASSWORD` en su entorno; el del MCP lleva
  `ls`. Con `--inspect none`, o sin `podman`/`docker` que vea el proyecto, los pasos 9.1b y 9.8 y el grupo `originales`
  se omiten o fallan con el motivo.

### 6. Resultado

Pendiente de la integración (C2). Contra la pila simulada, la ejecución completa pasa en un par de segundos (la parte
`huerfanos` en menos de uno).

## /baas-init guiado (conexión por aprobación en el navegador)

`tests/test_e2e_connect.py` prueba el `/baas-init` guiado de punta a punta sobre la pila **simulada**
(`dev/fake_h4_stack.py`, que ahora incluye `POST /api/v1/plugin/device/start` y `/device/poll`). Lanza
`python3 scripts/baas.py init ...` como proceso real, con un `HOME` y un proyecto temporales, sin navegador ni `claude`
reales, y se ejecuta con el resto de la suite (`uv run pytest tests/test_e2e_connect.py`). Cubre:

- recorrido feliz: `init` sale con 20 y el enlace de aprobación; `init --wait` sale con 0 y `[BAAS_RESULT linked]`,
  deja `~/baas-ai-ip/.env` en 0600 con la clave nueva y `.claude/settings.local.json` con el proxy y la cabecera de la
  clave; la clave no aparece en la salida de ningún comando; un segundo `init` sale `unchanged` sin pedir otro código;
- clave guardada revocada (handshake 401): se avisa, se descarta y el flujo la renueva solo;
- dirección equivocada: sale con 6 y `[BAAS_APP_URL ...]` sin guardar nada; con la correcta, sale con 20;
- límite de tasa (429) al empezar; código caducado (23) y reinicio con un código nuevo; denegado (22);
- dos `init` antes de aprobar muestran el mismo código (se reutiliza el pendiente);
- sondeo con varias respuestas «pendiente» antes de aprobar, con reloj inyectado (sin esperas reales);
- el guard, en un proyecto vinculado y sin proxy en el entorno: `/baas-init demo` y `/baas-status` pasan; cualquier otro
  prompt se bloquea con un mensaje en español llano.

El simulado se programa desde la prueba con `stack.device` (`outcome`: `approve`/`deny`/`expire`, `approve_after`,
`start_status`, `email`) y `stack.state.revoke_key(clave)`. No hay un script `dev/e2e_connect.py` contra la pila real:
aprobar exige una persona en el navegador.

Verificación manual en la pila de desarrollo (2026-11-19, webapp de la rama `feat/device-flow-plugin` en `baas-dev`, `http://app.localhost:8080`; el plugin con `HOME` y proyecto temporales; la aprobación se hizo por la API con `orgadmin@demo.test`, no en el navegador): **correcta**.

- `init` sin clave → código 20 con enlace y código; `init --wait` tras aprobar → 0 `[BAAS_RESULT linked]`; `.env` y `settings.local.json` con permisos 0600; la clave nueva no aparece en ninguna salida; un tercer `init` → `unchanged`.
- La clave nueva pasa el proxy (`/org/demo/anthropic`): la respuesta 401 es del proveedor por la credencial de Anthropic falsa que se envió a propósito, no del proxy.
- Clave revocada desde la API → `init` la renueva solo (aviso «ya no es válida», código 20); un segundo `init` reutiliza el mismo código; tras aprobar → `linked`.
- Dirección inexistente → código 6 con `[BAAS_APP_URL …]` y nada guardado.
- Pendiente: recorrido con la pantalla `/connect` en un navegador y con Claude Code real (skill y reinicio).

## E9 (roles funcionales)

`dev/e9_roles.py` comprueba contra la pila real que los **roles funcionales aíslan el contenido** de una organización,
aunque una skill del rol A cite la ruta de un documento del rol B (criterio 6 de la Fase 1). Crea lo que falte, así que
se puede lanzar las veces que haga falta.

### 1. Cómo lanzarla

La pila de `deploy` en modo desarrollo (`BAAS_DEV_SKIP_TOTP=1`, como `compose.dev.yml`):

```bash
uv run python dev/e9_roles.py --app-url http://app.localhost:8080 --evidence e9-evidencia.json
```

Sale con `0` si todo pasa, `1` si algún paso falla y `2` si los argumentos no valen. `--evidence` guarda cada llamada MCP
con su respuesta (sin API keys ni enlaces de activación; los textos largos se recortan). Los inicios de sesión están
limitados (20 por minuto y IP): con `RATE_LIMITED` la prueba espera y repite.

### 2. Qué crea y qué comprueba

Como PLATFORM_ADMIN (`admin@baas.test`), por la API de la webapp:

- La organización `e9-prueba` («E9 Prueba») con los roles funcionales `recepcion` y `facturacion`.
- Cuatro usuarios nuevos (`…@e9.test`, contraseña de desarrollo, activados por la API): un ORG_ADMIN, un USER solo con
  `recepcion`, un USER solo con `facturacion` y un USER sin rol funcional. Cada ejecución les crea una API key
  `e9-roles-<fecha>` y revoca las anteriores. Las URL del MCP salen del *handshake*.

Como ORG_ADMIN, **por MCP** y con el procedimiento de las skills `componer-conocimiento` y `componer-skill` de la
organización meta (buscar antes de crear, cabecera válida, `expected_sha256`, verificar con la búsqueda): un documento
por rol (`knowledge/recepcion/…`, `knowledge/facturacion/…`), un documento sin roles, y una skill con script por rol
(`gestion-de-citas`, `emision-de-facturas`) que **cita la ruta del documento del otro rol**.

Después, con la clave de cada usuario (9 búsquedas, filtros por `type` y `area` incluidos):

| Quién | Qué se comprueba |
| :--- | :--- |
| ORG_ADMIN y PLATFORM_ADMIN | Leen los siete ficheros y encuentran ambos documentos (control positivo) |
| USER con `recepcion` | Busca, lee y obtiene su documento, su skill y su script (con su `sha256`); la búsqueda y `baas_list_skills` no devuelven ni dejan ver trozos de lo de `facturacion`; leerlo por su ruta, su skill, su script o **siguiendo la cita de su skill** da `AUTHORIZATION_DENIED` con `required_roles: ["facturacion"]`; escribir da `AUTHORIZATION_DENIED` (`ORG_ADMIN`, `PLATFORM_ADMIN`) y no cambia nada |
| USER con `facturacion` | Lo simétrico |
| USER sin rol funcional | `baas_list_skills` vacío, búsquedas vacías y todas las lecturas denegadas |
| USER de otra organización (`user@otra.test`) | La ruta `/org/e9-prueba/mcp` responde `404 NOT_FOUND`; con `org=e9-prueba` desde su organización, `NOT_FOUND` en cinco tools |

`tests/test_e9_roles.py` prueba sin pila las utilidades puras, que el contenido es válido para el MCP, que lo visible para un
rol no contiene lo del otro (para que no haya falsos positivos) y que las funciones que buscan fugas detectan una.

### 3. Resultado (2026-11-18, pila `h4-int`)

La primera ejecución crea todo lo que falta (organización, roles, usuarios y los siete ficheros); las siguientes no crean
nada y repiten las comprobaciones. Con una mutación (el documento de facturación visible también para `recepcion`) el paso del rol `recepcion` falla y nombra la
ruta, el título y el extracto que se ven.

## Mediciones de H4

Dos scripts de `dev/` miden la pila de desarrollo real (`deploy/scripts/dev-up.sh --fake-upstream`, por defecto en
`*.localhost:28080`; si usas otros puertos, cambia las constantes `APP`, `PROXY` y `MCP`). Los resultados y su
método están en `knowledge/01-producto/fases/fase-1-h4-mediciones.md`. Son de una máquina de desarrollo: se repiten
en el servidor real (tramo 2).

| Script | Qué mide | Duración |
| :--- | :--- | :--- |
| `dev/measure_h4.py salida.json [n]` | Kill switch global → rechazo en el proxy y en el MCP, y petición al proxy → trama del WebSocket (con AI-IP y en bypass). Activa y levanta el kill switch `n` veces (30 por defecto) | ~1 min |
| `dev/measure_frozen_redis.py salida.json` | Redis **congelado** (`podman pause` del contenedor `h4-int_redis_1`, constante `REDIS`): línea base, al congelar, a los 4 min 30 s (dentro de la ventana de 5 min), a los 5 min 30 s y tras reanudar, con el proxy en bypass y con AI-IP, el MCP y el login. Lo reanuda siempre, también si falla | ~6 min |

Los textos de prueba no llevan dígitos seguidos: las reglas de AI-IP los toman por teléfonos y los sustituyen.

### Transparencia del proxy y la instancia efímera de Redis (H4-02)

Dos scripts más comprueban lo que nota un cliente de Claude Code (criterio rector de H4-02: el proxy debe ser lo más
transparente posible). Usan las opciones de `e2e_h4.py` para las URLs y el proyecto (`--app-url`, `--compose-project`…),
la semilla de desarrollo (`analista@privada.test` en `privada`, con AI-IP y anonimizador; `analista@demo.test` en `demo`,
en bypass) y solo la biblioteca estándar. Resultados y método: `knowledge/01-producto/fases/fase-1-h4-mediciones.md` §2.5.

| Script | Qué mide |
| :--- | :--- |
| `dev/measure_latency.py --n 300 --label X` | Latencia de extremo a extremo (Caddy → proxy → proveedor falso) con una conexión persistente: conversación nueva con AI-IP, conversación larga con historial (hasta 60 turnos) y bypass; p50, p95, p99 y máximo. Se ejecuta con la pila anterior y con la nueva, en calma, y se comparan (3 tandas cada una) |
| `dev/measure_ephemeral_outage.py --mode stop\|pause\|none` | Peticiones continuas mientras se **para** (`stop`/`start`), **congela** (`pause`/`unpause`) o no se toca (`none`, el control) el contenedor `<proyecto>_redis-ephemeral_1`. Imprime antes, durante y después: p50, p95, máximo, peticiones de más de 100 ms y errores; la petición más lenta durante el fallo; y cuántas conversaciones de la prueba llegan a la webapp al recuperarse. Sale con 1 si hay errores o se pierden trazas. Si algo falla a mitad, deja la efímera arrancada |

La mediana sube igual con y sin fallo (la conversación larga acumula historial): compara siempre con `--mode none`. En
una ejecución, la primera petición tras el fallo puede tardar ~100 ms (el tiempo de espera de la copia caliente) y las
siguientes, nada: la sonda de fondo del proxy y el disyuntor propio de la efímera hacen el resto.
