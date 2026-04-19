"""Configuración central de la demo — cambia aquí, se aplica en todo el proyecto."""

from __future__ import annotations

import os

# ── Comercio activo en la demo ─────────────────────────────────────────────────
# Opciones válidas: "COM-001" | "COM-002" | "COM-003"
DEMO_COMERCIO_ID: str = "COM-001"

# ── LLM — Groq + Llama 3.3:70B ────────────────────────────────────────────────
# Modelo open source: los datos de transacciones nunca salen a servidores de terceros
# propietarios. Compatible con infraestructura local en producción.
# Latencia demo medida: ~3.05s (vs ~10.06s en HPC con Qwen2.5:70B)
# Costo: ~$0.001111 por query (Groq API, mayo 2026)
LLM_BASE_URL: str = "http://localhost:11434/v1"
LLM_MODEL: str = "qwen2.5:32b"
LLM_API_KEY: str = "ollama"
