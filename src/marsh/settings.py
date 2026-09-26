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
PROFILES_DIR = DATA_DIR / "profiles"  # frozen company profiles (scripts/freeze_profile.py)
UPLOADS_DIR = DATA_DIR / "uploads"  # uploaded policy PDFs, by sha256 (git-ignored)
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
# Product names as stated in CLAUDE.md section 2.
BUNDLED_POLICY_NAMES = {
    "POL-NIVA": "Niva Bupa ReAssure 2.0",
    "POL-HDFC": "HDFC ERGO Optima Secure+",
    "POL-CARE": "Care Health Care Supreme",
    "POL-ABHI": "Aditya Birla Health Activ One",
}
# Plan variants named in CLAUDE.md section 2 (Care's OPD/Advanced are add-on policies, not variants).
BUNDLED_POLICY_VARIANTS = {
    "POL-NIVA": ["Platinum+", "Titanium+"],
    "POL-HDFC": [],
    "POL-CARE": [],
    "POL-ABHI": ["VIP+", "SAVR"],
}
CURRENCY_SYMBOL = "₹"  # display only; evidence text keeps the PDF's characters (Care/HDFC render ₹ as a backtick)

# Accepted product-name spellings per policy (for Prompt 8's policy-name check, grounding.policy_name_check).
# Only names that appear in the evidence; OCR garbles ("ReAssufe2.0", "ΘptimaSecure+") are left out. Matching
# ignores case ("ACTIV ONE" = "Activ One") but is whole-name. Care OPD / Care Advanced are add-on policies, not
# product names. "Optima Secure" (no "+") is NOT an alias: the HDFC footer UIN line names that different
# product. Slides always use the canonical PolicyDocument.display_name, never an alias.
PRODUCT_ALIASES = {
    "POL-NIVA": ["ReAssure 2.0"],
    "POL-HDFC": ["Optima Secure+", "OptimaSecure+", "Optima Secure +"],
    "POL-CARE": ["Care Supreme", "carē supreme"],
    "POL-ABHI": ["Activ One"],
}
# OCR/PDF spacing breaks inside names, collapsed by grounding.normalise_text (and nowhere else).
OCR_SPACED_NAMES = {"T itanium+": "Titanium+", "Platinum +": "Platinum+", "Optima Secure +": "Optima Secure+"}

# --- Limits ----------------------------------------------------------------------------------------
MAX_FILE_MB = 25
MAX_REPAIR_ATTEMPTS = 2
FULL_CONTEXT_TOKEN_LIMIT = 30000
LLM_RETRIES = 3  # retries after the first attempt, on API errors/timeouts
LLM_TIMEOUT_MS = 300_000  # one annotation call covers a whole brochure (HDFC: ~230 items)
DEFAULT_SUM_INSURED = 1_000_000  # INR 10 lakh; always shown as an assumption
COMPANY_NAME_MIN_LEN = 2
COMPANY_NAME_MAX_LEN = 120
MAX_EXPOSURES = 8  # identified exposures per company (baselines first)

# --- Gemini (google-genai on Vertex AI) ------------------------------------------------------------
USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").strip().lower() in {"1", "true", "yes"}
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip()  # gemini-3.8-flash is served on "global" only
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_AUDIT_MODEL = os.getenv("GEMINI_AUDIT_MODEL", "").strip() or GEMINI_MODEL
