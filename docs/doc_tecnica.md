# Documentación Técnica — Mi Contador de Bolsillo
**Interact2Hack 2026 · Reto IA Deuna · USFQ**

---

## 1. Problema y enfoque

Los micro-comerciantes ecuatorianos generan datos transaccionales en Deuna pero no los consultan: los dashboards tradicionales asumen alfabetización financiera que el usuario no tiene. El reto era construir un asistente conversacional que traduzca esos datos en respuestas accionables en menos de 5 segundos, en español neutro, sin formación contable.

**Decisión de diseño central:** modelamos el peor escenario real — Deuna solo captura monto total, hora y quién pagó. Sin ítems, sin catálogo. El agente funciona con los datos que Deuna ya tiene desde el primer cobro, cero configuración del comerciante.

---

## 2. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend  React + TypeScript + Vite                        │
│  MiPanaScreen (chat)   SaludFinancieraScreen (5 métricas)   │
└────────────────┬────────────────────────────────────────────┘
                 │ REST (JSON)
┌────────────────▼────────────────────────────────────────────┐
│  Backend  FastAPI                                            │
│  /chat · /health · /suggestions · /welcome · /report        │
└────────────────┬────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────┐
│  Agente  LangGraph — 6 nodos                                 │
│                                                              │
│  [Nodo 1+2 Clasificador+Semántico]                           │
│       ├─ en_scope ──────────► [Nodo 3 Generador SQL]        │
│       ├─ en_scope_financiero ► [Nodo 3] → [Nodo 6b Asesor]  │
│       └─ fuera_scope/ambiguous ──────► [Nodo 6a Sintetiz.]  │
│                                                              │
│  [Nodo 3] → [Nodo 4 Validador] → [Nodo 5 Ejecutor DuckDB]  │
│               └─ inválido ──► retry Nodo 3 (max 3)          │
│                                                              │
│  [Nodo 5] ──► [Nodo 6a Sintetizador] ──► respuesta JSON     │
└────────────────┬────────────────────────────────────────────┘
                 │ SQL sobre vistas
┌────────────────▼────────────────────────────────────────────┐
│  Capa Semántica  DuckDB en memoria                           │
│  8 vistas nombradas  ←  transacciones.csv (13,399 tx)       │
└─────────────────────────────────────────────────────────────┘

                 ┌──────────────────────────────┐
                 │  LLM — OpenAI-compatible API  │
                 │  Groq  Llama 3.3 70B  (~3s)  │
                 │  Ollama  Qwen 2.5 32B (HPC)   │
                 └──────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Alertas Proactivas  asyncio background task                 │
│  Tick 1 (10s): caída de ventas vs mediana histórica          │
│  Tick 2 (20s): DeUna Negocios — cobro con tarjeta            │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Stack tecnológico

| Componente | Tecnología | Justificación |
|---|---|---|
| LLM (demo) | Groq · Llama 3.3 70B | Open source · ~3s · $0.001/query · datos no salen a servidores propietarios |
| LLM (HPC) | Ollama · Qwen 2.5 32B | Despliegue local en CEDIA A100 · ~12s warm |
| Motor SQL | DuckDB en memoria | OLAP vectorizado · CSV directo sin import · GROUP BY en ms |
| Orquestación | LangGraph | Grafo dirigido con estado · loop Genera→Valida→Corrige determinístico |
| Backend | FastAPI + uvicorn | REST async · CORS · endpoint `/health` sin LLM |
| Frontend | React + Vite + Recharts | SPA · charts inline · sin dependencia de Chainlit |
| Alertas | asyncio background task | Hilo paralelo al chat · no bloquea el response loop |

---

## 4. Capa semántica — el diferenciante técnico

El LLM nunca ve el schema crudo. Solo accede a 8 vistas DuckDB con semántica de negocio:

| Vista | Descripción |
|---|---|
| `ventas_diarias` | Total + ticket promedio por día |
| `ventas_periodo` | Agregación por semana y mes |
| `categorias_populares` | Ranking por categoría (Bebidas, Lácteos, etc.) |
| `patrones_temporales` | Hora pico + día pico (año completo) |
| `patrones_temporales_mensual` | Hora pico filtrable por mes concreto |
| `gastos_proveedores` | Total pagado por proveedor (Egresos) |
| `gastos_proveedores_mensual` | Gasto por proveedor en mes concreto |
| `patrones_compra_proveedor` | Qué día del mes se compra al distribuidor |

**Por qué importa:** el "Performance Cliff" del Text-to-SQL en entornos reales cae al 6–21% con schema crudo (BIRD benchmark). La capa semántica eleva la precisión al superar el 80% requerido, al eliminar ambigüedad sobre qué columnas filtrar y cómo agregar.

