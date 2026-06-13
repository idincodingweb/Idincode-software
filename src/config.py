# src/config.py — pastikan ada:
import os

OUTPUT_DIR = "output"

TIER_CONFIGS = [
    {
        "filename": "leads_starter.csv",
        "min_score": 0.50,
        "limit": 25,
        "label": "STARTER ($19)",
    },
    {
        "filename": "leads_pro.csv",
        "min_score": 0.70,
        "limit": 100,
        "label": "PRO ($79)",
    },
    {
        "filename": "leads_premium_gold.csv",
        "min_score": 0.85,
        "limit": 50,
        "label": "PREMIUM GOLD ($199)",
    },
]

# kie.ai
IDINCODE_API = os.getenv("IDINCODE_API", "")
KIE_AI_BASE_URL = os.getenv("KIE_AI_BASE_URL", "https://api.kie.ai")
KIE_AI_MESSAGES_PATH = "/claude/v1/messages"
KIE_AI_MODEL = os.getenv("KIE_AI_MODEL", "claude-sonnet-4-5")
KIE_AI_THINKING = False

# PageSpeed
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")
