# Antigravity con BaaS-AI-IP

> **Estado**: tarea T3 (#165) de «soporte de más herramientas». Todo sale de la documentación oficial de Antigravity
> (leída el 2026-11-21) y de las pruebas del plugin con servidores falsos: **no se ha probado con la extensión real de
> VS Code ni con `agy`**. La lista de comprobación y las preguntas abiertas están al final.

Antigravity se usa como **extensión de VS Code** (`Google.google-antigravity`, VS Code 1.90 o posterior, cuenta de Google)
y como **CLI `agy`** (Linux, macOS y Windows). Las dos leen la misma configuración de MCP (`mcp_config.json`), así que
una sola orden las conecta.

| Mitad | ¿Con Antigravity? |
| :--- | :--- |
| **Cerebro** (el MCP de tu organización: skills y conocimiento) | **Sí**: `baas.py setup antigravity` |
| **Protección** (proxy y AI-IP) | **No**: sus conversaciones van directas a Google, **sin anonimizar**, y no aparecen en la webapp |

El plugin `baas` es de Claude Code; para Antigravity no hace falta instalarlo: basta con tener este repositorio (o el
plugin ya instalado) para ejecutar un script de Python.

## 1. Qué necesitas

- **Python 3.10 o superior** (solo biblioteca estándar).
- Tu **API key personal de BaaS-AI-IP** en `~/baas-ai-ip/.env` (en Windows, `%USERPROFILE%\baas-ai-ip\.env`). La guarda
  `/baas-init` en Claude Code o, en una terminal y sin pasar por ningún chat,
  `python3 "<ruta del plugin>/scripts/baas.py" set-key --app-url https://app.<dominio>` (ver el [README](../README.md)).
  `setup` **no** pide la clave ni la acepta como argumento: la lee de ese fichero.
- La extensión de Antigravity o `agy` instalados por ti. El plugin **no** los instala ni los ejecuta.

## 2. Conectar el proyecto

Una **organización por proyecto**, como con Claude Code: el servidor `baas` se escribe en el `mcp_config.json` **del
proyecto**, nunca en el global (`~/.gemini/config/mcp_config.json`, que afectaría a todos tus proyectos).

```bash
python3 "<ruta del plugin>/scripts/baas.py" setup antigravity --project <carpeta del proyecto>
```

- Sin `--project` usa la raíz git o la carpeta actual.
- Si perteneces a varias organizaciones, indica cuál con `--org <slug>`. Si el proyecto ya se vinculó con `/baas-init`,
  se usa esa organización.
- Para ver antes lo que escribiría, **sin la clave y sin tocar nada**: añade `--print`.
- Es seguro repetirlo (idempotente): también sirve para renovar la clave o cambiar de organización.

```bash
python3 "<ruta del plugin>/scripts/baas.py" setup antigravity --project <carpeta del proyecto> --print
```

Qué hace (y qué se niega a hacer):

| Fichero (en la raíz del proyecto) | Qué se escribe | Reglas |
| :--- | :--- | :--- |
| `.agents/mcp_config.json` | El servidor `baas` (forma de abajo). **Se conservan** los demás servidores, los demás ajustes del fichero y lo que ya tuvieras en la entrada `baas` (`disabledTools`, `timeoutSeconds`…) | Se añade al `.gitignore`; **se niega a escribir si el fichero está versionado** (`[BAAS_PROJECT_UNSAFE]`, hay que sacarlo con `git rm --cached`) o si no es JSON válido (no lo toca); permisos **0600** |
| `.agents/rules/baas.md` | La regla `always_on` de abajo | Sin secretos: puede versionarse. Si ya existe un `baas.md` que no escribió este comando, **no lo pisa** (avisa) |
| `AGENTS.md`, `GEMINI.md` | **Nada**: nunca se tocan | — |

```json
{
  "mcpServers": {
    "baas": {
      "serverUrl": "https://mcp.example.com/org/mi-organizacion/mcp",
      "headers": {
        "Authorization": "Bearer <API key>"
      }
    }
  }
}
```

`serverUrl` es el MCP de tu organización (la devuelve el *handshake* de la webapp) y solo admite **Streamable HTTP**, que
es lo que sirve el MCP de BaaS-AI-IP. `<API key>` se sustituye por tu clave, **en claro** (ver más abajo).

### La regla `.agents/rules/baas.md`

Es el equivalente de la skill `baas-brain` de Claude Code: la regla (`trigger: always_on`, se inyecta en cada turno)
enseña al agente las tools `baas_*` y a **detenerse** ante `AUTHORIZATION_DENIED` y `KILL_SWITCH_ACTIVE`, sin buscar el
mismo contenido por otra vía. Ocupa unos 5 KB; Antigravity admite 24 000 bytes por regla y trunca lo que pase. No usa
`AGENTS.md` ni `GEMINI.md` para no pisar los tuyos. Tampoco explica `/baas-sync` ni el ejecutor de scripts, que son de
Claude Code: la regla le dice al agente que **no ejecute** los scripts de las skills de la organización.

## 3. Extensión de VS Code

1. Instala la extensión **Antigravity** (`Google.google-antigravity`) desde el Marketplace e inicia sesión con tu cuenta
   de Google.
2. Abre la **carpeta del proyecto** en VS Code (la que pasaste a `--project`).
3. Ejecuta `setup antigravity` en una terminal (la integrada vale).
4. Recarga la ventana (*Developer: Reload Window*) para que lea el `mcp_config.json`.
5. Comprueba que el servidor `baas` aparece conectado en la configuración de MCP de la extensión y pídele al agente:
   «Llama a `baas_whoami`». Antigravity pregunta por defecto antes de usar una tool MCP: acéptala.

## 4. CLI `agy`

1. Ejecuta `setup antigravity` en la carpeta del proyecto.
2. Lanza `agy` **desde esa carpeta** (el MCP de proyecto solo aplica a su carpeta).
3. Escribe `/mcp` (el gestor de MCP de la CLI): `baas` debe aparecer conectado. Pídele al agente «Llama a `baas_whoami`».

## 5. La clave queda en claro (riesgo aceptado)

La documentación de Antigravity **no admite `${VAR}`** en las cabeceras ni un comando que dé el secreto (como el
`headersHelper` de Claude Code): `Authorization` lleva la clave literal. Por eso:

- La clave está **en claro** en `.agents/mcp_config.json`. El plugin lo deja en 0600, lo añade al `.gitignore` y se
  niega a escribir si está versionado, pero **cualquier proceso con tu usuario puede leerlo**, y el agente de
  Antigravity también si le abres ese fichero. La regla le prohíbe leerlo, pero no es una barrera técnica.
- En **Windows** los permisos 0600 no se aplican: el fichero hereda los de la carpeta del proyecto. No lo pongas en
  una carpeta compartida ni sincronizada.
- No pegues la clave en el chat ni la copies a otro fichero. Si se expone: revócala en la webapp (*Mis API keys*),
  guarda otra (`set-key` o `/baas-init`) y repite `setup antigravity`.
- Cuando el proxy admita credenciales de proyecto (#175) se podrá evitar guardar la clave personal aquí.

## 6. Lo que NO cubre

- **Sin proxy ni AI-IP.** Las conversaciones de Antigravity van a Google **sin anonimizar**: nada se sustituye antes de
  salir, no hay trazas en la webapp (W8) y el kill switch, el bloqueo y la pausa de la organización **no cortan el
  modelo**. Solo cortan el MCP (`KILL_SWITCH_ACTIVE`). No pegues datos que no pegarías en una herramienta sin
  protección. (La protección para su CLI con clave de Gemini es el trabajo de #178.)
- **Otros clientes de Antigravity** que no lean el `mcp_config.json` del proyecto.
- **Las skills de la organización como comandos.** Hoy el servidor `baas` expone las skills como *prompts* del MCP
  (`/mcp__baas__<skill>` en Claude Code) y la documentación de Antigravity no dice que los muestre. Llegarán como
  **skills nativas** (`.agents/skills/<nombre>/SKILL.md`): es un seguimiento, no está hecho. Mientras tanto el agente
  las consulta con `baas_list_skills` y `baas_get_skill`.
- **El ejecutor de scripts** (`run-script`) y `/baas-sync` desde Antigravity.
- Windows y macOS: probados por T6 (#168), no por esta guía.

## 7. Solución de problemas

| Síntoma | Causa y solución |
| :--- | :--- |
| `[BAAS_KEY_MISSING]` (2) | No hay clave en `~/baas-ai-ip/.env`: lanza `/baas-init` o `set-key` primero |
| `[BAAS_ORG_SELECTION_REQUIRED]` (3) | Perteneces a varias organizaciones: repite con `--org <slug>` |
| `[BAAS_AUTH_ERROR]` (5) | La clave está revocada o caducada: guarda otra (`/baas-init` o `set-key`) |
| `[BAAS_PROJECT_UNSAFE]` (7) | `.agents/mcp_config.json` está versionado (`git rm --cached .agents/mcp_config.json` y commit) o no es JSON válido (corrígelo; no se toca) |
| `baas` no aparece | Recarga la ventana o reinicia `agy` desde la carpeta del proyecto; comprueba que el JSON es válido |
| Falla con 401 | La clave del fichero está revocada: renuévala y repite `setup antigravity` |
| `KILL_SWITCH_ACTIVE` o 403 | La plataforma está detenida o tu organización bloqueada o pausada: habla con tu administrador |
| El agente no conoce las tools `baas_*` | Falta `.agents/rules/baas.md` o ya existía uno que no es de BaaS (el comando avisa): renómbralo y repite |

## 8. Lista de comprobación manual

Con la extensión real de VS Code y con `agy` real (Linux y Windows). Marca lo que compruebes:

- [ ] `setup antigravity --print` no muestra la clave y no escribe nada.
- [ ] `setup antigravity` crea `.agents/mcp_config.json` (0600 en Linux) y `.agents/rules/baas.md`, y añade el primero al
      `.gitignore`; `git status` no lo muestra.
- [ ] Repetirlo no cambia nada («sin cambios»); con otros servidores en el fichero, siguen ahí.
- [ ] **Extensión**: `baas` aparece conectado tras recargar; «Llama a `baas_whoami`» devuelve tu usuario y organización.
- [ ] **CLI**: `/mcp` muestra `baas` conectado; el mismo `baas_whoami` responde.
- [ ] El agente aplica la regla: ante una pregunta sobre la organización busca con `baas_search_knowledge`.
- [ ] Con un recurso al que no tienes rol, el agente **se detiene** e informa de `AUTHORIZATION_DENIED` (sin buscar otra vía).
- [ ] El acceso queda **auditado** (la webapp registra la llamada a la tool).
- [ ] Con la organización pausada, `KILL_SWITCH_ACTIVE` y el agente se detiene.
- [ ] La clave **no** aparece en el chat, en el `.gitignore` ni en ningún otro fichero del proyecto.
- [ ] Windows: las rutas, el `.gitignore` y el `mcp_config.json` salen bien; anota los permisos reales del fichero.

## 9. Preguntas abiertas (la documentación no las responde)

| Pregunta | Cómo comprobarla |
| :--- | :--- |
| ¿La extensión lee el mismo `mcp_config.json` que la CLI? | Configura solo con `setup` y mira si `baas` aparece en la extensión **sin** copiar nada a `~/.gemini/config/` |
| ¿Respeta el de proyecto en una **carpeta sin confianza** (*Restricted Mode*)? | Abre el proyecto sin aceptar la confianza y comprueba si `baas` se conecta. Si lo hiciera, un repositorio ajeno podría traer su propio `.agents/mcp_config.json`: no confíes en uno que no hayas escrito tú |
| ¿Usa el campo `instructions` del MCP? | La documentación solo dice que lo guarda en una caché (`instructions.md`); mira si el agente lo recibe sin la regla (borra `.agents/rules/baas.md` y pregúntale qué tools tiene) |
| ¿Salen las skills como comandos con las **skills nativas**? | Ver el apartado 6: la CLI convierte cada skill de `.agents/skills/` en un comando; falta comprobar la extensión |

Fuentes: <https://antigravity.google/docs/mcp>, <https://antigravity.google/docs/rules>, <https://antigravity.google/docs/skills>.