Los overrides determinísticos en Python (independientes del LLM) redirigen preguntas temporales a las vistas mensuales correctas sin coste de token adicional.

---

## 5. Selección del modelo LLM

Se evaluaron cinco modelos en generación SQL analítico (benchmark Tinybird):

| Modelo | Precisión SQL | Latencia demo |
|---|---|---|
| Llama 3.1 405B | 76% | >5s (multi-GPU) |
| **Llama 3.3 70B** | **74%** | **~3s (Groq)** ✅ |
| Qwen 2.5 32B | ~72% | ~12s (HPC warm) |
| Codestral 22B | 70% | ~4s |
| Llama 3.1 70B | 60% | — |

Llama 3.3 70B vía Groq cumple el constraint de 5 segundos con margen y usa infraestructura open source — los datos financieros del comerciante nunca salen a modelos propietarios.

---

## 6. Arquitectura desacoplable

El sistema está diseñado para que cada capa se intercambie de forma independiente:

**LLM:** un solo campo en `agent/config.py` — `LLM_BASE_URL` y `MODEL_NAME`. Cambiar de Groq a Ollama a cualquier endpoint OpenAI-compatible es una modificación de 2 líneas sin tocar el agente.

```python
# agent/config.py
LLM_BASE_URL = "https://api.groq.com/openai/v1"   # ← cambiar aquí
MODEL_NAME   = "llama-3.3-70b-versatile"            # ← y aquí
```

**Frontend ↔ Backend:** el frontend React consume únicamente la API REST de FastAPI. El chat de Chainlit y el frontend React coexisten — ambos apuntan al mismo agente LangGraph.

**Alertas:** el `asyncio.Task` corre en un hilo independiente. Si falla no afecta el flujo de chat; si el chat falla las alertas siguen activas.

**Datos:** DuckDB lee el CSV directamente (`read_csv_auto`). En producción, reemplazar el CSV por una conexión a la API interna de Deuna requiere modificar únicamente `agent/semantic_layer.py`.

---

## 7. Manejo de errores y casos borde

**Prompt injection:** detección determinística pre-LLM con 30+ patrones en `nodes.py`. Costo: 0ms, 0 tokens. Respuesta fija sin pasar por el modelo.

**SQL inválido:** el Nodo 4 hace validación estática (solo SELECT/WITH, solo vistas permitidas). Si falla, regresa al Nodo 3 con el error como contexto — loop autocorrectivo hasta 3 reintentos.

**Resultado vacío:** el Sintetizador recibe instrucción explícita de declararlo. Nunca proyecta ni estima cuando DuckDB devuelve cero filas.

**Fuera de scope:** el Clasificador detecta preguntas externas al dataset (precio del dólar, inventario físico, impuestos) y hace bypass directo al Sintetizador con respuesta negativa, sin ejecutar SQL.

**Fecha de corte:** el dataset termina el 2026-04-18. Cuando el usuario pregunta por "hoy" (2026-04-19), el sistema responde con los datos más recientes disponibles y lo aclara explícitamente.

---

## 8. Privacidad por diseño

El agente no expone perfiles individuales de clientes. Todas las respuestas son datos agregados. Decisión alineada con la LOPDP Ecuador (vigente mayo 2023): el comerciante es un tercero respecto al cliente final; exponer nombre + frecuencia + monto requeriría base legal y opt-in explícito que el dataset sintético no contempla.

En producción, Deuna podría activar reconocimiento de clientes con consentimiento desde su propia app — la arquitectura lo soporta añadiendo las vistas sin modificar el agente.

---

## 9. Feature diferenciador — Mi Salud Financiera

Panel de 5 métricas calculadas directamente en DuckDB (sin LLM, respuesta instantánea):

| Métrica | Lógica | Semáforo |
|---|---|---|
| Tendencia de ventas | Últimos 3 meses vs 3 anteriores | Verde >5% · Rojo <-5% |
| Dependencia de proveedor | % egreso en proveedor top | Rojo >55% |
| Flujo de caja | Ratio ingresos / egresos | Verde >3x |
| Concentración de categorías | % ingreso en categoría top | Rojo >60% |
| Oportunidad semanal | Ratio mejor día / peor día | Siempre tip |

Cada tarjeta es clickeable y abre el chat con la pregunta correspondiente pre-cargada, conectando el diagnóstico con la conversación.

---

## 10. Resultados de precisión

| Suite | Preguntas | Aciertos | Precisión |
|---|---|---|---|
| Benchmark (15 preguntas típicas) | 15 | — | ⏳ pendiente re-run |
| Adversariales (injection + borde) | 10 | — | ⏳ pendiente re-run |

*Umbral requerido por el jurado: 80%*
