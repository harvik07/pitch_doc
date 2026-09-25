"""Environment config, paths and limits (CLAUDE.md sections 3, 4, 6 and 11).

Other modules must read these as ``settings.NAME`` at call time (not ``from settings import NAME``)
so tests can monkeypatch them.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# --- Paths -----------------------------------------------------------------------------------------
DATA_DIR = ROOT / "data"
POLICIES_DIR = DATA_DIR / "policies"
MARSH_PROFILE_PATH = DATA_DIR / "marsh" / "marsh_profile.md"
CACHE_DIR = DATA_DIR / "cache"
EVIDENCE_OVERRIDES_PATH = DATA_DIR / "evidence_overrides.yaml"
CONFIG_DIR = ROOT / "config"
EXPOSURE_TAXONOMY_PATH = CONFIG_DIR / "exposure_taxonomy.yaml"
PROMPTS_DIR = ROOT / "prompts"
OUTPUTS_DIR = ROOT / "outputs"
DELIVERABLES_DIR = ROOT / "deliverables"
DOCS_DIR = ROOT / "docs"
BRIEF_PATH = DOCS_DIR / "Marsh_Internship_Case_Study.pdf"

# Bundled brochures keep their original file names: document_id -> file name in POLICIES_DIR.
BUNDLED_POLICY_FILES = {
    "POL-NIVA": "Niva Bupa Product Brochure.pdf",
    "POL-HDFC": "HDFC Product Brochure.pdf",
    "POL-CARE": "Care Health Product Brochure.pdf",
    "POL-ABHI": "ABHI Product Brochure.pdf",
}

# --- Limits ----------------------------------------------------------------------------------------
MAX_FILE_MB = 25
MAX_REPAIR_ATTEMPTS = 2
FULL_CONTEXT_TOKEN_LIMIT = 30000
LLM_RETRIES = 3  # retries after the first attempt, on API errors/timeouts
LLM_TIMEOUT_MS = 120_000
DEFAULT_SUM_INSURED = 1_000_000  # INR 10 lakh; always shown as an assumption
COMPANY_NAME_MIN_LEN = 2
COMPANY_NAME_MAX_LEN = 120

# --- Gemini (google-genai on Vertex AI) ------------------------------------------------------------
USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").strip().lower() in {"1", "true", "yes"}
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_AUDIT_MODEL = os.getenv("GEMINI_AUDIT_MODEL", "").strip() or GEMINI_MODEL
