from pathlib import Path

# --- Filesystem paths (single source of truth) ---
# Anchored to the repo root by walking up from this file: src/ -> code/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SUPPORT_TICKETS_DIR = REPO_ROOT / "support_tickets"
INPUT_CSV = SUPPORT_TICKETS_DIR / "support_tickets.csv"
OUTPUT_CSV = SUPPORT_TICKETS_DIR / "output.csv"
SAMPLE_CSV = SUPPORT_TICKETS_DIR / "sample_support_tickets.csv"
DECISION_TRACE_PATH = SUPPORT_TICKETS_DIR / "decision_trace.jsonl"
COVERAGE_LOG_PATH = SUPPORT_TICKETS_DIR / "coverage_gaps.log"
FAILURES_LOG_PATH = SUPPORT_TICKETS_DIR / "failed_tickets.log"

ESCALATION_KEYWORDS = [
    "site is down",
    "pages are inaccessible",
    "platform outage",
    "service outage",
]

RETRIEVAL_TOP_K_BM25 = 20    # BM25 candidates fed to reranker
RETRIEVAL_TOP_K_FINAL = 3   # reranker keeps this many for LLM

SCORE_THRESHOLD_HACKERRANK = 0.3
SCORE_THRESHOLD_CLAUDE = 0.3
SCORE_THRESHOLD_VISA = 0.5      

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  

LLM_TEMPERATURE = 0
MAX_TOKENS_RESPONSE = 512   # responses are 150-300 tokens in practice; 512 is safe headroom
