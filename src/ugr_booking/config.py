"""Application configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root
DATA_PATH = Path(os.getenv("DATA_PATH", "./data"))

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ugr_booking.db")

# ---------------------------------------------------------------------------
# Ollama / LLM
# ---------------------------------------------------------------------------

OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

# Regex pattern for anonymised VINs used in the case dataset
VIN_PATTERN: str = os.getenv("VIN_PATTERN", r"^ANONVIN\d{10}$")

# Plausibility ranges for number normalisation
WEIGHT_KG_MIN = 100.0
WEIGHT_KG_MAX = 100_000.0
DIM_M_MIN = 0.3
DIM_M_MAX = 30.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
