"""
NEET 2027 Accountability RPG Discord Bot (Single-File Architecture)
==================================================================
A private, single-user gamified study RPG bot built with discord.py (v2.7+)
and Neon PostgreSQL (`DATABASE_URL` via `psycopg`), tailored for NEET 2027
preparation with a syllabus mastery deadline of December 15, 2026 (Asia/Kolkata IST).

Includes:
- Persistent Neon PostgreSQL cloud database (`DATABASE_URL` with SSL) so all
  XP, levels, streaks, 72 NEET 2027 chapters, topic queue, recovery quests,
  custom daily goals, and mistake vault survive Render restarts and redeploys.
- Strict single-user lock (ALLOWED_USER_ID), GUILD_ID slash sync + hard HTTP
  global command wipe + /sync_commands utility, and Asia/Kolkata (IST)
  timekeeping with a 5:00 AM IST study-day reset and 4:55 AM IST daily audit.
- Customizable daily targets via /set_goal (minutes, Bio/Phy/Chem MCQs, total
  MCQs, topics) persisted in PostgreSQL.
- Automated World Builder (/setup_world) with private channels, roles, and
  live-updating pinned embeds (#quest-board, #status-window, #boss-raids,
  #codex-topics).
- Pre-seeded NEET 2027-only chapters across Class 11 & 12 (Physics, Chemistry,
  Biology), 5-stage revision checklist, /chapters browser, /chapter_toggle,
  and Notes-Only Trap detection.
- Chapter Boss Raids (/boss add, /boss list), Mistake Vault & Cursed Raids
  (/curse add, /curse raid, /curse cleanse, /test_log), Reward Shop & Rest
  Shields (/shop, /buy, /use, /shield), Exam Mode (/exam_mode), 7-day
  Subject/Habit Balance tracker, Next-Topic Queue (/queue_add, /next_topic),
  24-hour Streak Recovery Quests (/redeem), and Weekly Reports (/weekly_report
  + Sunday 21:00 IST auto-post).
- Gemini API integration (google-genai SDK with structured Pydantic JSON
  schemas) for daily MCQ target & pace enforcement, /gemini_quiz (10-20
  NEET MCQs with interactive buttons & DEX XP), and /gemini_analyze
  (Strength & Focus areas, #cursed-vault logging, and WIS XP).
- Render + UptimeRobot HTTP keep-alive server supporting both HEAD and GET.
"""

import os
import json
import logging
import traceback
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List, Tuple, Literal

import psycopg
from psycopg.rows import dict_row
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors

    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
    genai_errors = None
    GENAI_AVAILABLE = False

# ==============================================================================
# 1. ENVIRONMENT, LOGGING & CONSTANTS
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("StudyRPG")

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID_RAW = (os.getenv("GUILD_ID") or "").strip()
ALLOWED_USER_ID_RAW = (os.getenv("ALLOWED_USER_ID") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()

missing_vars = [
    name
    for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("GUILD_ID", GUILD_ID_RAW),
        ("ALLOWED_USER_ID", ALLOWED_USER_ID_RAW),
        ("DATABASE_URL", DATABASE_URL),
    ]
    if not val
]
if missing_vars:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(missing_vars)}. "
        "Set them in your environment or .env file."
    )

GUILD_ID = int(GUILD_ID_RAW)
ALLOWED_USER_ID = int(ALLOWED_USER_ID_RAW)

# Timezone & Target Constants
IST = ZoneInfo("Asia/Kolkata")
TARGET_MASTERY_DATE = date(2026, 12, 15)
TOTAL_SYLLABUS_TOPICS = 360  # 72 NEET 2027 chapters * 5 stages = 360 mastery stages
BOSS_HP_PER_TOPIC = 100

# Default Daily Quest Targets (Resets at 05:00 AM IST / Audited at 04:55 AM IST)
DAILY_RESET_HOUR = 5
DAILY_AUDIT_HOUR = 4
DAILY_AUDIT_MINUTE = 55
DAILY_TARGET_MINUTES = 360     # 6 Hours -> END Stat
DAILY_TARGET_BIO = 360         # Default Bio split toward 600+ daily MCQ target
DAILY_TARGET_PHY = 140         # Default Physics split toward 600+ daily MCQ target
DAILY_TARGET_CHEM = 100        # Default Chem split toward 600+ daily MCQ target
DAILY_TARGET_TOTAL_MCQS = 600  # 600+ Daily MCQ Target across Bio + Physics + Chem
DAILY_TARGET_TOPICS = 1        # >= 1 Topic Concept/Revision -> INT / WIS Stat

# RPG Progression Ranks
RANK_TIERS: List[Tuple[int, str, int]] = [
    (1, "E-Rank Novice", 0x95A5A6),
    (5, "D-Rank Apprentice", 0x2ECC71),
    (12, "C-Rank Aspirant", 0x3498DB),
    (22, "B-Rank Vanguard", 0x9B59B6),
    (35, "A-Rank Grandmaster", 0xE67E22),
    (50, "S-Rank NEET Sovereign", 0xF1C40F),
]
FATIGUED_ROLE_NAME = "⚠️ Fatigued"

# World Architecture Blueprint
WORLD_STRUCTURE = {
    "⚔️ COMMAND CITADEL": [
        ("quest-board", "text"),
        ("action-terminal", "text"),
        ("focus-dungeon", "voice"),
    ],
    "📊 HUNTER PROFILE": [
        ("status-window", "text"),
        ("boss-raids", "text"),
        ("inventory-and-shop", "text"),
    ],
    "📜 ARCHIVES & GRIMOIRE": [
        ("battle-logs", "text"),
        ("cursed-vault", "text"),
        ("codex-topics", "text"),
    ],
    "💀 JUDGMENT HALL": [
        ("midnight-tribunal", "text"),
    ],
}

# Pre-Seeded NEET 2027-Only Chapters (Class 11 & 12 across Physics, Chemistry, Biology)
NEET_2027_CHAPTERS: List[Tuple[str, int, str]] = [
    # Physics — Class 11 (10)
    ("Physics", 11, "Units and Measurements"),
    ("Physics", 11, "Kinematics"),
    ("Physics", 11, "Laws of Motion"),
    ("Physics", 11, "Work, Energy and Power"),
    ("Physics", 11, "Rotational Motion"),
    ("Physics", 11, "Gravitation"),
    ("Physics", 11, "Properties of Solids and Liquids"),
    ("Physics", 11, "Thermodynamics"),
    ("Physics", 11, "Kinetic Theory of Gases"),
    ("Physics", 11, "Oscillations and Waves"),
    # Physics — Class 12 (10)
    ("Physics", 12, "Electrostatics"),
    ("Physics", 12, "Current Electricity"),
    ("Physics", 12, "Magnetic Effects of Current and Magnetism"),
    ("Physics", 12, "Electromagnetic Induction and Alternating Currents"),
    ("Physics", 12, "Electromagnetic Waves"),
    ("Physics", 12, "Optics (Ray & Wave)"),
    ("Physics", 12, "Dual Nature of Matter and Radiation"),
    ("Physics", 12, "Atoms and Nuclei"),
    ("Physics", 12, "Electronic Devices"),
    ("Physics", 12, "Experimental Skills"),
    # Chemistry — Class 11 (9)
    ("Chemistry", 11, "Some Basic Concepts of Chemistry"),
    ("Chemistry", 11, "Structure of Atom"),
    ("Chemistry", 11, "Classification of Elements and Periodicity in Properties"),
    ("Chemistry", 11, "Chemical Bonding and Molecular Structure"),
    ("Chemistry", 11, "Chemical Thermodynamics"),
    ("Chemistry", 11, "Equilibrium"),
    ("Chemistry", 11, "Redox Reactions"),
    ("Chemistry", 11, "Organic Chemistry: Some Basic Principles and Techniques"),
    ("Chemistry", 11, "Hydrocarbons"),
    # Chemistry — Class 12 (11)
    ("Chemistry", 12, "Solutions"),
    ("Chemistry", 12, "Electrochemistry"),
    ("Chemistry", 12, "Chemical Kinetics"),
    ("Chemistry", 12, "d- and f-Block Elements"),
    ("Chemistry", 12, "Coordination Compounds"),
    ("Chemistry", 12, "Haloalkanes and Haloarenes"),
    ("Chemistry", 12, "Alcohols, Phenols and Ethers"),
    ("Chemistry", 12, "Aldehydes, Ketones and Carboxylic Acids"),
    ("Chemistry", 12, "Amines"),
    ("Chemistry", 12, "Biomolecules"),
    ("Chemistry", 12, "Principles Related to Practical Chemistry"),
    # Biology — Class 11 (19)
    ("Biology", 11, "The Living World"),
    ("Biology", 11, "Biological Classification"),
    ("Biology", 11, "Plant Kingdom"),
    ("Biology", 11, "Animal Kingdom"),
    ("Biology", 11, "Morphology of Flowering Plants"),
    ("Biology", 11, "Anatomy of Flowering Plants"),
    ("Biology", 11, "Structural Organisation in Animals"),
    ("Biology", 11, "Cell: The Unit of Life"),
    ("Biology", 11, "Biomolecules"),
    ("Biology", 11, "Cell Cycle and Cell Division"),
    ("Biology", 11, "Photosynthesis in Higher Plants"),
    ("Biology", 11, "Respiration in Plants"),
    ("Biology", 11, "Plant Growth and Development"),
    ("Biology", 11, "Breathing and Exchange of Gases"),
    ("Biology", 11, "Body Fluids and Circulation"),
    ("Biology", 11, "Excretory Products and their Elimination"),
    ("Biology", 11, "Locomotion and Movement"),
    ("Biology", 11, "Neural Control and Coordination"),
    ("Biology", 11, "Chemical Coordination and Integration"),
    # Biology — Class 12 (13)
    ("Biology", 12, "Sexual Reproduction in Flowering Plants"),
    ("Biology", 12, "Human Reproduction"),
    ("Biology", 12, "Reproductive Health"),
    ("Biology", 12, "Principles of Inheritance and Variation"),
    ("Biology", 12, "Molecular Basis of Inheritance"),
    ("Biology", 12, "Evolution"),
    ("Biology", 12, "Human Health and Diseases"),
    ("Biology", 12, "Microbes in Human Welfare"),
    ("Biology", 12, "Biotechnology: Principles and Processes"),
    ("Biology", 12, "Biotechnology and its Applications"),
    ("Biology", 12, "Organisms and Populations"),
    ("Biology", 12, "Ecosystem"),
    ("Biology", 12, "Biodiversity and Conservation"),
]

STAGE_KEYS = [
    "short_notes",
    "topics_done",
    "mcqs_done",
    "mistakes_logged",
    "revised",
]
STAGE_SHORT_LABELS = {
    "short_notes": "Notes",
    "topics_done": "Topics",
    "mcqs_done": "MCQs",
    "mistakes_logged": "Mistakes",
    "revised": "Revised",
}


def now_ist() -> datetime:
    """Returns the current timezone-aware datetime in Asia/Kolkata (IST)."""
    return datetime.now(IST)


def study_day_ist_date(dt: Optional[datetime] = None) -> date:
    """
    Returns the active study-day date in IST where a new day begins at 05:00 AM IST
    (so 12:00 AM – 04:59 AM IST still counts toward the active study day).
    """
    current = dt or now_ist()
    return (current - timedelta(hours=DAILY_RESET_HOUR)).date()


def today_ist_str() -> str:
    """Returns today's 5:00 AM-anchored study-day string (YYYY-MM-DD) in IST."""
    return study_day_ist_date().isoformat()


def next_audit_datetime_ist(dt: Optional[datetime] = None) -> datetime:
    """Returns the next 04:55 AM IST audit timestamp."""
    current = dt or now_ist()
    audit_dt = datetime.combine(
        current.date(),
        time(hour=DAILY_AUDIT_HOUR, minute=DAILY_AUDIT_MINUTE),
        tzinfo=IST,
    )
    if current >= audit_dt:
        audit_dt += timedelta(days=1)
    return audit_dt


def xp_for_next_level(level: int) -> int:
    """Calculates XP required to advance from `level` to `level + 1`."""
    return 500 + (level - 1) * 250


def build_progress_bar(current: int, maximum: int, length: int = 14) -> str:
    """Renders a sleek Unicode RPG progress bar."""
    if maximum <= 0:
        maximum = 1
    ratio = max(0.0, min(1.0, current / maximum))
    filled = int(round(ratio * length))
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {int(ratio * 100)}%"


def build_stage_bar(completed_stages: int) -> str:
    """Renders a 5-block mastery bar for the 5-stage chapter checklist."""
    done = max(0, min(5, completed_stages))
    return f"[{'🟩' * done}{'⬜' * (5 - done)}]"


def get_rank_for_level(level: int) -> str:
    """Determines the Hunter Rank title for a given character level."""
    current_rank = RANK_TIERS[0][1]
    for req_lvl, rank_name, _ in RANK_TIERS:
        if level >= req_lvl:
            current_rank = rank_name
    return current_rank


def normalize_subject_key(subject: str) -> str:
    """Maps subject strings like 'Biology'/'Bio', 'Physics'/'Phy', 'Chemistry'/'Chem' to canonical keys."""
    s = subject.strip().lower()
    if s.startswith("bio"):
        return "Bio"
    if s.startswith("phy"):
        return "Physics"
    if s.startswith("chem"):
        return "Chem"
    return subject.strip().capitalize()


# ==============================================================================
# 2. PYDANTIC SCHEMAS & GEMINI AI COACH
# ==============================================================================


class QuizQuestionItem(BaseModel):
    question_number: int = Field(..., description="1-based question index")
    question: str = Field(..., description="Concise NEET 2027 single-choice question aligned with NCERT")
    options: List[str] = Field(
        ...,
        min_length=4,
        max_length=4,
        description="Four options prefixed as 'A) ...', 'B) ...', 'C) ...', 'D) ...'",
    )
    correct_option: Literal["A", "B", "C", "D"] = Field(
        ..., description="Single correct option letter: A, B, C, or D"
    )
    subtopic_tag: str = Field(..., description="Specific NEET/NCERT subtopic tag")
    explanation: str = Field(..., description="Brief, direct explanation referencing NCERT/NEET logic")


class QuizSetPayload(BaseModel):
    subject: str
    chapter: str
    questions: List[QuizQuestionItem]


class DiagnosticReportPayload(BaseModel):
    strength_areas: List[str] = Field(
        ..., description="Subtopics or concepts demonstrated accurately"
    )
    focus_areas: List[str] = Field(
        ..., description="Weak subtopics, traps, or NCERT points needing immediate work"
    )
    action_step: str = Field(
        ..., description="One direct, matter-of-fact revision or MCQ practice step"
    )
    accuracy_summary: str = Field(
        ..., description="Concise 1-2 sentence diagnostic summary"
    )


class GeminiStudyCoach:
    """
    Wrapper around the official `google-genai` SDK for NEET 2027 MCQ generation
    and post-practice / post-test strength & focus diagnostics.
    """

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self._client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options={"api_version": "v1beta"},
                )
            except TypeError:
                self._client = genai.Client(api_key=self.api_key)

    @property
    def is_configured(self) -> bool:
        return bool(GENAI_AVAILABLE and self.api_key and self._client is not None)

    async def _call_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: type[BaseModel],
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        if not GENAI_AVAILABLE:
            return None, "⚠️ `google-genai` or `pydantic` package is not installed in this environment."
        if not self.is_configured:
            return None, "⚠️ `GEMINI_API_KEY` is not set. Add it to your environment or `.env` to use Gemini commands."

        import asyncio

        combined_prompt = (
            f"Instructions:\n{system_instruction}\n\n"
            f"Task:\n{prompt}"
        )

        def _invoke_sync() -> BaseModel:
            cfg = genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0.45,
            )
            resp = self._client.models.generate_content(
                model=self.model_name,
                contents=combined_prompt,
                config=cfg,
            )
            if getattr(resp, "parsed", None) is not None:
                return resp.parsed
            raw_text = (resp.text or "").strip()
            return response_schema.model_validate_json(raw_text)

        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                parsed_obj = await asyncio.to_thread(_invoke_sync)
                return parsed_obj, None
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str.upper()
                if genai_errors is not None and isinstance(exc, genai_errors.APIError):
                    if getattr(exc, "code", None) == 429:
                        is_rate_limit = True
                if is_rate_limit and attempt == 0:
                    await asyncio.sleep(2.0)
                    continue
                if is_rate_limit:
                    return (
                        None,
                        "⏳ **Gemini Rate Limit / Quota Reached:** Too many requests or daily API quota hit. "
                        "Wait a moment and retry, or log manual MCQs with `/mcq`.",
                    )
                logger.error("Gemini structured call failed: %s", exc)
                return None, f"⚠️ **Gemini API Error:** `{err_str[:220]}`"

        return None, f"⚠️ **Gemini Request Failed:** `{last_err}`"


# ==============================================================================
# 3. NEON POSTGRESQL DATABASE MANAGER
# ==============================================================================


class DatabaseManager:
    """
    PostgreSQL persistence layer backed by Neon (`DATABASE_URL`) using `psycopg`.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._init_db()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _init_db(self) -> None:
        """Initializes all PostgreSQL tables, runs safe migrations, and seeds defaults."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Core Hunter Profile & Daily Quest Progress
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS hunter_profile (
                        user_id BIGINT PRIMARY KEY,
                        level INTEGER DEFAULT 1,
                        xp INTEGER DEFAULT 0,
                        hp INTEGER DEFAULT 100,
                        max_hp INTEGER DEFAULT 100,
                        gold INTEGER DEFAULT 0,
                        streak INTEGER DEFAULT 0,
                        stat_int INTEGER DEFAULT 10,
                        stat_end INTEGER DEFAULT 10,
                        stat_dex INTEGER DEFAULT 10,
                        stat_wis INTEGER DEFAULT 10,
                        daily_minutes INTEGER DEFAULT 0,
                        daily_bio_mcqs INTEGER DEFAULT 0,
                        daily_phy_mcqs INTEGER DEFAULT 0,
                        daily_chem_mcqs INTEGER DEFAULT 0,
                        daily_topics INTEGER DEFAULT 0,
                        daily_gold_earned INTEGER DEFAULT 0,
                        total_topics_cleared INTEGER DEFAULT 0,
                        is_fatigued INTEGER DEFAULT 0,
                        active_shield_date TEXT,
                        last_reset_date TEXT,
                        last_audit_date TEXT,
                        exam_mode_active INTEGER DEFAULT 0,
                        exam_mode_test_name TEXT,
                        exam_mode_started_iso TEXT
                    )
                """)

                migrations = [
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS daily_gold_earned INTEGER DEFAULT 0",
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS active_shield_date TEXT",
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS last_audit_date TEXT",
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS exam_mode_active INTEGER DEFAULT 0",
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS exam_mode_test_name TEXT",
                    "ALTER TABLE hunter_profile ADD COLUMN IF NOT EXISTS exam_mode_started_iso TEXT",
                ]
                for alter_sql in migrations:
                    cur.execute(alter_sql)

                # Discord World Config & Custom Goal Overrides (BIGINT for snowflakes & targets)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS world_config (
                        key TEXT PRIMARY KEY,
                        value BIGINT
                    )
                """)
                cur.execute("ALTER TABLE world_config ALTER COLUMN value TYPE BIGINT")

                # Active Focus Session State
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS active_focus (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        start_time_iso TEXT,
                        source TEXT
                    )
                """)

                # Chapter Bosses & Syllabus Topics Tables
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bosses (
                        id SERIAL PRIMARY KEY,
                        subject TEXT NOT NULL,
                        chapter TEXT NOT NULL,
                        total_topics INTEGER NOT NULL,
                        completed_topics INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'ACTIVE',
                        created_at TEXT
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS topics (
                        id SERIAL PRIMARY KEY,
                        boss_id INTEGER REFERENCES bosses(id),
                        topic_name TEXT NOT NULL,
                        concept_done INTEGER DEFAULT 0,
                        revision_done INTEGER DEFAULT 0
                    )
                """)

                # Pre-Seeded NEET 2027 Chapters & 5-Stage Revision Checklist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chapters (
                        id SERIAL PRIMARY KEY,
                        subject TEXT NOT NULL,
                        class_level INTEGER NOT NULL,
                        chapter_name TEXT NOT NULL UNIQUE,
                        short_notes INTEGER DEFAULT 0,
                        topics_done INTEGER DEFAULT 0,
                        mcqs_done INTEGER DEFAULT 0,
                        mistakes_logged INTEGER DEFAULT 0,
                        revised INTEGER DEFAULT 0,
                        notes_only_since TEXT,
                        boss_id INTEGER,
                        updated_at TEXT
                    )
                """)

                # Rolling 7-Day Telemetry Ledger
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS daily_activity_log (
                        log_date TEXT PRIMARY KEY,
                        study_minutes INTEGER DEFAULT 0,
                        bio_mcqs INTEGER DEFAULT 0,
                        phy_mcqs INTEGER DEFAULT 0,
                        chem_mcqs INTEGER DEFAULT 0,
                        concept_count INTEGER DEFAULT 0,
                        execution_count INTEGER DEFAULT 0,
                        tests_logged INTEGER DEFAULT 0,
                        exam_mode_minutes INTEGER DEFAULT 0
                    )
                """)

                # Next-Topic Queue
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS topic_queue (
                        id SERIAL PRIMARY KEY,
                        subject TEXT NOT NULL,
                        chapter_id INTEGER,
                        chapter_name TEXT NOT NULL,
                        topic_name TEXT NOT NULL,
                        priority INTEGER DEFAULT 2,
                        status TEXT DEFAULT 'PENDING',
                        created_at TEXT,
                        completed_at TEXT
                    )
                """)

                # Streak Recovery Quest (24h window on streak break)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS recovery_quests (
                        id SERIAL PRIMARY KEY,
                        triggered_date TEXT NOT NULL,
                        expires_at_iso TEXT NOT NULL,
                        req_minutes INTEGER DEFAULT 420,
                        req_mcqs INTEGER DEFAULT 600,
                        req_curses_cleansed INTEGER DEFAULT 3,
                        prog_minutes INTEGER DEFAULT 0,
                        prog_mcqs INTEGER DEFAULT 0,
                        prog_curses_cleansed INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'ACTIVE'
                    )
                """)

                # Gemini Quiz Sessions
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS gemini_quiz_sessions (
                        id SERIAL PRIMARY KEY,
                        subject TEXT NOT NULL,
                        chapter TEXT NOT NULL,
                        focus_topic TEXT,
                        questions_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                # Legacy Topic Logs
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS topic_logs (
                        id SERIAL PRIMARY KEY,
                        subject TEXT,
                        chapter TEXT,
                        topic TEXT,
                        mode TEXT,
                        logged_at TEXT
                    )
                """)

                # Mistake Vault & Curses Table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS curses (
                        id SERIAL PRIMARY KEY,
                        subject TEXT NOT NULL,
                        topic_or_note TEXT NOT NULL,
                        source_test TEXT DEFAULT 'Manual Entry',
                        cleared INTEGER DEFAULT 0,
                        created_at TEXT
                    )
                """)

                # Shop Items, Inventory & Shield Usage History
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS shop_items (
                        id SERIAL PRIMARY KEY,
                        item_key TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        price INTEGER NOT NULL,
                        category TEXT DEFAULT 'REWARD'
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        id SERIAL PRIMARY KEY,
                        item_key TEXT UNIQUE NOT NULL,
                        quantity INTEGER DEFAULT 0
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS shield_usage_log (
                        id SERIAL PRIMARY KEY,
                        used_at_iso TEXT NOT NULL,
                        covered_date TEXT NOT NULL
                    )
                """)

                # Test Logs History
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS test_logs (
                        id SERIAL PRIMARY KEY,
                        test_name TEXT,
                        score INTEGER,
                        max_score INTEGER,
                        percentage REAL,
                        logged_at TEXT
                    )
                """)

                # Seed default user if not exists
                cur.execute(
                    """
                    INSERT INTO hunter_profile (user_id, last_reset_date)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    (ALLOWED_USER_ID, today_ist_str()),
                )

                # Seed today's activity row
                cur.execute(
                    "INSERT INTO daily_activity_log (log_date) VALUES (%s) ON CONFLICT (log_date) DO NOTHING",
                    (today_ist_str(),),
                )

                # Seed NEET 2027-only chapters idempotently (no duplicates)
                now_iso = now_ist().isoformat()
                for subj, cls_lvl, chap_title in NEET_2027_CHAPTERS:
                    cur.execute(
                        """
                        INSERT INTO chapters (subject, class_level, chapter_name, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (chapter_name) DO NOTHING
                        """,
                        (subj, cls_lvl, chap_title, now_iso),
                    )

                # Seed default NEET-tailored Shop Items
                default_shop = [
                    (
                        "rest_shield",
                        "Rest / Post-Test Shield",
                        "Waives today's daily targets & preserves your streak until next 05:00 AM IST reset (Max 2 per rolling 7 days).",
                        450,
                        "SHIELD",
                    ),
                    (
                        "anime_episode",
                        "Anime / Show Episode Token",
                        "Redeem for 1 guilt-free episode (25-30 mins) after finishing your active study block.",
                        200,
                        "REWARD",
                    ),
                    (
                        "cheat_meal",
                        "Cheat Meal Voucher",
                        "Redeem for a favorite snack or cheat meal reward.",
                        500,
                        "REWARD",
                    ),
                    (
                        "break_block",
                        "Guilt-Free 1-Hour Break Block",
                        "Redeem for 60 minutes of complete off-screen rest or gaming.",
                        350,
                        "REWARD",
                    ),
                ]
                for item_key, name, desc, price, cat in default_shop:
                    cur.execute(
                        """
                        INSERT INTO shop_items (item_key, name, description, price, category)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (item_key) DO NOTHING
                        """,
                        (item_key, name, desc, price, cat),
                    )

            conn.commit()
        logger.info("Connected to Neon PostgreSQL and verified schema.")

    def _ensure_activity_row(self, conn: psycopg.Connection, log_date: str) -> None:
        conn.execute(
            "INSERT INTO daily_activity_log (log_date) VALUES (%s) ON CONFLICT (log_date) DO NOTHING",
            (log_date,),
        )

    # --- World Config & Daily Goals Helpers ---

    def set_config(self, key: str, value: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO world_config (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, int(value)),
            )
            conn.commit()

    def get_config(self, key: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM world_config WHERE key = %s", (key,)).fetchone()
            return int(row["value"]) if row else None

    def get_daily_goals(self) -> Dict[str, int]:
        """Returns active daily quest targets (custom overrides if set via /set_goal, else defaults)."""
        keys = {
            "minutes": ("goal_minutes", DAILY_TARGET_MINUTES),
            "bio_mcqs": ("goal_bio_mcqs", DAILY_TARGET_BIO),
            "phy_mcqs": ("goal_phy_mcqs", DAILY_TARGET_PHY),
            "chem_mcqs": ("goal_chem_mcqs", DAILY_TARGET_CHEM),
            "total_mcqs": ("goal_total_mcqs", DAILY_TARGET_TOTAL_MCQS),
            "topics": ("goal_topics", DAILY_TARGET_TOPICS),
        }
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM world_config WHERE key LIKE 'goal_%'"
            ).fetchall()
        overrides = {r["key"]: int(r["value"]) for r in rows}
        return {
            name: overrides.get(cfg_key, default_val)
            for name, (cfg_key, default_val) in keys.items()
        }

    # --- Profile & Stat Helpers ---

    def get_profile(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM hunter_profile WHERE user_id = %s", (ALLOWED_USER_ID,)
            ).fetchone()
            return dict(row)

    def set_exam_mode(self, active: bool, test_name: Optional[str] = None) -> Dict[str, Any]:
        with self._connect() as conn:
            if active:
                t_name = (test_name or "NEET Mock / Part Test").strip()
                conn.execute(
                    """
                    UPDATE hunter_profile
                    SET exam_mode_active = 1,
                        exam_mode_test_name = %s,
                        exam_mode_started_iso = %s
                    WHERE user_id = %s
                    """,
                    (t_name, now_ist().isoformat(), ALLOWED_USER_ID),
                )
            else:
                conn.execute(
                    """
                    UPDATE hunter_profile
                    SET exam_mode_active = 0,
                        exam_mode_test_name = NULL,
                        exam_mode_started_iso = NULL
                    WHERE user_id = %s
                    """,
                    (ALLOWED_USER_ID,),
                )
            conn.commit()
        return self.get_profile()

    def get_7day_balance(self) -> Dict[str, Any]:
        """
        Computes rolling 7-day metrics for Bio vs. Physics MCQs and Concept vs.
        Execution (MCQs + Revision) balance, flagging imbalances and XP nudge factors.
        """
        cutoff_date = (study_day_ist_date() - timedelta(days=6)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(study_minutes), 0) AS study_minutes,
                    COALESCE(SUM(bio_mcqs), 0) AS bio_mcqs,
                    COALESCE(SUM(phy_mcqs), 0) AS phy_mcqs,
                    COALESCE(SUM(chem_mcqs), 0) AS chem_mcqs,
                    COALESCE(SUM(concept_count), 0) AS concept_count,
                    COALESCE(SUM(execution_count), 0) AS execution_count,
                    COALESCE(SUM(tests_logged), 0) AS tests_logged,
                    COALESCE(SUM(exam_mode_minutes), 0) AS exam_mode_minutes
                FROM daily_activity_log
                WHERE log_date >= %s
                """,
                (cutoff_date,),
            ).fetchone()

        bio_7d = int(row["bio_mcqs"])
        phy_7d = int(row["phy_mcqs"])
        chem_7d = int(row["chem_mcqs"])
        concept_7d = int(row["concept_count"])
        exec_7d = int(row["execution_count"])

        alerts: List[str] = []
        nudge_bio = 1.0
        nudge_phy = 1.0
        nudge_concept = 1.0

        # Bio vs. Physics MCQ balance check
        if bio_7d >= 250 and phy_7d > 0 and (bio_7d / phy_7d) > 4.0:
            alerts.append(f"Heavy Bio MCQ skew ({bio_7d} Bio vs {phy_7d} Phy in 7d) — push Physics MCQs.")
            nudge_bio = 0.75
        elif bio_7d >= 300 and phy_7d == 0:
            alerts.append(f"Zero Physics MCQs in 7d vs {bio_7d} Bio MCQs — start Physics MCQ drills.")
            nudge_bio = 0.75

        if phy_7d >= 150 and bio_7d > 0 and (phy_7d / max(1, bio_7d)) > 1.5:
            alerts.append(f"Physics MCQ heavy ({phy_7d} Phy vs {bio_7d} Bio in 7d) — balance Bio volume.")
            nudge_phy = 0.85

        # Concept/Notes vs. Execution (MCQ/Revision) balance check
        if concept_7d >= 6 and exec_7d > 0 and (concept_7d / exec_7d) > 2.5:
            alerts.append(
                f"Concept/Notes outpacing execution ({concept_7d} notes/concepts vs {exec_7d} practice/revision) — convert to MCQs & revision."
            )
            nudge_concept = 0.75

        is_balanced = len(alerts) == 0
        return {
            "study_minutes": int(row["study_minutes"]),
            "bio_mcqs": bio_7d,
            "phy_mcqs": phy_7d,
            "chem_mcqs": chem_7d,
            "concept_count": concept_7d,
            "execution_count": exec_7d,
            "tests_logged": int(row["tests_logged"]),
            "exam_mode_minutes": int(row["exam_mode_minutes"]),
            "is_balanced": is_balanced,
            "alerts": alerts,
            "nudge_bio": nudge_bio,
            "nudge_phy": nudge_phy,
            "nudge_concept": nudge_concept,
        }

    def award_rewards(
        self,
        xp_gain: int = 0,
        gold_gain: int = 0,
        d_int: int = 0,
        d_end: int = 0,
        d_dex: int = 0,
        d_wis: int = 0,
        d_minutes: int = 0,
        d_bio: int = 0,
        d_phy: int = 0,
        d_chem: int = 0,
        d_topics: int = 0,
        d_concept_events: int = 0,
        d_exec_events: int = 0,
        d_tests: int = 0,
        heal_hp: int = 0,
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Atomically updates XP, Gold, Core Stats, Daily Quests, Rolling 7-Day
        Telemetry, Active Recovery Quest progress, and Level-Ups.
        """
        prof = self.get_profile()
        today_str = today_ist_str()
        exam_active = bool(prof.get("exam_mode_active"))

        actual_xp = max(0, int(xp_gain))
        new_xp = prof["xp"] + actual_xp
        new_level = prof["level"]
        leveled_up = False

        while new_xp >= xp_for_next_level(new_level):
            new_xp -= xp_for_next_level(new_level)
            new_level += 1
            leveled_up = True

        new_max_hp = 100 + (new_level - 1) * 10
        new_hp = min(new_max_hp, prof["hp"] + heal_hp + (25 if leveled_up else 0))

        total_new_mcqs = max(0, d_bio) + max(0, d_phy) + max(0, d_chem)
        exam_mins_add = max(0, d_minutes) if exam_active else 0

        with self._connect() as conn:
            self._ensure_activity_row(conn, today_str)
            conn.execute(
                """
                UPDATE hunter_profile
                SET level = %s,
                    xp = %s,
                    hp = %s,
                    max_hp = %s,
                    gold = gold + %s,
                    daily_gold_earned = daily_gold_earned + %s,
                    stat_int = stat_int + %s,
                    stat_end = stat_end + %s,
                    stat_dex = stat_dex + %s,
                    stat_wis = stat_wis + %s,
                    daily_minutes = daily_minutes + %s,
                    daily_bio_mcqs = daily_bio_mcqs + %s,
                    daily_phy_mcqs = daily_phy_mcqs + %s,
                    daily_chem_mcqs = daily_chem_mcqs + %s,
                    daily_topics = daily_topics + %s,
                    total_topics_cleared = total_topics_cleared + %s
                WHERE user_id = %s
                """,
                (
                    new_level,
                    new_xp,
                    new_hp,
                    new_max_hp,
                    gold_gain,
                    max(0, gold_gain),
                    d_int,
                    d_end,
                    d_dex,
                    d_wis,
                    d_minutes,
                    d_bio,
                    d_phy,
                    d_chem,
                    d_topics,
                    d_topics,
                    ALLOWED_USER_ID,
                ),
            )

            conn.execute(
                """
                UPDATE daily_activity_log
                SET study_minutes = study_minutes + %s,
                    bio_mcqs = bio_mcqs + %s,
                    phy_mcqs = phy_mcqs + %s,
                    chem_mcqs = chem_mcqs + %s,
                    concept_count = concept_count + %s,
                    execution_count = execution_count + %s,
                    tests_logged = tests_logged + %s,
                    exam_mode_minutes = exam_mode_minutes + %s
                WHERE log_date = %s
                """,
                (
                    max(0, d_minutes),
                    max(0, d_bio),
                    max(0, d_phy),
                    max(0, d_chem),
                    max(0, d_concept_events),
                    max(0, d_exec_events),
                    max(0, d_tests),
                    exam_mins_add,
                    today_str,
                ),
            )

            # Update active 24h recovery quest progress if not expired
            active_rq = conn.execute(
                "SELECT * FROM recovery_quests WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active_rq:
                exp_dt = datetime.fromisoformat(active_rq["expires_at_iso"])
                if now_ist() <= exp_dt:
                    conn.execute(
                        """
                        UPDATE recovery_quests
                        SET prog_minutes = prog_minutes + %s,
                            prog_mcqs = prog_mcqs + %s
                        WHERE id = %s
                        """,
                        (max(0, d_minutes), total_new_mcqs, active_rq["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE recovery_quests SET status = 'EXPIRED' WHERE id = %s",
                        (active_rq["id"],),
                    )

            conn.commit()

        return self.get_profile(), leveled_up

    # --- Focus Session Management ---

    def get_active_focus(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM active_focus WHERE id = 1").fetchone()
            return dict(row) if row else None

    def start_focus(self, source: str = "Manual") -> Tuple[bool, str]:
        existing = self.get_active_focus()
        if existing:
            return False, existing["start_time_iso"]
        iso_now = now_ist().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO active_focus (id, start_time_iso, source) VALUES (1, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET start_time_iso = EXCLUDED.start_time_iso, source = EXCLUDED.source",
                (iso_now, source),
            )
            conn.commit()
        return True, iso_now

    def stop_focus(self) -> Optional[Tuple[int, str]]:
        existing = self.get_active_focus()
        if not existing:
            return None
        start_dt = datetime.fromisoformat(existing["start_time_iso"])
        elapsed_minutes = max(0, int((now_ist() - start_dt).total_seconds() // 60))
        source = existing["source"] or "Focus"
        with self._connect() as conn:
            conn.execute("DELETE FROM active_focus WHERE id = 1")
            conn.commit()
        return elapsed_minutes, source

    # --- Pre-Seeded NEET 2027 Chapters & 5-Stage Checklist ---

    def get_filtered_chapters(
        self,
        subject_filter: str = "ALL",
        class_filter: Optional[int] = None,
        traps_only: bool = False,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM chapters WHERE 1=1"
        params: List[Any] = []

        if subject_filter and subject_filter.upper() != "ALL":
            subj_canonical = {
                "bio": "Biology",
                "biology": "Biology",
                "phy": "Physics",
                "physics": "Physics",
                "chem": "Chemistry",
                "chemistry": "Chemistry",
            }.get(subject_filter.strip().lower(), subject_filter.strip())
            sql += " AND LOWER(subject) = LOWER(%s)"
            params.append(subj_canonical)

        if class_filter in (11, 12):
            sql += " AND class_level = %s"
            params.append(class_filter)

        sql += " ORDER BY id ASC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        if traps_only:
            rows = [r for r in rows if self.is_notes_only_trap(r)]
        return rows

    def is_notes_only_trap(self, chap_row: Dict[str, Any]) -> bool:
        """
        Flags a chapter as a Notes-Only Trap when short_notes is done while
        mcqs_done and revised are incomplete for >= 2 days (or immediately if marked).
        """
        if not chap_row.get("short_notes"):
            return False
        if chap_row.get("mcqs_done") or chap_row.get("revised"):
            return False
        since_iso = chap_row.get("notes_only_since")
        if not since_iso:
            return True
        try:
            since_dt = datetime.fromisoformat(since_iso)
            return (now_ist() - since_dt).total_seconds() >= 2 * 86400
        except ValueError:
            return True

    def add_custom_chapter(self, subject: str, class_level: int, chapter_name: str) -> Tuple[bool, str]:
        subj_canonical = {
            "bio": "Biology",
            "biology": "Biology",
            "phy": "Physics",
            "physics": "Physics",
            "chem": "Chemistry",
            "chemistry": "Chemistry",
        }.get(subject.strip().lower(), subject.strip().capitalize())
        title = chapter_name.strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO chapters (subject, class_level, chapter_name, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chapter_name) DO NOTHING
                """,
                (subj_canonical, int(class_level), title, now_ist().isoformat()),
            )
            conn.commit()
            if cur.rowcount == 0:
                return False, f"⚠️ Chapter **{title}** already exists in the tracker."
        return True, f"✅ Added **{title}** (`{subj_canonical}` • Class `{class_level}`) to the NEET 2027 tracker."

    def search_chapter_names(self, query: str) -> List[Dict[str, Any]]:
        q = f"%{query.strip().lower()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chapters
                WHERE LOWER(chapter_name) LIKE %s OR LOWER(subject) LIKE %s
                ORDER BY id ASC
                LIMIT 25
                """,
                (q, q),
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_chapter(self, query: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """
        Resolves a chapter by exact case-insensitive match first, then unique substring match.
        Returns `(matched_row_or_None, candidate_labels)`.
        """
        clean = query.strip()
        with self._connect() as conn:
            exact = conn.execute(
                "SELECT * FROM chapters WHERE LOWER(chapter_name) = LOWER(%s)", (clean,)
            ).fetchone()
            if exact:
                return dict(exact), []

            matches = conn.execute(
                "SELECT * FROM chapters WHERE LOWER(chapter_name) LIKE %s ORDER BY id ASC",
                (f"%{clean.lower()}%",),
            ).fetchall()
            match_list = [dict(m) for m in matches]
            if len(match_list) == 1:
                return match_list[0], []

            labels = [
                f"{m['chapter_name']} ({m['subject']} Cl {m['class_level']})"
                for m in match_list[:10]
            ]
            return None, labels

    def toggle_chapter_stage(
        self, chapter_query: str, stage_key: str
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Flips one of the 5 stages (`short_notes`, `topics_done`, `mcqs_done`,
        `mistakes_logged`, `revised`) on a chapter, syncs its linked Chapter Boss
        (20% HP per stage), and returns `(ok, message, meta_dict)`.
        """
        if stage_key not in STAGE_KEYS:
            return False, f"❌ Invalid stage `{stage_key}`.", None

        chap, candidates = self.resolve_chapter(chapter_query)
        if not chap:
            if candidates:
                hint = ", ".join(f"`{c}`" for c in candidates[:6])
                return False, f"🔍 Multiple chapters match `{chapter_query}`: {hint}. Be more specific.", None
            return False, f"❌ Chapter `{chapter_query}` not found in NEET 2027 tracker.", None

        now_iso = now_ist().isoformat()
        with self._connect() as conn:
            fresh = dict(
                conn.execute("SELECT * FROM chapters WHERE id = %s", (chap["id"],)).fetchone()
            )
            new_val = 0 if int(fresh[stage_key]) else 1

            notes_only_since = fresh.get("notes_only_since")
            if stage_key == "short_notes":
                if new_val == 1 and not fresh.get("mcqs_done") and not fresh.get("revised"):
                    notes_only_since = notes_only_since or now_iso
                elif new_val == 0:
                    notes_only_since = None
            elif stage_key in ("mcqs_done", "revised") and new_val == 1:
                notes_only_since = None

            conn.execute(
                f"""
                UPDATE chapters
                SET {stage_key} = %s,
                    notes_only_since = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (new_val, notes_only_since, now_iso, fresh["id"]),
            )

            updated = dict(
                conn.execute("SELECT * FROM chapters WHERE id = %s", (fresh["id"],)).fetchone()
            )
            stages_done = sum(int(updated[k]) for k in STAGE_KEYS)
            pct = stages_done * 20

            # Sync linked Chapter Boss (5 stages -> 5 topics = 500 HP)
            boss_id = updated.get("boss_id")
            if not boss_id:
                existing_boss = conn.execute(
                    "SELECT id FROM bosses WHERE LOWER(subject) = LOWER(%s) AND LOWER(chapter) = LOWER(%s)",
                    (updated["subject"], updated["chapter_name"]),
                ).fetchone()
                if existing_boss:
                    boss_id = existing_boss["id"]
                else:
                    b_cur = conn.execute(
                        """
                        INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                        VALUES (%s, %s, 5, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            updated["subject"],
                            updated["chapter_name"],
                            stages_done,
                            "DEFEATED" if stages_done >= 5 else "ACTIVE",
                            now_iso,
                        ),
                    ).fetchone()
                    boss_id = b_cur["id"]
                conn.execute("UPDATE chapters SET boss_id = %s WHERE id = %s", (boss_id, fresh["id"]))

            boss_row = dict(conn.execute("SELECT * FROM bosses WHERE id = %s", (boss_id,)).fetchone())
            was_defeated = boss_row["status"] == "DEFEATED"
            new_comp = min(boss_row["total_topics"], stages_done)
            new_status = "DEFEATED" if new_comp >= boss_row["total_topics"] else "ACTIVE"
            just_defeated = (not was_defeated) and (new_status == "DEFEATED")

            conn.execute(
                "UPDATE bosses SET completed_topics = %s, status = %s WHERE id = %s",
                (new_comp, new_status, boss_id),
            )
            conn.commit()

        rem_hp = max(0, boss_row["total_topics"] - new_comp) * BOSS_HP_PER_TOPIC
        max_hp = boss_row["total_topics"] * BOSS_HP_PER_TOPIC
        trap_flag = self.is_notes_only_trap(updated)

        return (
            True,
            f"{'✅' if new_val else '⬜'} **{updated['chapter_name']}** — "
            f"`{STAGE_SHORT_LABELS[stage_key]}` set to **{'DONE' if new_val else 'PENDING'}** "
            f"(`{pct}%` | Boss HP: `{rem_hp}/{max_hp}`)",
            {
                "chapter": updated,
                "turned_on": bool(new_val),
                "stage_key": stage_key,
                "stages_done": stages_done,
                "pct": pct,
                "just_defeated": just_defeated,
                "trap_flag": trap_flag,
                "boss_hp": f"{rem_hp}/{max_hp}",
            },
        )

    def get_syllabus_totals(self) -> Tuple[int, int]:
        """Returns `(completed_mastery_units, total_mastery_units)` across NEET 2027 chapters."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS chap_cnt,
                    COALESCE(SUM(short_notes + topics_done + mcqs_done + mistakes_logged + revised), 0) AS done_stages
                FROM chapters
                """
            ).fetchone()
            chap_cnt = int(row["chap_cnt"] or 0)
            done_stages = int(row["done_stages"] or 0)

        total_units = max(TOTAL_SYLLABUS_TOPICS, chap_cnt * 5)
        prof = self.get_profile()
        cleared_units = max(done_stages, int(prof["total_topics_cleared"]))
        return min(total_units, cleared_units), total_units

    def record_topic_and_damage_boss(
        self, subject: str, chapter: str, topic_name: str, mode: str
    ) -> Optional[Dict[str, Any]]:
        subj = subject.strip()
        chap = chapter.strip()
        top = topic_name.strip()
        is_concept = mode.lower() == "concept"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_logs (subject, chapter, topic, mode, logged_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (subj, chap, top, mode, now_ist().isoformat()),
            )

            boss = conn.execute(
                "SELECT * FROM bosses WHERE LOWER(subject) = LOWER(%s) AND LOWER(chapter) = LOWER(%s)",
                (subj, chap),
            ).fetchone()

            if not boss:
                ins = conn.execute(
                    """
                    INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                    VALUES (%s, %s, 5, 0, 'ACTIVE', %s)
                    RETURNING *
                    """,
                    (subj, chap, now_ist().isoformat()),
                ).fetchone()
                boss = ins

            boss_id = boss["id"]
            topic_row = conn.execute(
                "SELECT * FROM topics WHERE boss_id = %s AND LOWER(topic_name) = LOWER(%s)",
                (boss_id, top),
            ).fetchone()

            is_new_unique_topic = False
            if not topic_row:
                is_new_unique_topic = True
                conn.execute(
                    """
                    INSERT INTO topics (boss_id, topic_name, concept_done, revision_done)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (boss_id, top, 1 if is_concept else 0, 0 if is_concept else 1),
                )
            else:
                conn.execute(
                    """
                    UPDATE topics
                    SET concept_done = CASE WHEN %s THEN 1 ELSE concept_done END,
                        revision_done = CASE WHEN %s THEN 1 ELSE revision_done END
                    WHERE id = %s
                    """,
                    (is_concept, not is_concept, topic_row["id"]),
                )

            prev_completed = boss["completed_topics"]
            total_topics = boss["total_topics"]
            was_active = boss["status"] == "ACTIVE"

            new_completed = prev_completed
            if is_new_unique_topic or prev_completed < total_topics:
                new_completed = min(total_topics, prev_completed + 1)

            just_defeated = was_active and (new_completed >= total_topics)
            new_status = "DEFEATED" if new_completed >= total_topics else "ACTIVE"

            conn.execute(
                "UPDATE bosses SET completed_topics = %s, status = %s WHERE id = %s",
                (new_completed, new_status, boss_id),
            )
            conn.commit()

            rem_topics = max(0, total_topics - new_completed)
            return {
                "boss_id": boss_id,
                "subject": boss["subject"],
                "chapter": boss["chapter"],
                "completed_topics": new_completed,
                "total_topics": total_topics,
                "current_hp": rem_topics * BOSS_HP_PER_TOPIC,
                "max_hp": total_topics * BOSS_HP_PER_TOPIC,
                "just_defeated": just_defeated,
                "status": new_status,
            }

    # --- Next-Topic Queue ---

    def add_queue_topic(
        self, subject: str, chapter_query: str, topic_name: str, priority: int = 2
    ) -> Tuple[bool, str]:
        matched, _ = self.resolve_chapter(chapter_query)
        chap_id = matched["id"] if matched else None
        chap_title = matched["chapter_name"] if matched else chapter_query.strip()
        subj_key = normalize_subject_key(subject)
        prio = max(1, min(3, int(priority)))

        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO topic_queue (subject, chapter_id, chapter_name, topic_name, priority, status, created_at)
                VALUES (%s, %s, %s, %s, %s, 'PENDING', %s)
                RETURNING id
                """,
                (subj_key, chap_id, chap_title, topic_name.strip(), prio, now_ist().isoformat()),
            ).fetchone()
            conn.commit()
            qid = row["id"]

        return True, f"📌 Queued **[#{qid}] {subj_key} — {chap_title}: {topic_name.strip()}** (Priority `P{prio}`)"

    def get_next_pending_topic(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM topic_queue
                WHERE status = 'PENDING'
                ORDER BY priority ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None

    def get_pending_queue_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM topic_queue WHERE status = 'PENDING'").fetchone()
            return int(row["cnt"])

    def complete_queue_topic(self, queue_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_queue WHERE id = %s AND status = 'PENDING'", (queue_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE topic_queue SET status = 'COMPLETED', completed_at = %s WHERE id = %s",
                (now_ist().isoformat(), queue_id),
            )
            conn.commit()
            return dict(row)

    # --- Mistake Vault, Curses & Gemini Quiz Sessions ---

    def add_curse(self, subject: str, note: str, source_test: str = "Manual Entry") -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                VALUES (%s, %s, %s, 0, %s)
                RETURNING id
                """,
                (subject.strip(), note.strip(), source_test.strip(), now_ist().isoformat()),
            ).fetchone()
            conn.commit()
            return int(row["id"])

    def log_test_and_curses(
        self, test_name: str, score: int, max_score: int, wrong_topics: str
    ) -> List[Tuple[int, str]]:
        pct = round((score / max_score) * 100, 2) if max_score > 0 else 0.0
        parsed_curses = [
            t.strip()
            for t in wrong_topics.split(",")
            if t.strip() and t.strip().lower() not in ("none", "nil", "-")
        ]
        inserted: List[Tuple[int, str]] = []

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO test_logs (test_name, score, max_score, percentage, logged_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (test_name, score, max_score, pct, now_ist().isoformat()),
            )
            for item in parsed_curses:
                row = conn.execute(
                    """
                    INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                    VALUES (%s, %s, %s, 0, %s)
                    RETURNING id
                    """,
                    ("Mock Error", item, test_name, now_ist().isoformat()),
                ).fetchone()
                inserted.append((int(row["id"]), item))
            conn.commit()

        return inserted

    def get_uncleared_curses(self, limit: int = 15) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, subject, topic_or_note, source_test, created_at
                FROM curses
                WHERE cleared = 0
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_uncleared_curse_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM curses WHERE cleared = 0").fetchone()
            return int(row["cnt"])

    def cleanse_curse(self, curse_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM curses WHERE id = %s AND cleared = 0", (curse_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE curses SET cleared = 1 WHERE id = %s", (curse_id,))

            active_rq = conn.execute(
                "SELECT * FROM recovery_quests WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active_rq:
                exp_dt = datetime.fromisoformat(active_rq["expires_at_iso"])
                if now_ist() <= exp_dt:
                    conn.execute(
                        """
                        UPDATE recovery_quests
                        SET prog_curses_cleansed = prog_curses_cleansed + 1
                        WHERE id = %s
                        """,
                        (active_rq["id"],),
                    )
            conn.commit()
            return dict(row)

    def save_quiz_session(
        self,
        subject: str,
        chapter: str,
        focus_topic: Optional[str],
        questions_data: List[Dict[str, Any]],
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO gemini_quiz_sessions (subject, chapter, focus_topic, questions_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    subject,
                    chapter,
                    focus_topic,
                    json.dumps(questions_data, ensure_ascii=False),
                    now_ist().isoformat(),
                ),
            ).fetchone()
            conn.commit()
            return int(row["id"])

    def get_quiz_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gemini_quiz_sessions WHERE id = %s", (session_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["questions"] = json.loads(d.pop("questions_json"))
            return d

    def record_quiz_answer(self, session_id: int, q_idx: int, choice: str) -> Tuple[Optional[Dict[str, Any]], bool]:
        sess = self.get_quiz_session(session_id)
        if not sess:
            return None, False
        qs = sess["questions"]
        if q_idx < 0 or q_idx >= len(qs):
            return sess, False

        already_answered = qs[q_idx].get("user_answer") is not None
        qs[q_idx]["user_answer"] = choice
        qs[q_idx]["is_correct"] = choice == qs[q_idx].get("correct_option")

        with self._connect() as conn:
            conn.execute(
                "UPDATE gemini_quiz_sessions SET questions_json = %s WHERE id = %s",
                (json.dumps(qs, ensure_ascii=False), session_id),
            )
            conn.commit()

        sess["questions"] = qs
        return sess, (not already_answered)

    # --- Shop, Inventory & Rest Shields ---

    def get_shop_items(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM shop_items ORDER BY price ASC").fetchall()
            return [dict(r) for r in rows]

    def get_inventory(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.item_key, s.name, s.description, s.category, i.quantity
                FROM inventory i
                JOIN shop_items s ON i.item_key = s.item_key
                WHERE i.quantity > 0
                ORDER BY s.name ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def find_shop_item(self, query: str) -> Optional[Dict[str, Any]]:
        q = query.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM shop_items
                WHERE LOWER(item_key) = %s OR LOWER(name) LIKE %s
                LIMIT 1
                """,
                (q, f"%{q}%"),
            ).fetchone()
            return dict(row) if row else None

    def buy_shop_item(self, query: str) -> Tuple[bool, str]:
        item = self.find_shop_item(query)
        if not item:
            return False, f"❌ Item `{query}` not found in the Hunter Shop."

        prof = self.get_profile()
        if prof["gold"] < item["price"]:
            return (
                False,
                f"❌ Insufficient Gold! **{item['name']}** costs `{item['price']} G`, but you have `{prof['gold']} G`.",
            )

        with self._connect() as conn:
            conn.execute(
                "UPDATE hunter_profile SET gold = gold - %s WHERE user_id = %s",
                (item["price"], ALLOWED_USER_ID),
            )
            conn.execute(
                """
                INSERT INTO inventory (item_key, quantity) VALUES (%s, 1)
                ON CONFLICT (item_key) DO UPDATE SET quantity = inventory.quantity + 1
                """,
                (item["item_key"],),
            )
            conn.commit()

        return True, f"🛒 Purchased **{item['name']}** for `{item['price']} G`! Stored in your Inventory."

    def get_shields_used_last_7_days(self) -> int:
        cutoff = (now_ist() - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM shield_usage_log WHERE used_at_iso >= %s",
                (cutoff,),
            ).fetchone()
            return int(row["cnt"])

    def activate_rest_shield(self) -> Tuple[bool, str]:
        prof = self.get_profile()
        today_str = today_ist_str()

        if prof.get("active_shield_date") == today_str:
            return False, "🛡️ A **Rest / Post-Test Shield** is already active for this study day!"

        used_7d = self.get_shields_used_last_7_days()
        if used_7d >= 2:
            return (
                False,
                "⛔ **Shield Limit Reached:** You have already used **2 Rest Shields** in the rolling 7-day window.",
            )

        with self._connect() as conn:
            inv = conn.execute(
                "SELECT quantity FROM inventory WHERE item_key = 'rest_shield'"
            ).fetchone()
            if not inv or inv["quantity"] <= 0:
                return (
                    False,
                    "❌ You have **0 Rest / Post-Test Shields** in your inventory! Buy one via `/buy item:rest_shield` (`450 G`).",
                )

            conn.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE item_key = 'rest_shield'"
            )
            conn.execute(
                "UPDATE hunter_profile SET active_shield_date = %s WHERE user_id = %s",
                (today_str, ALLOWED_USER_ID),
            )
            conn.execute(
                "INSERT INTO shield_usage_log (used_at_iso, covered_date) VALUES (%s, %s)",
                (now_ist().isoformat(), today_str),
            )
            conn.commit()

        return (
            True,
            f"🛡️ **REST / POST-TEST SHIELD ACTIVATED!**\n"
            f"Study day `{today_str}` targets are waived, and your streak is protected until the `05:00 AM IST` reset.\n"
            f"*Rolling 7-day shields used: `{used_7d + 1} / 2`*",
        )

    def use_inventory_item(self, query: str) -> Tuple[bool, str]:
        item = self.find_shop_item(query)
        if not item:
            return False, f"❌ Item `{query}` does not exist."

        if item["item_key"] == "rest_shield":
            return self.activate_rest_shield()

        with self._connect() as conn:
            inv = conn.execute(
                "SELECT quantity FROM inventory WHERE item_key = %s", (item["item_key"],)
            ).fetchone()
            if not inv or inv["quantity"] <= 0:
                return False, f"❌ You don't have any **{item['name']}** in your inventory."

            conn.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE item_key = %s",
                (item["item_key"],),
            )
            conn.commit()

        return True, f"🎟️ Redeemed **{item['name']}**! Enjoy your earned reward—then return to the grind."

    # --- Streak Recovery Quest (/redeem) ---

    def get_active_recovery_quest(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recovery_quests WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            exp_dt = datetime.fromisoformat(d["expires_at_iso"])
            if now_ist() > exp_dt:
                conn.execute("UPDATE recovery_quests SET status = 'EXPIRED' WHERE id = %s", (d["id"],))
                conn.commit()
                return None
            return d

    def claim_recovery_quest(self) -> Tuple[bool, str]:
        rq = self.get_active_recovery_quest()
        if not rq:
            return False, "ℹ️ No active 24-hour Streak Recovery Quest right now."

        ok_mins = rq["prog_minutes"] >= rq["req_minutes"]
        ok_mcqs = rq["prog_mcqs"] >= rq["req_mcqs"]
        ok_curses = rq["prog_curses_cleansed"] >= rq["req_curses_cleansed"]

        if not (ok_mins and ok_mcqs and ok_curses):
            return (
                False,
                f"⏳ **Recovery Quest Incomplete:**\n"
                f"• Study Time: `{rq['prog_minutes']}/{rq['req_minutes']} mins`\n"
                f"• MCQs: `{rq['prog_mcqs']}/{rq['req_mcqs']}`\n"
                f"• Curses Cleansed: `{rq['prog_curses_cleansed']}/{rq['req_curses_cleansed']}`",
            )

        prof = self.get_profile()
        new_max_hp = prof["max_hp"]
        new_hp = min(new_max_hp, prof["hp"] + 35)
        restored_streak = max(1, prof["streak"])

        with self._connect() as conn:
            conn.execute(
                "UPDATE recovery_quests SET status = 'REDEEMED' WHERE id = %s",
                (rq["id"],),
            )
            conn.execute(
                """
                UPDATE hunter_profile
                SET hp = %s,
                    streak = %s,
                    is_fatigued = 0
                WHERE user_id = %s
                """,
                (new_hp, restored_streak, ALLOWED_USER_ID),
            )
            conn.commit()

        return (
            True,
            f"⛓️‍💥 **RECOVERY QUEST REDEEMED!**\n"
            f"• Restored `+35 HP` (`{new_hp}/{new_max_hp} HP`)\n"
            f"• Cleared `⚠️ Fatigued` slump penalty & set Streak to `🔥 {restored_streak} Day`",
        )

    # --- 04:55 AM IST Daily Audit & 05:00 AM IST Rollover ---

    def run_midnight_audit(self) -> Optional[Dict[str, Any]]:
        prof = self.get_profile()
        goals = self.get_daily_goals()
        today_str = today_ist_str()

        if prof.get("last_audit_date") == today_str:
            return None

        total_mcqs_today = (
            int(prof["daily_bio_mcqs"])
            + int(prof["daily_phy_mcqs"])
            + int(prof["daily_chem_mcqs"])
        )

        t_mins = goals["minutes"]
        t_bio = goals["bio_mcqs"]
        t_phy = goals["phy_mcqs"]
        t_chem = goals["chem_mcqs"]
        t_total_mcqs = goals["total_mcqs"]
        t_topics = goals["topics"]

        mins_ok = prof["daily_minutes"] >= t_mins
        bio_ok = prof["daily_bio_mcqs"] >= t_bio
        phy_ok = prof["daily_phy_mcqs"] >= t_phy
        chem_ok = prof["daily_chem_mcqs"] >= t_chem
        quota_total_ok = total_mcqs_today >= t_total_mcqs
        top_ok = prof["daily_topics"] >= t_topics

        missed_items: List[str] = []
        if not mins_ok:
            missed_items.append(
                f"• **Study Time:** `{prof['daily_minutes']} / {t_mins} mins` (Short by `{t_mins - prof['daily_minutes']} mins`)"
            )
        if not bio_ok:
            missed_items.append(
                f"• **Biology MCQs:** `{prof['daily_bio_mcqs']} / {t_bio}` (Short by `{t_bio - prof['daily_bio_mcqs']} MCQs`)"
            )
        if not phy_ok:
            missed_items.append(
                f"• **Physics MCQs:** `{prof['daily_phy_mcqs']} / {t_phy}` (Short by `{t_phy - prof['daily_phy_mcqs']} MCQs`)"
            )
        if not chem_ok:
            missed_items.append(
                f"• **Chemistry MCQs:** `{prof['daily_chem_mcqs']} / {t_chem}` (Short by `{t_chem - prof['daily_chem_mcqs']} MCQs`)"
            )
        if not quota_total_ok:
            missed_items.append(
                f"• **Daily MCQ Pace:** `{total_mcqs_today} / {t_total_mcqs} total MCQs` (Short by `{t_total_mcqs - total_mcqs_today} MCQs`)"
            )
        if not top_ok:
            missed_items.append(
                f"• **Syllabus Topic:** `{prof['daily_topics']} / {t_topics}` (Zero concepts/revisions cleared)"
            )

        shield_active = prof.get("active_shield_date") == today_str
        all_passed = len(missed_items) == 0

        new_level = prof["level"]
        new_gold = prof["gold"]
        new_max_hp = prof["max_hp"]
        level_dropped = False
        gold_halved = False
        bonus_gold = 0
        recovery_quest_data: Optional[Dict[str, Any]] = None
        next_day_str = (study_day_ist_date() + timedelta(days=1)).isoformat()

        if all_passed or shield_active:
            new_streak = prof["streak"] + 1
            bonus_gold = max(100, prof.get("daily_gold_earned", 0))
            new_gold += bonus_gold
            hp_delta = 15
            new_hp = min(new_max_hp, prof["hp"] + hp_delta)
            fatigued = 0
        else:
            new_streak = 0
            hp_delta = -15 * len(missed_items)
            new_hp = prof["hp"] + hp_delta
            fatigued = 1

            if new_hp <= 0:
                level_dropped = True
                gold_halved = True
                new_level = max(1, prof["level"] - 1)
                new_max_hp = 100 + (new_level - 1) * 10
                new_hp = 50
                new_gold = new_gold // 2

            expires_iso = (now_ist() + timedelta(hours=24)).isoformat()
            recovery_quest_data = {
                "triggered_date": today_str,
                "expires_at_iso": expires_iso,
                "req_minutes": max(420, t_mins + 60),
                "req_mcqs": t_total_mcqs,
                "req_curses_cleansed": 3,
            }

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hunter_profile
                SET level = %s,
                    hp = %s,
                    max_hp = %s,
                    gold = %s,
                    streak = %s,
                    is_fatigued = %s,
                    daily_minutes = 0,
                    daily_bio_mcqs = 0,
                    daily_phy_mcqs = 0,
                    daily_chem_mcqs = 0,
                    daily_topics = 0,
                    daily_gold_earned = 0,
                    active_shield_date = NULL,
                    last_reset_date = %s,
                    last_audit_date = %s
                WHERE user_id = %s
                """,
                (
                    new_level,
                    new_hp,
                    new_max_hp,
                    new_gold,
                    new_streak,
                    fatigued,
                    next_day_str,
                    today_str,
                    ALLOWED_USER_ID,
                ),
            )
            self._ensure_activity_row(conn, next_day_str)

            if recovery_quest_data:
                conn.execute("UPDATE recovery_quests SET status = 'EXPIRED' WHERE status = 'ACTIVE'")
                conn.execute(
                    """
                    INSERT INTO recovery_quests (
                        triggered_date, expires_at_iso, req_minutes, req_mcqs, req_curses_cleansed, status
                    ) VALUES (%s, %s, %s, %s, %s, 'ACTIVE')
                    """,
                    (
                        recovery_quest_data["triggered_date"],
                        recovery_quest_data["expires_at_iso"],
                        recovery_quest_data["req_minutes"],
                        recovery_quest_data["req_mcqs"],
                        recovery_quest_data["req_curses_cleansed"],
                    ),
                )

            conn.commit()

        return {
            "passed": all_passed,
            "shield_active": shield_active,
            "missed_items": missed_items,
            "bonus_gold": bonus_gold,
            "new_streak": new_streak,
            "hp_delta": hp_delta,
            "new_hp": new_hp,
            "max_hp": new_max_hp,
            "new_level": new_level,
            "new_gold": new_gold,
            "level_dropped": level_dropped,
            "gold_halved": gold_halved,
            "fatigued": bool(fatigued),
            "recovery_quest": recovery_quest_data,
        }


# ==============================================================================
# 4. EMBED BUILDERS & WEEKLY REPORT GENERATOR
# ==============================================================================


def _mcq_pace_status(total_mcqs: int, target_total_mcqs: int, now: datetime) -> str:
    """Computes current daily MCQ pace vs. the active daily MCQ target in IST (05:00 AM cycle)."""
    day_start = datetime.combine(
        study_day_ist_date(now), time(hour=DAILY_RESET_HOUR, minute=0), tzinfo=IST
    )
    hours_elapsed = max(1.0, (now - day_start).total_seconds() / 3600.0)
    expected_by_now = int(round((target_total_mcqs / 24.0) * hours_elapsed))
    rem_mcqs = max(0, target_total_mcqs - total_mcqs)
    audit_dt = next_audit_datetime_ist(now)
    hours_left = max(0.25, (audit_dt - now).total_seconds() / 3600.0)
    needed_per_hr = rem_mcqs / hours_left if rem_mcqs > 0 else 0.0

    if total_mcqs >= target_total_mcqs:
        return f"🔥 **{target_total_mcqs}+ TARGET CLEARED** (`{total_mcqs}` MCQs)"
    if total_mcqs >= expected_by_now:
        return f"✅ **ON PACE** (`{total_mcqs}` vs `{expected_by_now}` expected now • `{needed_per_hr:.1f}/hr` needed)"
    return f"⚠️ **BEHIND PACE** (`{total_mcqs}` vs `{expected_by_now}` expected now • `{needed_per_hr:.1f}/hr` needed)"


def build_quest_board_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the live-updating Daily Quest Board embed."""
    prof = db.get_profile()
    goals = db.get_daily_goals()
    active_focus = db.get_active_focus()
    balance = db.get_7day_balance()
    recovery = db.get_active_recovery_quest()
    now = now_ist()
    study_date = study_day_ist_date(now)

    t_mins = goals["minutes"]
    t_bio = goals["bio_mcqs"]
    t_phy = goals["phy_mcqs"]
    t_chem = goals["chem_mcqs"]
    t_total_mcqs = goals["total_mcqs"]
    t_topics = goals["topics"]

    audit_dt = next_audit_datetime_ist(now)
    rem_sec = int((audit_dt - now).total_seconds())
    rem_hrs, rem_mins = divmod(rem_sec // 60, 60)

    def status_icon(curr: int, target: int) -> str:
        return "✅" if curr >= target else "⏳"

    m_curr = int(prof["daily_minutes"])
    b_curr = int(prof["daily_bio_mcqs"])
    p_curr = int(prof["daily_phy_mcqs"])
    c_curr = int(prof["daily_chem_mcqs"])
    total_mcqs = b_curr + p_curr + c_curr
    t_curr = int(prof["daily_topics"])

    shield_active = prof.get("active_shield_date") == today_ist_str()
    exam_active = bool(prof.get("exam_mode_active"))

    banners: List[str] = []
    if exam_active:
        t_lbl = prof.get("exam_mode_test_name") or "NEET Mock / Part Test"
        banners.append(f"🎯 **EXAM MODE ACTIVE:** `{t_lbl}` (+WIS XP on diagnostics & curses)")
    if shield_active:
        banners.append("🛡️ **REST / POST-TEST SHIELD ACTIVE** — Today's targets waived & streak protected!")
    if recovery:
        exp_dt = datetime.fromisoformat(recovery["expires_at_iso"])
        banners.append(
            f"⛓️ **24H RECOVERY QUEST ACTIVE** (Due `{exp_dt.strftime('%d %b %I:%M %p IST')}`): "
            f"Mins `{recovery['prog_minutes']}/{recovery['req_minutes']}` • "
            f"MCQs `{recovery['prog_mcqs']}/{recovery['req_mcqs']}` • "
            f"Curses `{recovery['prog_curses_cleansed']}/{recovery['req_curses_cleansed']}`"
        )

    if active_focus:
        start_dt = datetime.fromisoformat(active_focus["start_time_iso"])
        elapsed = int((now - start_dt).total_seconds() // 60)
        focus_banner = (
            f"🔥 **ACTIVE ({active_focus['source'].upper()})** — "
            f"`{elapsed} mins` (Started {start_dt.strftime('%I:%M %p IST')})"
        )
    else:
        focus_banner = "🟢 **IDLE** — Click `Toggle Focus` or join `#focus-dungeon`"

    banner_block = ("\n".join(banners) + "\n") if banners else ""

    embed = discord.Embed(
        title="⚔️ DAILY HUNTER QUESTS — NEET 2027",
        description=(
            f"**Study Day (IST):** `{study_date.strftime('%d %b %Y')}` *(Resets 05:00 AM)* • **04:55 AM Audit In:** `{rem_hrs}h {rem_mins}m`\n"
            f"{banner_block}"
            f"**Focus Status:** {focus_banner}\n"
            f"**MCQ Pace:** {_mcq_pace_status(total_mcqs, t_total_mcqs, now)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0xE67E22 if exam_active else (0x3498DB if shield_active else (0xE74C3C if prof["is_fatigued"] else 0x2B2D31)),
        timestamp=now,
    )

    embed.add_field(
        name=f"{status_icon(m_curr, t_mins)} Deep Study Endurance (END)",
        value=f"`{m_curr} / {t_mins} mins`\n{build_progress_bar(m_curr, t_mins)}",
        inline=False,
    )
    embed.add_field(
        name=f"{status_icon(total_mcqs, t_total_mcqs)} Daily MCQ Volume (DEX)",
        value=(
            f"`{total_mcqs} / {t_total_mcqs} Total MCQs` "
            f"(Bio `{b_curr}/{t_bio}` • Phy `{p_curr}/{t_phy}` • Chem `{c_curr}/{t_chem}`)\n"
            f"{build_progress_bar(total_mcqs, t_total_mcqs)}"
        ),
        inline=False,
    )
    embed.add_field(
        name=f"{status_icon(t_curr, t_topics)} Syllabus Mastery (INT / WIS)",
        value=f"`{t_curr} / {t_topics} Topic Cleared`\n{build_progress_bar(t_curr, t_topics)}",
        inline=False,
    )

    footer_txt = "Audit at 04:55 AM IST (Resets 05:00 AM IST): -15 HP per missed target • Tune via /set_goal"
    if not balance["is_balanced"]:
        footer_txt += f" | Balance Alert: {balance['alerts'][0]}"
    embed.set_footer(text=footer_txt)
    return embed


def build_status_window_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the Hunter Status Window (Character Sheet) embed."""
    prof = db.get_profile()
    level = prof["level"]
    rank = get_rank_for_level(level)
    req_xp = xp_for_next_level(level)
    shield_active = prof.get("active_shield_date") == today_ist_str()
    exam_active = bool(prof.get("exam_mode_active"))
    balance = db.get_7day_balance()

    traps = db.get_filtered_chapters(traps_only=True)
    trap_count = len(traps)

    if shield_active:
        condition = "🛡️ SHIELDED (Rest Protection)"
        color = 0x3498DB
    elif prof["is_fatigued"]:
        condition = "⚠️ FATIGUED (Debuff Active)"
        color = 0xE67E22
    else:
        condition = "⚡ OPTIMAL"
        color = 0x00D2FF

    mode_str = (
        f"🎯 **EXAM MODE:** `{prof.get('exam_mode_test_name') or 'Active'}`"
        if exam_active
        else "📚 **Standard Prep Mode**"
    )

    inv = db.get_inventory()
    inv_str = ", ".join(f"{i['name']} `x{i['quantity']}`" for i in inv) if inv else "Empty"

    if balance["is_balanced"]:
        bal_str = f"✅ Balanced (7d Bio `{balance['bio_mcqs']}` : Phy `{balance['phy_mcqs']}` | Notes/Exec `{balance['concept_count']}/{balance['execution_count']}`)"
    else:
        bal_str = "⚖️ " + " • ".join(balance["alerts"])

    embed = discord.Embed(
        title="📊 HUNTER STATUS WINDOW",
        description=(
            f"**Class:** `NEET 2027 Sovereign Aspirant` • {mode_str}\n"
            f"**Rank:** `{rank}` • **Level:** `{level}`\n"
            f"**Condition:** `{condition}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=color,
        timestamp=now_ist(),
    )

    embed.add_field(
        name=f"✨ Experience (XP): {prof['xp']} / {req_xp}",
        value=build_progress_bar(prof["xp"], req_xp, length=18),
        inline=False,
    )
    embed.add_field(
        name=f"❤️ Health Points (HP): {prof['hp']} / {prof['max_hp']}",
        value=build_progress_bar(prof["hp"], prof["max_hp"], length=18),
        inline=False,
    )
    embed.add_field(name="🧠 INT (Concept)", value=f"`{prof['stat_int']}`", inline=True)
    embed.add_field(name="🛡️ END (Focus)", value=f"`{prof['stat_end']}`", inline=True)
    embed.add_field(name="⚡ DEX (MCQs)", value=f"`{prof['stat_dex']}`", inline=True)
    embed.add_field(name="📖 WIS (Revision)", value=f"`{prof['stat_wis']}`", inline=True)
    embed.add_field(name="🪙 Gold Treasury", value=f"`{prof['gold']:,} G`", inline=True)
    embed.add_field(name="🔥 Accountability Streak", value=f"`{prof['streak']} Days`", inline=True)
    embed.add_field(name="⚖️ 7-Day Subject & Habit Balance", value=bal_str, inline=False)
    embed.add_field(
        name="🚨 Notes-Only Trap Chapters",
        value=f"`{trap_count}` chapters flagged" if trap_count else "`0` (Clean)",
        inline=True,
    )
    embed.add_field(name="🎒 Inventory Pouch", value=inv_str, inline=True)

    return embed


def build_boss_raids_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the Syllabus Boss Raid & Dec 15, 2026 Countdown embed."""
    today = study_day_ist_date()
    days_left = max(0, (TARGET_MASTERY_DATE - today).days)

    cleared, total = db.get_syllabus_totals()
    remaining_units = max(0, total - cleared)

    if days_left > 0:
        required_pace = remaining_units / days_left
        pace_str = f"`{required_pace:.2f} stages/day` ({remaining_units} stages left over {days_left} days)"
    elif remaining_units == 0:
        pace_str = "`0.00 stages/day` — SYLLABUS CONQUERED!"
    else:
        pace_str = f"`DEADLINE REACHED` — {remaining_units} stages remaining!"

    with db._connect() as conn:
        boss_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM bosses ORDER BY CASE WHEN status = 'ACTIVE' THEN 0 ELSE 1 END, id DESC"
            ).fetchall()
        ]

    active_bosses = [b for b in boss_rows if b["status"] == "ACTIVE"][:6]
    defeated_count = sum(1 for b in boss_rows if b["status"] == "DEFEATED")

    if active_bosses:
        boss_lines = []
        for b in active_bosses:
            rem_t = max(0, b["total_topics"] - b["completed_topics"])
            cur_hp = rem_t * BOSS_HP_PER_TOPIC
            max_hp = b["total_topics"] * BOSS_HP_PER_TOPIC
            bar = build_progress_bar(cur_hp, max_hp, length=10)
            boss_lines.append(
                f"👹 **[ID {b['id']}] {b['subject']} — {b['chapter']}**\n"
                f"   HP: `{cur_hp}/{max_hp}` {bar} • `{rem_t}` stages left"
            )
        chapter_lines = "\n".join(boss_lines)
    else:
        chapter_lines = "*No active Chapter Bosses. Toggle a stage in `/chapters` or run `/boss add`!*"

    embed = discord.Embed(
        title="🐉 SYLLABUS BOSS RAID — DEC 15, 2026 DEADLINE",
        description=(
            f"**Target Mastery Date:** `December 15, 2026 (IST)`\n"
            f"**Days Remaining:** `⏳ {days_left} Days`\n"
            f"**Required Daily Pace:** {pace_str}\n"
            f"**Chapter Bosses Defeated:** `🏆 {defeated_count}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x9B59B6,
        timestamp=now_ist(),
    )

    embed.add_field(
        name=f"🏰 Overall NEET 2027 Conquest ({cleared} / {total} Stages)",
        value=build_progress_bar(cleared, total, length=18),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Active Chapter Bosses",
        value=chapter_lines,
        inline=False,
    )
    return embed


def build_chapter_codex_embed(
    db: DatabaseManager,
    subject_filter: str = "ALL",
    class_filter: Optional[int] = None,
    traps_only: bool = False,
    page: int = 0,
    per_page: int = 6,
) -> Tuple[discord.Embed, int, int]:
    """Builds a clean paginated chapter browser embed (6 chapters per page)."""
    chapters = db.get_filtered_chapters(
        subject_filter=subject_filter,
        class_filter=class_filter,
        traps_only=traps_only,
    )

    total_items = len(chapters)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    safe_page = max(0, min(page, total_pages - 1))

    max_stage_pts = max(1, total_items * 5)
    earned_stage_pts = sum(
        sum(int(ch[k]) for k in STAGE_KEYS) for ch in chapters
    )
    overall_pct = int(round((earned_stage_pts / max_stage_pts) * 100)) if total_items else 0

    filter_desc = subject_filter if subject_filter != "ALL" else "All Subjects"
    if class_filter:
        filter_desc += f" • Class {class_filter}"
    if traps_only:
        filter_desc += " • ⚠️ Notes-Only Traps"

    start = safe_page * per_page
    end = start + per_page
    page_items = chapters[start:end]

    card_lines: List[str] = []
    for idx, ch in enumerate(page_items, start=start + 1):
        done_cnt = sum(int(ch[k]) for k in STAGE_KEYS)
        pct = done_cnt * 20
        trap_badge = " 🚨 *NOTES TRAP*" if db.is_notes_only_trap(ch) else ""
        stage_badges = " | ".join(
            f"{STAGE_SHORT_LABELS[k]} {'✅' if int(ch[k]) else '⬜'}"
            for k in STAGE_KEYS
        )
        card_lines.append(
            f"**{idx}. {ch['chapter_name']}** *(Cl {ch['class_level']} • {ch['subject'][:3]})* — `{pct}%` {build_stage_bar(done_cnt)}{trap_badge}\n"
            f"   {stage_badges}"
        )

    body_text = "\n\n".join(card_lines) if card_lines else "*No chapters match this filter.*"

    embed = discord.Embed(
        title=f"📘 NEET 2027 CHAPTER CODEX — {filter_desc}",
        description=(
            f"**Filter Progress:** `{earned_stage_pts}/{max_stage_pts}` stages (`{overall_pct}%`) {build_progress_bar(earned_stage_pts, max_stage_pts, 14)}\n"
            f"**Chapters Matched:** `{total_items}` • **Page:** `{safe_page + 1} / {total_pages}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{body_text}"
        ),
        color=0x1ABC9C,
        timestamp=now_ist(),
    )
    embed.set_footer(
        text="Toggle stages via /chapter_toggle chapter:<name> stage:<short_notes|topics_done|mcqs_done|mistakes_logged|revised>"
    )
    return embed, safe_page, total_pages


def build_weekly_report_embed(db: DatabaseManager) -> discord.Embed:
    """Builds the 7-day rolling accountability & NEET 2027 pace report."""
    bal = db.get_7day_balance()
    prof = db.get_profile()
    goals = db.get_daily_goals()
    today = study_day_ist_date()
    days_left = max(0, (TARGET_MASTERY_DATE - today).days)
    cleared, total = db.get_syllabus_totals()
    rem_units = max(0, total - cleared)
    pace_needed = (rem_units / days_left) if days_left > 0 else float(rem_units)

    total_7d_mcqs = bal["bio_mcqs"] + bal["phy_mcqs"] + bal["chem_mcqs"]
    study_hours_7d = bal["study_minutes"] / 60.0
    uncleared_curses = db.get_uncleared_curse_count()

    with db._connect() as conn:
        chap_prog_cnt = int(
            conn.execute(
                "SELECT COUNT(*) AS cnt FROM chapters WHERE (short_notes + topics_done + mcqs_done + mistakes_logged + revised) > 0"
            ).fetchone()["cnt"]
        )

    balance_txt = (
        "✅ Balanced across subjects & execution."
        if bal["is_balanced"]
        else "\n".join(f"• ⚠️ {a}" for a in bal["alerts"])
    )

    embed = discord.Embed(
        title="📅 WEEKLY ACCOUNTABILITY REPORT (ROLLING 7 DAYS — IST)",
        description=(
            f"**Report Date:** `{now_ist().strftime('%d %b %Y')}` • **Streak:** `🔥 {prof['streak']} Days`\n"
            f"**Dec 15, 2026 Countdown:** `{days_left} Days Left` • **Required Pace:** `{pace_needed:.2f} stages/day`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x5865F2,
        timestamp=now_ist(),
    )
    embed.add_field(
        name="⏱️ Study & Exam Mode Time",
        value=f"`{study_hours_7d:.1f} hrs` (`{bal['study_minutes']} mins`) • Exam/Test time: `{bal['exam_mode_minutes']} mins`",
        inline=False,
    )
    embed.add_field(
        name="🎯 7-Day MCQ Volume",
        value=(
            f"**Total:** `{total_7d_mcqs:,} MCQs` (Avg `{total_7d_mcqs / 7.0:.0f}/day` vs `{goals['total_mcqs']}/day` target)\n"
            f"Bio `{bal['bio_mcqs']:,}` • Physics `{bal['phy_mcqs']:,}` • Chem `{bal['chem_mcqs']:,}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="📝 Tests, Chapters & Cursed Vault",
        value=(
            f"• **Tests Logged (7d):** `{bal['tests_logged']}`\n"
            f"• **Chapters Touched:** `{chap_prog_cnt}` (`{cleared}/{total}` total stages)\n"
            f"• **Uncleared Curses in Vault:** `{uncleared_curses}`"
        ),
        inline=False,
    )
    embed.add_field(name="⚖️ Subject & Habit Balance", value=balance_txt, inline=False)
    return embed


def build_gemini_quiz_question_embed(sess: Dict[str, Any], q_idx: int) -> discord.Embed:
    qs = sess["questions"]
    total = len(qs)
    idx = max(0, min(q_idx, total - 1))
    q = qs[idx]

    answered_cnt = sum(1 for item in qs if item.get("user_answer") is not None)
    correct_cnt = sum(1 for item in qs if item.get("is_correct") is True)

    opts_text = "\n".join(q["options"])
    user_ans = q.get("user_answer")
    feedback_block = ""
class LogMCQModal(discord.ui.Modal, title="Log Completed NEET MCQs"):
    """Modal popup triggered from #quest-board so the user can enter any custom MCQ count."""

    subject_input = discord.ui.TextInput(
        label="Subject (Bio, Physics, or Chem)",
        placeholder="Bio / Physics / Chem",
        min_length=2,
        max_length=15,
        required=True,
    )
    count_input = discord.ui.TextInput(
        label="Number of MCQs Solved",
        placeholder="e.g., 39, 70, 14, 100",
        min_length=1,
        max_length=5,
        required=True,
    )

    def __init__(self, bot: "StudyRPGBot", default_subject: str = ""):
        super().__init__()
        self.bot = bot
        if default_subject:
            self.subject_input.default = default_subject

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_subj = self.subject_input.value.strip().lower()
        if raw_subj.startswith("bio"):
            canon_subj = "Bio"
        elif raw_subj.startswith("phy"):
            canon_subj = "Physics"
        elif raw_subj.startswith("chem"):
            canon_subj = "Chem"
        else:
            await interaction.response.send_message(
                "❌ Invalid subject! Type **Bio**, **Physics**, or **Chem**.",
                ephemeral=True,
            )
            return

        try:
            count = int(self.count_input.value.strip())
            if count <= 0 or count > 5000:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid positive number of MCQs (1 – 5000).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        receipt = await self.bot.process_mcq_log(
            subject=canon_subj, count=count, source="Quest Board Modal"
        )
        await interaction.followup.send(f"✅ **MCQs Logged!**\n{receipt}", ephemeral=True)


class QuestBoardView(discord.ui.View):
    """Persistent button panel attached to #quest-board."""

    def __init__(self, bot: "StudyRPGBot"):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message(
                "⛔ **Access Denied:** Locked to the Sovereign Hunter.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Log Bio MCQs",
        style=discord.ButtonStyle.success,
        emoji="🧬",
        custom_id="rpg_quest_modal_bio",
    )
    async def log_bio_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LogMCQModal(self.bot, default_subject="Bio"))

    @discord.ui.button(
        label="Log Phy MCQs",
        style=discord.ButtonStyle.primary,
        emoji="⚛️",
        custom_id="rpg_quest_modal_phy",
    )
    async def log_phy_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LogMCQModal(self.bot, default_subject="Physics"))

    @discord.ui.button(
        label="Log Chem MCQs",
        style=discord.ButtonStyle.primary,
        emoji="🧪",
        custom_id="rpg_quest_modal_chem",
    )
    async def log_chem_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LogMCQModal(self.bot, default_subject="Chem"))

    @discord.ui.button(
        label="+1 Topic Cleared",
        style=discord.ButtonStyle.secondary,
        emoji="📘",
        custom_id="rpg_quest_topic_1",
        row=1,
    )
    async def quick_topic_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_topic_log(
            subject="Biology",
            chapter="The Living World",
            topic=f"Quick Clear ({now_ist().strftime('%H:%M IST')})",
            mode="Revision",
        )
        await interaction.followup.send("✅ Logged **+1 Topic Cleared**!", ephemeral=True)

    @discord.ui.button(
        label="Toggle Focus",
        style=discord.ButtonStyle.danger,
        emoji="⏱️",
        custom_id="rpg_quest_toggle_focus",
        row=1,
    )
    async def toggle_focus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        active = self.bot.db.get_active_focus()
        if not active:
            _, iso_str = self.bot.db.start_focus(source="Quest Board Button")
            await self.bot.refresh_all_dashboards()
            await interaction.followup.send(
                f"🔥 **Focus Session Started** at `{datetime.fromisoformat(iso_str).strftime('%I:%M %p IST')}`!",
                ephemeral=True,
            )
        else:
            result = self.bot.db.stop_focus()
            if result:
                mins, src = result
                summary = await self.bot.finalize_focus_session(mins, src)
                await interaction.followup.send(summary, ephemeral=True)


class ChapterCodexSelect(discord.ui.Select):
    def __init__(self, codex_view: "ChapterCodexView"):
        options = [
            discord.SelectOption(label="All Subjects (Cl 11 & 12)", value="ALL|0", emoji="📚"),
            discord.SelectOption(label="Physics — All", value="Physics|0", emoji="⚛️"),
            discord.SelectOption(label="Physics — Class 11", value="Physics|11", emoji="⚛️"),
            discord.SelectOption(label="Physics — Class 12", value="Physics|12", emoji="⚛️"),
            discord.SelectOption(label="Chemistry — All", value="Chemistry|0", emoji="🧪"),
            discord.SelectOption(label="Chemistry — Class 11", value="Chemistry|11", emoji="🧪"),
            discord.SelectOption(label="Chemistry — Class 12", value="Chemistry|12", emoji="🧪"),
            discord.SelectOption(label="Biology — All", value="Biology|0", emoji="🧬"),
            discord.SelectOption(label="Biology — Class 11", value="Biology|11", emoji="🧬"),
            discord.SelectOption(label="Biology — Class 12", value="Biology|12", emoji="🧬"),
            discord.SelectOption(label="Notes-Only Trap Chapters", value="TRAPS|0", emoji="🚨"),
        ]
        super().__init__(
            placeholder="Filter NEET 2027 Chapters by Subject / Class...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rpg_codex_filter_select",
        )
        self.codex_view = codex_view

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        subj_part, cls_part = val.split("|", 1)
        if subj_part == "TRAPS":
            self.codex_view.subject_filter = "ALL"
            self.codex_view.class_filter = None
            self.codex_view.traps_only = True
        else:
            self.codex_view.subject_filter = subj_part
            self.codex_view.class_filter = int(cls_part) if int(cls_part) in (11, 12) else None
            self.codex_view.traps_only = False
        self.codex_view.page = 0
        await self.codex_view.render_update(interaction)


class ChapterCodexView(discord.ui.View):
    """Persistent paginated chapter browser view for #codex-topics and /chapters."""

    def __init__(
        self,
        bot: "StudyRPGBot",
        subject_filter: str = "ALL",
        class_filter: Optional[int] = None,
        traps_only: bool = False,
        page: int = 0,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.subject_filter = subject_filter
        self.class_filter = class_filter
        self.traps_only = traps_only
        self.page = page
        self.add_item(ChapterCodexSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message(
                "⛔ **Access Denied:** Locked to the Sovereign Hunter.",
                ephemeral=True,
            )
            return False
        return True

    async def render_update(self, interaction: discord.Interaction) -> None:
        embed, safe_page, _ = build_chapter_codex_embed(
            self.bot.db,
            subject_filter=self.subject_filter,
            class_filter=self.class_filter,
            traps_only=self.traps_only,
            page=self.page,
        )
        self.page = safe_page
        if not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(
        label="◀️ Prev",
        style=discord.ButtonStyle.secondary,
        custom_id="rpg_codex_prev",
    )
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self.render_update(interaction)

    @discord.ui.button(
        label="Next ▶️",
        style=discord.ButtonStyle.secondary,
        custom_id="rpg_codex_next",
    )
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.page + 1
        await self.render_update(interaction)

    @discord.ui.button(
        label="Notes-Only Trap Check",
        style=discord.ButtonStyle.danger,
        emoji="🚨",
        custom_id="rpg_codex_trap_check",
    )
    async def trap_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        traps = self.bot.db.get_filtered_chapters(traps_only=True)
        if not traps:
            await interaction.response.send_message(
                "✅ **Reality Check Passed:** No chapters are stuck in a Notes-Only Trap right now.",
                ephemeral=True,
            )
            return
        lines = [
            f"• **{t['chapter_name']}** ({t['subject']} Cl {t['class_level']}) — Notes ✅, MCQs ⬜, Revised ⬜"
            for t in traps[:15]
        ]
        await interaction.response.send_message(
            "🚨 **NOTES-ONLY TRAP ALERT:** You have notes done without locking MCQs or Revision yet:\n"
            + "\n".join(lines),
            ephemeral=True,
        )


class GeminiQuizView(discord.ui.View):
    """Interactive A/B/C/D answer & navigation controls for /gemini_quiz."""

    def __init__(self, bot: "StudyRPGBot", session_id: int, q_idx: int = 0):
        super().__init__(timeout=900)
        self.bot = bot
        self.session_id = session_id
        self.q_idx = q_idx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message("⛔ Locked to owner.", ephemeral=True)
            return False
        return True

    async def _answer(self, interaction: discord.Interaction, letter: str) -> None:
        await interaction.response.defer()
        sess, is_first_ans = self.bot.db.record_quiz_answer(self.session_id, self.q_idx, letter)
        if not sess:
            return
        if is_first_ans:
            subj_key = normalize_subject_key(sess["subject"])
            await self.bot.process_mcq_log(
                subject=subj_key,
                count=1,
                source=f"Gemini Quiz #{self.session_id}",
                quiet_battle_log=True,
            )
        embed = build_gemini_quiz_question_embed(sess, self.q_idx)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="A", style=discord.ButtonStyle.secondary)
    async def opt_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.secondary)
    async def opt_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, "B")

    @discord.ui.button(label="C", style=discord.ButtonStyle.secondary)
    async def opt_c(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, "C")

    @discord.ui.button(label="D", style=discord.ButtonStyle.secondary)
    async def opt_d(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, "D")

    @discord.ui.button(label="◀️ Prev Q", style=discord.ButtonStyle.primary, row=1)
    async def prev_q(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = self.bot.db.get_quiz_session(self.session_id)
        if not sess:
            return
        self.q_idx = max(0, self.q_idx - 1)
        await interaction.response.edit_message(
            embed=build_gemini_quiz_question_embed(sess, self.q_idx), view=self
        )

    @discord.ui.button(label="Next Q ▶️", style=discord.ButtonStyle.primary, row=1)
    async def next_q(self, interaction: discord.Interaction, button: discord.ui.Button):
        sess = self.bot.db.get_quiz_session(self.session_id)
        if not sess:
            return
        self.q_idx = min(len(sess["questions"]) - 1, self.q_idx + 1)
        await interaction.response.edit_message(
            embed=build_gemini_quiz_question_embed(sess, self.q_idx), view=self
        )


# ==============================================================================
# 6. CORE RPG BOT ENGINE & AUTOMATED WORLD BUILDER
# ==============================================================================


class StudyRPGBot(commands.Bot):
    """Main Discord Bot subclass encapsulating RPG logic, tasks, and voice tracking."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = DatabaseManager(DATABASE_URL)
        self.gemini = GeminiStudyCoach(GEMINI_API_KEY, GEMINI_MODEL)

    async def sync_all_slash_commands(self) -> Tuple[int, str]:
        """
        Hard-wipes any stale global commands via raw Discord HTTP API and syncs
        all current commands cleanly to `GUILD_ID`.
        """
        guild_obj = discord.Object(id=GUILD_ID)
        # Snapshot global tree into guild tree before wiping global
        self.tree.copy_global_to(guild=guild_obj)

        synced_guild = await self.tree.sync(guild=guild_obj)
        logger.info(
            "SLASH SYNC: Synced %d RPG commands to Guild ID %s",
            len(synced_guild),
            GUILD_ID,
        )

        # Hard-overwrite global application commands with [] so old bot commands disappear
        if self.user:
            await self.http.bulk_upsert_global_commands(self.user.id, [])
            logger.info("SLASH SYNC: Hard-wiped all global commands for App ID %s", self.user.id)

        return len(synced_guild), ", ".join(f"/{c.name}" for c in synced_guild)

    async def setup_hook(self) -> None:
        self.add_view(QuestBoardView(self))
        self.add_view(ChapterCodexView(self))

        async def global_owner_check(interaction: discord.Interaction) -> bool:
            if interaction.user.id != ALLOWED_USER_ID:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⛔ **Unauthorized:** This private RPG system belongs exclusively to its owner.",
                        ephemeral=True,
                    )
                return False
            return True

        self.tree.interaction_check = global_owner_check

        try:
            await self.sync_all_slash_commands()
        except Exception as exc:
            logger.error("SLASH SYNC ERROR during setup_hook: %s", exc)

        if not self.midnight_tribunal_task.is_running():
            self.midnight_tribunal_task.start()
        if not self.dashboard_heartbeat_task.is_running():
            self.dashboard_heartbeat_task.start()
        if not self.weekly_report_task.is_running():
            self.weekly_report_task.start()

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (ID: %s) | IST Time: %s | Active Study Day: %s",
            self.user,
            self.user.id,
            now_ist().strftime("%Y-%m-%d %H:%M:%S"),
            today_ist_str(),
        )

    async def resolve_owner_member(self, guild: discord.Guild) -> Optional[discord.Member]:
        member = guild.get_member(ALLOWED_USER_ID)
        if member:
            return member
        try:
            return await guild.fetch_member(ALLOWED_USER_ID)
        except discord.NotFound:
            return None
        except discord.HTTPException:
            return None

    async def sync_hunter_roles(self, guild: discord.Guild) -> Optional[str]:
        member = await self.resolve_owner_member(guild)
        if not member:
            return None

        prof = self.db.get_profile()
        target_rank_name = get_rank_for_level(prof["level"])
        rank_names = {r[1] for r in RANK_TIERS}

        roles_to_add = []
        roles_to_remove = []
        rank_up_announcement = None

        target_role = discord.utils.get(guild.roles, name=target_rank_name)
        if target_role and target_role not in member.roles:
            roles_to_add.append(target_role)
            rank_up_announcement = f"🎊 **RANK ASCENSION!** You have ascended to **{target_rank_name}**!"

        for r in member.roles:
            if r.name in rank_names and r.name != target_rank_name:
                roles_to_remove.append(r)

        fatigued_role = discord.utils.get(guild.roles, name=FATIGUED_ROLE_NAME)
        if fatigued_role:
            if prof["is_fatigued"] and fatigued_role not in member.roles:
                roles_to_add.append(fatigued_role)
            elif not prof["is_fatigued"] and fatigued_role in member.roles:
                roles_to_remove.append(fatigued_role)

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="RPG Stat Sync")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="RPG Stat Sync")
        except discord.Forbidden:
            logger.warning("Missing Manage Roles permission or role hierarchy issue.")

        return rank_up_announcement

    async def refresh_all_dashboards(self) -> None:
        guild = self.get_guild(GUILD_ID)
        if not guild:
            return

        qb_ch_id = self.db.get_config("ch_quest-board")
        qb_msg_id = self.db.get_config("msg_quest-board")
        if qb_ch_id and qb_msg_id:
            ch = guild.get_channel(qb_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(qb_msg_id)
                    await msg.edit(embed=build_quest_board_embed(self.db), view=QuestBoardView(self))
                except discord.NotFound:
                    logger.warning("Pinned quest-board message not found.")

        sw_ch_id = self.db.get_config("ch_status-window")
        sw_msg_id = self.db.get_config("msg_status-window")
        if sw_ch_id and sw_msg_id:
            ch = guild.get_channel(sw_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(sw_msg_id)
                    await msg.edit(embed=build_status_window_embed(self.db))
                except discord.NotFound:
                    logger.warning("Pinned status-window message not found.")

        br_ch_id = self.db.get_config("ch_boss-raids")
        br_msg_id = self.db.get_config("msg_boss-raids")
        if br_ch_id and br_msg_id:
            ch = guild.get_channel(br_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(br_msg_id)
                    await msg.edit(embed=build_boss_raids_embed(self.db))
                except discord.NotFound:
                    logger.warning("Pinned boss-raids message not found.")

        cd_ch_id = self.db.get_config("ch_codex-topics")
        cd_msg_id = self.db.get_config("msg_codex-topics")
        if cd_ch_id and cd_msg_id:
            ch = guild.get_channel(cd_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(cd_msg_id)
                    codex_embed, _, _ = build_chapter_codex_embed(self.db, page=0)
                    await msg.edit(embed=codex_embed, view=ChapterCodexView(self))
                except discord.NotFound:
                    logger.warning("Pinned codex-topics message not found.")

    async def post_battle_receipt(self, title: str, details: str, color: int = 0x2ECC71) -> None:
        guild = self.get_guild(GUILD_ID)
        if not guild:
            return
        ch_id = self.db.get_config("ch_battle-logs")
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            embed = discord.Embed(
                title=title,
                description=details,
                color=color,
                timestamp=now_ist(),
            )
            embed.set_footer(text=f"Logged at {now_ist().strftime('%I:%M %p IST')}")
            await ch.send(embed=embed)

    async def finalize_focus_session(self, minutes: int, source: str) -> str:
        if minutes < 1:
            await self.refresh_all_dashboards()
            return "⚠️ Focus session ended under **1 minute** — no XP or Gold awarded."

        prof_before = self.db.get_profile()
        goals = self.db.get_daily_goals()
        exam_active = bool(prof_before.get("exam_mode_active"))

        xp_gain = minutes * 2
        gold_gain = minutes * 1
        end_gain = max(1, minutes // 30)

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_end=end_gain,
            d_minutes=minutes,
            d_exec_events=1,
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None

        mode_tag = f"{source} [EXAM MODE]" if exam_active else source
        receipt = (
            f"**Source:** `{mode_tag}`\n"
            f"**Duration:** `{minutes} minutes`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{end_gain} END`\n"
            f"**Daily Study Progress:** `{prof['daily_minutes']} / {goals['minutes']} mins`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt("⏱️ FOCUS DUNGEON CLEARED", receipt, color=0x3498DB)
        await self.refresh_all_dashboards()
        return f"✅ **Focus Session Logged ({minutes} mins):** `+{xp_gain} XP`, `+{gold_gain} Gold`, `+{end_gain} END`."

    async def process_mcq_log(
        self,
        subject: str,
        count: int,
        source: str,
        quiet_battle_log: bool = False,
    ) -> str:
        subj_norm = normalize_subject_key(subject)
        bal = self.db.get_7day_balance()
        goals = self.db.get_daily_goals()

        if subj_norm == "Bio":
            xp_per_q = 1.0 * bal["nudge_bio"]
            d_bio, d_phy, d_chem = count, 0, 0
            label = "Biology"
        elif subj_norm == "Physics":
            xp_per_q = 2.0 * bal["nudge_phy"]
            d_bio, d_phy, d_chem = 0, count, 0
            label = "Physics"
        else:
            xp_per_q = 1.5
            d_bio, d_phy, d_chem = 0, 0, count
            label = "Chemistry"

        xp_gain = max(1, int(round(count * xp_per_q)))
        gold_gain = max(1, count // 2)
        dex_gain = max(1, count // 25)

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_dex=dex_gain,
            d_bio=d_bio,
            d_phy=d_phy,
            d_chem=d_chem,
            d_exec_events=max(1, count // 20),
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None
        total_today = prof["daily_bio_mcqs"] + prof["daily_phy_mcqs"] + prof["daily_chem_mcqs"]

        nudge_note = ""
        if (subj_norm == "Bio" and bal["nudge_bio"] < 1.0) or (
            subj_norm == "Physics" and bal["nudge_phy"] < 1.0
        ):
            nudge_note = "\n⚖️ *Balance Nudge active: slightly reduced XP until lagging subject/practice catches up.*"

        receipt = (
            f"**Subject:** `{label}` ({source})\n"
            f"**Solved:** `+{count} MCQs`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{dex_gain} DEX`\n"
            f"**Daily MCQ Total:** `{total_today} / {goals['total_mcqs']}` "
            f"(Bio `{prof['daily_bio_mcqs']}` • Phy `{prof['daily_phy_mcqs']}` • Chem `{prof['daily_chem_mcqs']}`)"
            f"{nudge_note}"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        if not quiet_battle_log:
            await self.post_battle_receipt(f"🎯 {label.upper()} MCQ BARRAGE", receipt, color=0x2ECC71)
        await self.refresh_all_dashboards()
        return receipt

    async def process_topic_log(
        self, subject: str, chapter: str, topic: str, mode: str
    ) -> str:
        boss_telemetry = self.db.record_topic_and_damage_boss(subject, chapter, topic, mode)
        bal = self.db.get_7day_balance()

        is_concept = mode.lower() == "concept"
        base_xp = 120 if is_concept else 100
        if is_concept and bal["nudge_concept"] < 1.0:
            base_xp = int(round(base_xp * bal["nudge_concept"]))

        xp_gain = base_xp
        gold_gain = 60 if is_concept else 50
        d_int = 2 if is_concept else 0
        d_wis = 0 if is_concept else 2
        stat_label = "+2 INT (Concept)" if is_concept else "+2 WIS (Revision)"

        boss_bonus_text = ""
        if boss_telemetry and boss_telemetry["just_defeated"]:
            xp_gain += 250
            gold_gain += 150
            boss_bonus_text = (
                f"\n🏆 **CHAPTER BOSS DEFEATED: {boss_telemetry['chapter'].upper()}!** "
                f"(Bounty: `+250 XP`, `+150 Gold`)"
            )

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_int=d_int,
            d_wis=d_wis,
            d_topics=1,
            d_concept_events=1 if is_concept else 0,
            d_exec_events=0 if is_concept else 1,
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None
        cleared, total = self.db.get_syllabus_totals()

        boss_hp_line = ""
        if boss_telemetry:
            boss_hp_line = (
                f"\n👹 **Boss HP ({boss_telemetry['chapter']}):** "
                f"`{boss_telemetry['current_hp']} / {boss_telemetry['max_hp']} HP` "
                f"(`{boss_telemetry['completed_topics']}/{boss_telemetry['total_topics']}`)"
            )

        receipt = (
            f"**Subject:** `{subject}` • **Chapter:** `{chapter}`\n"
            f"**Topic:** `{topic}` (`{mode}`)\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `{stat_label}`"
            f"{boss_hp_line}"
            f"{boss_bonus_text}\n"
            f"**Overall Syllabus:** `{cleared} / {total} Stages`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt(
            "🏆 CHAPTER BOSS VANQUISHED!"
            if (boss_telemetry and boss_telemetry["just_defeated"])
            else "📘 SYLLABUS BOSS STRIKE",
            receipt,
            color=0xF1C40F if (boss_telemetry and boss_telemetry["just_defeated"]) else 0x9B59B6,
        )

        await self.refresh_all_dashboards()
        return receipt

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id != ALLOWED_USER_ID or member.guild.id != GUILD_ID:
            return

        focus_vc_id = self.db.get_config("ch_focus-dungeon")
        if not focus_vc_id:
            return

        joined_dungeon = (after.channel is not None and after.channel.id == focus_vc_id) and (
            before.channel is None or before.channel.id != focus_vc_id
        )
        left_dungeon = (before.channel is not None and before.channel.id == focus_vc_id) and (
            after.channel is None or after.channel.id != focus_vc_id
        )

        if joined_dungeon:
            started, _ = self.db.start_focus(source="Voice #focus-dungeon")
            if started:
                await self.refresh_all_dashboards()
        elif left_dungeon:
            stopped = self.db.stop_focus()
            if stopped:
                mins, src = stopped
                await self.finalize_focus_session(mins, src)

    @tasks.loop(time=time(hour=DAILY_AUDIT_HOUR, minute=DAILY_AUDIT_MINUTE, second=0, tzinfo=IST))
    async def midnight_tribunal_task(self) -> None:
        logger.info("Executing 04:55 AM IST Ruthless Daily Audit at %s", now_ist())
        verdict = self.db.run_midnight_audit()
        if not verdict:
            return

        guild = self.get_guild(GUILD_ID)
        if guild:
            await self.sync_hunter_roles(guild)
            tribunal_ch_id = self.db.get_config("ch_midnight-tribunal")
            if tribunal_ch_id:
                ch = guild.get_channel(tribunal_ch_id)
                if isinstance(ch, discord.TextChannel):
                    if verdict["passed"] or verdict["shield_active"]:
                        reason = (
                            "🛡️ **REST / POST-TEST SHIELD ABSORBED TODAY'S QUOTA!**"
                            if verdict["shield_active"]
                            else "⚖️ **ALL DAILY TARGETS & MCQ PACE CRUSHED — 2X GOLD BONUS AWARDED!**"
                        )
                        desc = (
                            f"{reason}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• **Daily Clear Gold Bonus:** `+{verdict['bonus_gold']} G` (Total: `{verdict['new_gold']:,} G`)\n"
                            f"• **HP Recovery:** `{verdict['hp_delta']:+d} HP` (`{verdict['new_hp']}/{verdict['max_hp']} HP`)\n"
                            f"• **Accountability Streak:** `🔥 {verdict['new_streak']} Days`\n"
                            f"• **New Study Day Rollover:** `05:00 AM IST`"
                        )
                        color = 0x2ECC71
                    else:
                        missed_block = "\n".join(verdict["missed_items"])
                        catastrophe_lines = ""
                        if verdict["level_dropped"]:
                            catastrophe_lines += (
                                f"\n💀 **CRITICAL HP COLLAPSE:** Level dropped to **Level {verdict['new_level']}**, "
                                f"Gold halved to `{verdict['new_gold']:,} G`, and `⚠️ Fatigued` debuff applied!"
                            )
                        rq = verdict.get("recovery_quest")
                        rq_block = ""
                        if rq:
                            exp_dt = datetime.fromisoformat(rq["expires_at_iso"])
                            rq_block = (
                                f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"⛓️ **24-HOUR RECOVERY QUEST GENERATED** (Expires `{exp_dt.strftime('%d %b %I:%M %p IST')}`):\n"
                                f"• Log `{rq['req_minutes']} mins` study time\n"
                                f"• Complete `{rq['req_mcqs']} MCQs`\n"
                                f"• Cleanse `{rq['req_curses_cleansed']} curses` in `#cursed-vault`\n"
                                f"Claim via `/redeem action:claim` once finished!"
                            )
                        desc = (
                            f"💀 **VERDICT: UNEXCUSED FAILURE (NO DAILY CLASSES EXCUSE)**\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Missed Quests (`-15 HP` each):**\n{missed_block}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• **HP Penalty:** `{verdict['hp_delta']} HP` (`{verdict['new_hp']}/{verdict['max_hp']} HP`)\n"
                            f"• **Streak Obliterated:** `0 Days`\n"
                            f"• **Status Debuff:** `⚠️ Fatigued`\n"
                            f"• **New Study Day Rollover:** `05:00 AM IST`"
                            f"{catastrophe_lines}"
                            f"{rq_block}"
                        )
                        color = 0xE74C3C

                    embed = discord.Embed(
                        title="💀 RUTHLESS DAILY AUDIT (04:55 AM IST)",
                        description=desc,
                        color=color,
                        timestamp=now_ist(),
                    )
                    await ch.send(content=f"<@{ALLOWED_USER_ID}>", embed=embed)

        await self.refresh_all_dashboards()

    @tasks.loop(time=time(hour=21, minute=0, second=0, tzinfo=IST))
    async def weekly_report_task(self) -> None:
        """Automatically posts the Weekly Report on Sundays at 21:00 IST."""
        if study_day_ist_date().weekday() != 6:  # 6 = Sunday
            return
        guild = self.get_guild(GUILD_ID)
        if not guild:
            return
        tribunal_ch_id = self.db.get_config("ch_midnight-tribunal")
        if tribunal_ch_id:
            ch = guild.get_channel(tribunal_ch_id)
            if isinstance(ch, discord.TextChannel):
                await ch.send(
                    content=f"<@{ALLOWED_USER_ID}>",
                    embed=build_weekly_report_embed(self.db),
                )

    @tasks.loop(minutes=15)
    async def dashboard_heartbeat_task(self) -> None:
        await self.refresh_all_dashboards()

    @dashboard_heartbeat_task.before_loop
    @midnight_tribunal_task.before_loop
    @weekly_report_task.before_loop
    async def before_tasks(self) -> None:
        await self.wait_until_ready()


bot = StudyRPGBot()


async def chapter_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    rows = bot.db.search_chapter_names(current)
    return [
        app_commands.Choice(
            name=f"{r['chapter_name']} ({r['subject']} Cl {r['class_level']})"[:100],
            value=r["chapter_name"][:100],
        )
        for r in rows[:25]
    ]


# ==============================================================================
# 7. SLASH COMMANDS (APP COMMANDS)
# ==============================================================================


@bot.tree.command(
    name="sync_commands",
    description="Force-wipe stale global slash commands and re-sync RPG commands to this server.",
)
async def sync_commands_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    count, names = await bot.sync_all_slash_commands()
    await interaction.followup.send(
        f"✅ **Slash Commands Synced & Global Cache Wiped!**\n"
        f"• Registered `{count}` server commands on Guild `{GUILD_ID}`.\n"
        f"• Restart Discord (`Ctrl+R` or reopen app) if your client still caches old global commands.\n"
        f"**Active Commands:** {names}",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_goal",
    description="Customize your daily targets (study mins, Bio/Phy/Chem MCQs, total MCQs, topics).",
)
@app_commands.describe(
    minutes="Daily study minutes target (e.g., 360 for 6h, 480 for 8h)",
    bio_mcqs="Daily Biology MCQ target (e.g., 360)",
    phy_mcqs="Daily Physics MCQ target (e.g., 140)",
    chem_mcqs="Daily Chemistry MCQ target (e.g., 100)",
    total_mcqs="Total daily MCQ target across all subjects (e.g., 600)",
    topics="Daily syllabus topics/revisions target (e.g., 1)",
)
async def set_goal_command(
    interaction: discord.Interaction,
    minutes: Optional[app_commands.Range[int, 30, 1200]] = None,
    bio_mcqs: Optional[app_commands.Range[int, 0, 2000]] = None,
    phy_mcqs: Optional[app_commands.Range[int, 0, 2000]] = None,
    chem_mcqs: Optional[app_commands.Range[int, 0, 2000]] = None,
    total_mcqs: Optional[app_commands.Range[int, 10, 3000]] = None,
    topics: Optional[app_commands.Range[int, 1, 50]] = None,
):
    await interaction.response.defer(ephemeral=True)

    if minutes is not None:
        bot.db.set_config("goal_minutes", int(minutes))
    if bio_mcqs is not None:
        bot.db.set_config("goal_bio_mcqs", int(bio_mcqs))
    if phy_mcqs is not None:
        bot.db.set_config("goal_phy_mcqs", int(phy_mcqs))
    if chem_mcqs is not None:
        bot.db.set_config("goal_chem_mcqs", int(chem_mcqs))
    if total_mcqs is not None:
        bot.db.set_config("goal_total_mcqs", int(total_mcqs))
    elif any(v is not None for v in (bio_mcqs, phy_mcqs, chem_mcqs)):
        # Keep total_mcqs aligned with subject split if user updated splits without passing total_mcqs
        cur = bot.db.get_daily_goals()
        bot.db.set_config(
            "goal_total_mcqs",
            max(10, cur["bio_mcqs"] + cur["phy_mcqs"] + cur["chem_mcqs"]),
        )
    if topics is not None:
        bot.db.set_config("goal_topics", int(topics))

    goals = bot.db.get_daily_goals()
    await bot.refresh_all_dashboards()

    await interaction.followup.send(
        "🎯 **Active Daily Hunter Goals (Saved to Neon PostgreSQL):**\n"
        f"• **Study Time:** `{goals['minutes']} mins` (`{goals['minutes'] / 60.0:.1f} hrs`)\n"
        f"• **Total Daily MCQs:** `{goals['total_mcqs']} MCQs` "
        f"(Bio `{goals['bio_mcqs']}` • Phy `{goals['phy_mcqs']}` • Chem `{goals['chem_mcqs']}`)\n"
        f"• **Syllabus Topics:** `{goals['topics']}`\n"
        "*Quest Board updated immediately.*",
        ephemeral=True,
    )


@bot.tree.command(
    name="setup_world",
    description="Wipes all server channels/roles and rebuilds the RPG world while keeping your DB data.",
)
async def setup_world(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Must be run inside a server.", ephemeral=True)
        return

    owner_member = await bot.resolve_owner_member(guild)
    if not owner_member:
        await interaction.followup.send(
            f"❌ Could not find `ALLOWED_USER_ID` (`{ALLOWED_USER_ID}`) in this server.",
            ephemeral=True,
        )
        return

    # 1. Delete all existing channels and categories in the server (preserving current channel until last)
    current_channel_id = interaction.channel_id
    for ch in list(guild.channels):
        if ch.id == current_channel_id:
            continue
        try:
            await ch.delete(reason="NEET 2027 RPG Full Server Rebuild")
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not delete channel %s: %s", ch.name, exc)

    # 2. Delete all deletable roles below the bot's top role (except @everyone and managed bot roles)
    bot_top_pos = guild.me.top_role.position if guild.me else 0
    for role in list(guild.roles):
        if role.is_default() or role.managed or role.position >= bot_top_pos:
            continue
        try:
            await role.delete(reason="NEET 2027 RPG Full Server Rebuild")
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not delete role %s: %s", role.name, exc)

    # 3. Create fresh RPG Rank & Debuff roles
    for _, role_name, color_hex in RANK_TIERS:
        new_role = await guild.create_role(
            name=role_name,
            color=discord.Color(color_hex),
            hoist=True,
            reason="NEET 2027 RPG World Builder",
        )
        bot.db.set_config(f"role_{role_name}", new_role.id)

    fatigued_role = await guild.create_role(
        name=FATIGUED_ROLE_NAME,
        color=discord.Color(0xE74C3C),
        hoist=True,
        reason="NEET 2027 RPG Debuff Role",
    )
    bot.db.set_config(f"role_{FATIGUED_ROLE_NAME}", fatigued_role.id)

    # 4. Build private RPG categories and channels
    private_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
            manage_channels=True,
            embed_links=True,
            connect=True,
        ),
    }

    created_channels: Dict[str, discord.abc.GuildChannel] = {}

    for category_name, channels in WORLD_STRUCTURE.items():
        category = await guild.create_category(
            name=category_name,
            overwrites=private_overwrites,
            reason="NEET 2027 RPG World Builder",
        )

        for ch_name, ch_type in channels:
            if ch_type == "text":
                ch = await guild.create_text_channel(
                    name=ch_name,
                    category=category,
                    overwrites=private_overwrites,
                    reason="NEET 2027 RPG World Builder",
                )
            else:
                ch = await guild.create_voice_channel(
                    name=ch_name,
                    category=category,
                    overwrites=private_overwrites,
                    reason="NEET 2027 RPG World Builder",
                )

            bot.db.set_config(f"ch_{ch_name}", ch.id)
            created_channels[ch_name] = ch

    # 5. Pin fresh live dashboards backed by existing Neon PostgreSQL data
    async def pin_fresh_dashboard(
        ch_key: str,
        msg_key: str,
        embed: discord.Embed,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        ch = created_channels.get(ch_key)
        if not isinstance(ch, discord.TextChannel):
            return
        new_msg = await ch.send(embed=embed, view=view)
        await new_msg.pin(reason="RPG Live Dashboard")
        bot.db.set_config(msg_key, new_msg.id)

    await pin_fresh_dashboard(
        "quest-board", "msg_quest-board", build_quest_board_embed(bot.db), view=QuestBoardView(bot)
    )
    await pin_fresh_dashboard(
        "status-window", "msg_status-window", build_status_window_embed(bot.db)
    )
    await pin_fresh_dashboard(
        "boss-raids", "msg_boss-raids", build_boss_raids_embed(bot.db)
    )
    codex_embed, _, _ = build_chapter_codex_embed(bot.db, page=0)
    await pin_fresh_dashboard(
        "codex-topics", "msg_codex-topics", codex_embed, view=ChapterCodexView(bot)
    )

    shop_ch = created_channels.get("inventory-and-shop")
    if isinstance(shop_ch, discord.TextChannel):
        await shop_ch.send(embed=build_shop_embed(bot.db))

    # Re-post active uncleared curses to #cursed-vault so your vault channel stays populated
    vault_ch = created_channels.get("cursed-vault")
    if isinstance(vault_ch, discord.TextChannel):
        active_curses = bot.db.get_uncleared_curses(limit=25)
        if active_curses:
            c_lines = "\n".join(
                f"🔮 **ID `{c['id']}`** `[{c['subject']}]` — **{c['topic_or_note']}** *({c['source_test']})*"
                for c in active_curses
            )
            await vault_ch.send(
                embed=discord.Embed(
                    title="🩸 RESTORED CURSED VAULT",
                    description=f"{c_lines}\n\nCleanse via `/curse cleanse id:<ID>`.",
                    color=0x8E44AD,
                    timestamp=now_ist(),
                )
            )

    await bot.sync_hunter_roles(guild)

    # 6. Send confirmation into #action-terminal and delete the old channel where the command was run
    terminal_ch = created_channels.get("action-terminal")
    summary_text = (
        "✅ **Full Server Wipe & Rebuild Complete!**\n"
        "• Deleted all old channels, categories, and roles.\n"
        "• Rebuilt all RPG channels, roles, and pinned live dashboards.\n"
        "• **100% of your Neon PostgreSQL data** (XP, Level, Streak, Chapters, Curses, Goals & Inventory) was preserved."
    )
    if isinstance(terminal_ch, discord.TextChannel):
        await terminal_ch.send(content=f"<@{ALLOWED_USER_ID}>\n{summary_text}")

    try:
        await interaction.followup.send(summary_text, ephemeral=True)
    except discord.HTTPException:
        pass

    if current_channel_id and current_channel_id not in {c.id for c in created_channels.values()}:
        old_ch = guild.get_channel(current_channel_id)
        if old_ch:
            try:
                await old_ch.delete(reason="NEET 2027 RPG Full Server Rebuild")
            except (discord.Forbidden, discord.HTTPException):
                pass


# --- /chapters & /chapter_toggle & /chapter_add ---


@bot.tree.command(
    name="chapters",
    description="Open the interactive paginated NEET 2027 chapter & 5-stage mastery browser.",
)
async def chapters_command(interaction: discord.Interaction):
    embed, _, _ = build_chapter_codex_embed(bot.db, page=0)
    await interaction.response.send_message(embed=embed, view=ChapterCodexView(bot), ephemeral=True)


@bot.tree.command(
    name="chapter_toggle",
    description="Toggle one of the 5 mastery stages (20% each) on a NEET 2027 chapter.",
)
@app_commands.describe(
    chapter="NEET 2027 chapter name",
    stage="5-stage checklist item to toggle",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    stage=[
        app_commands.Choice(name="1. Concept + Short Notes (Notes)", value="short_notes"),
        app_commands.Choice(name="2. Topic-Wise Sweep (Topics)", value="topics_done"),
        app_commands.Choice(name="3. MCQ Grind (MCQs)", value="mcqs_done"),
        app_commands.Choice(name="4. Mistake / NCERT Sweep (Mistakes)", value="mistakes_logged"),
        app_commands.Choice(name="5. Test / Revision Lock (Revised)", value="revised"),
    ]
)
async def chapter_toggle_command(
    interaction: discord.Interaction,
    chapter: str,
    stage: app_commands.Choice[str],
):
    await interaction.response.defer(ephemeral=True)
    ok, msg, meta = bot.db.toggle_chapter_stage(chapter, stage.value)
    if not ok or not meta:
        await interaction.followup.send(msg, ephemeral=True)
        return

    chap_row = meta["chapter"]
    turned_on = meta["turned_on"]
    stage_key = meta["stage_key"]

    xp_delta = 0
    gold_delta = 0
    d_int = d_end = d_dex = d_wis = 0

    if turned_on:
        xp_delta = 90
        gold_delta = 40
        if stage_key == "short_notes":
            d_int = 2
        elif stage_key == "topics_done":
            d_int = 1
            d_wis = 1
        elif stage_key == "mcqs_done":
            d_dex = 3
        elif stage_key == "mistakes_logged":
            d_wis = 2
        elif stage_key == "revised":
            d_wis = 2
            d_end = 1
        if meta["just_defeated"]:
            xp_delta += 250
            gold_delta += 150
    else:
        xp_delta = -90
        gold_delta = -40

    prof = bot.db.get_profile()
    new_xp = max(0, prof["xp"] + xp_delta)
    new_gold = max(0, prof["gold"] + gold_delta)

    with bot.db._connect() as conn:
        conn.execute(
            """
            UPDATE hunter_profile
            SET xp = %s,
                gold = %s,
                stat_int = GREATEST(1, stat_int + %s),
                stat_end = GREATEST(1, stat_end + %s),
                stat_dex = GREATEST(1, stat_dex + %s),
                stat_wis = GREATEST(1, stat_wis + %s)
            WHERE user_id = %s
            """,
            (
                new_xp,
                new_gold,
                d_int if turned_on else 0,
                d_end if turned_on else 0,
                d_dex if turned_on else 0,
                d_wis if turned_on else 0,
                ALLOWED_USER_ID,
            ),
        )
        conn.commit()

    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        await bot.post_battle_receipt(
            "🏆 CHAPTER BOSS VANQUISHED!" if meta["just_defeated"] else "📘 CHAPTER STAGE UPDATED",
            f"**Chapter:** `{chap_row['chapter_name']}` ({chap_row['subject']} Cl {chap_row['class_level']})\n"
            f"**Stage:** `{STAGE_SHORT_LABELS[stage_key]}` -> `{'✅ DONE' if turned_on else '⬜ UNDONE'}`\n"
            f"**Mastery:** `{meta['pct']}%` (`{meta['stages_done']}/5`) • **Boss HP:** `{meta['boss_hp']}`\n"
            f"**XP / Gold Delta:** `{xp_delta:+d} XP`, `{gold_delta:+d} G`",
            color=0xF1C40F if meta["just_defeated"] else 0x1ABC9C,
        )

    await bot.refresh_all_dashboards()

    extra = ""
    if meta["just_defeated"]:
        extra += "\n🏆 **BOSS DEFEATED!** Bonus `+250 XP` & `+150 G` awarded!"
    if meta["trap_flag"]:
        extra += "\n🚨 **Notes-Only Trap Warning:** Notes are marked done without MCQs or Revision locked in yet."

    await interaction.followup.send(f"{msg}{extra}", ephemeral=True)


@bot.tree.command(
    name="chapter_add",
    description="Add a custom chapter entry to the NEET 2027 chapter tracker.",
)
@app_commands.describe(
    subject="Physics, Chemistry, or Biology",
    class_level="11 or 12",
    chapter="Chapter name",
)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chemistry", value="Chemistry"),
        app_commands.Choice(name="Biology", value="Biology"),
    ],
    class_level=[
        app_commands.Choice(name="Class 11", value=11),
        app_commands.Choice(name="Class 12", value=12),
    ],
)
async def chapter_add_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    class_level: app_commands.Choice[int],
    chapter: str,
):
    await interaction.response.defer(ephemeral=True)
    ok, msg = bot.db.add_custom_chapter(subject.value, class_level.value, chapter)
    if ok:
        await bot.refresh_all_dashboards()
    await interaction.followup.send(msg, ephemeral=True)


# --- /exam_mode, /queue_add, /next_topic, /redeem, /weekly_report ---


@bot.tree.command(
    name="exam_mode",
    description="Toggle Exam Mode on/off for test-taking & error-extraction focus.",
)
@app_commands.describe(
    state="Turn Exam Mode on or off",
    test_name="Optional test/mock name (e.g., Allen Major Test 05)",
)
@app_commands.choices(
    state=[
        app_commands.Choice(name="On", value="on"),
        app_commands.Choice(name="Off", value="off"),
    ]
)
async def exam_mode_command(
    interaction: discord.Interaction,
    state: app_commands.Choice[str],
    test_name: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)
    is_on = state.value == "on"
    prof = bot.db.set_exam_mode(is_on, test_name=test_name)
    await bot.refresh_all_dashboards()

    if is_on:
        lbl = prof.get("exam_mode_test_name") or "NEET Mock / Part Test"
        msg = (
            f"🎯 **EXAM MODE ENABLED:** `{lbl}`\n"
            "• Bonus WIS XP & extra diagnostic XP active on `/gemini_analyze` and `/curse`.\n"
            "• Focus sessions during this window are tracked as test/exam prep blocks."
        )
    else:
        msg = "📚 **Exam Mode Disabled.** Returned to standard daily prep mode."
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="queue_add",
    description="Add a topic to your Next-Topic Queue tied to NEET 2027 & Dec 15, 2026.",
)
@app_commands.describe(
    subject="Bio, Physics, or Chem",
    chapter="NEET 2027 chapter name",
    topic="Subtopic or NCERT point to cover",
    priority="1 = High (Default 2, 3 = Low)",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Bio", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chem", value="Chem"),
    ],
    priority=[
        app_commands.Choice(name="1 - High Priority", value=1),
        app_commands.Choice(name="2 - Normal Priority", value=2),
        app_commands.Choice(name="3 - Low Priority", value=3),
    ],
)
async def queue_add_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    chapter: str,
    topic: str,
    priority: Optional[int] = 2,
):
    await interaction.response.defer(ephemeral=True)
    ok, msg = bot.db.add_queue_topic(subject.value, chapter, topic, priority or 2)
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="next_topic",
    description="View highest-priority queued topic vs Dec 15, 2026 pace, or complete it.",
)
@app_commands.describe(
    action="View next topic or mark it completed as Concept or Revision",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="View Next Topic & Pace", value="view"),
        app_commands.Choice(name="Complete as Concept (+INT)", value="complete_concept"),
        app_commands.Choice(name="Complete as Revision (+WIS)", value="complete_revision"),
    ]
)
async def next_topic_command(
    interaction: discord.Interaction,
    action: Optional[str] = "view",
):
    await interaction.response.defer(ephemeral=True)
    item = bot.db.get_next_pending_topic()
    if not item:
        await interaction.followup.send(
            "📭 Your Next-Topic Queue is empty! Add topics with `/queue_add`.", ephemeral=True
        )
        return

    if action in ("complete_concept", "complete_revision"):
        completed = bot.db.complete_queue_topic(item["id"])
        if completed:
            mode_str = "Concept" if action == "complete_concept" else "Revision"
            receipt = await bot.process_topic_log(
                subject=completed["subject"],
                chapter=completed["chapter_name"],
                topic=completed["topic_name"],
                mode=mode_str,
            )
            next_up = bot.db.get_next_pending_topic()
            next_txt = (
                f"\n\n⏭️ **Up Next:** `[#{next_up['id']}] {next_up['chapter_name']} — {next_up['topic_name']}`"
                if next_up
                else "\n\n🎉 **Queue Cleared!**"
            )
            await interaction.followup.send(f"✅ **Queued Topic Completed!**\n{receipt}{next_txt}", ephemeral=True)
            return

    days_left = max(0, (TARGET_MASTERY_DATE - study_day_ist_date()).days)
    cleared, total = bot.db.get_syllabus_totals()
    rem_units = max(0, total - cleared)
    pending_q = bot.db.get_pending_queue_count()
    pace = (rem_units / days_left) if days_left > 0 else float(rem_units)

    embed = discord.Embed(
        title=f"⏭️ NEXT TOPIC IN QUEUE — [#{item['id']}] P{item['priority']}",
        description=(
            f"**Subject:** `{item['subject']}` • **Chapter:** `{item['chapter_name']}`\n"
            f"**Topic:** `{item['topic_name']}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Queue Pending:** `{pending_q}` topics\n"
            f"• **Dec 15, 2026 Countdown:** `{days_left} days` (`{pace:.2f} stages/day` required)"
        ),
        color=0x3498DB,
        timestamp=now_ist(),
    )
    embed.set_footer(
        text="Run /next_topic action:complete_concept or complete_revision to log and advance."
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="redeem",
    description="Check or claim your 24-hour Streak Recovery Quest after a missed audit.",
)
@app_commands.describe(action="Check status or claim completed recovery quest")
@app_commands.choices(
    action=[
        app_commands.Choice(name="Check Recovery Status", value="status"),
        app_commands.Choice(name="Claim Recovery Reward (+35 HP)", value="claim"),
    ]
)
async def redeem_command(interaction: discord.Interaction, action: Optional[str] = "status"):
    await interaction.response.defer(ephemeral=True)
    if action == "claim":
        ok, msg = bot.db.claim_recovery_quest()
        if ok:
            if interaction.guild:
                await bot.sync_hunter_roles(interaction.guild)
            await bot.post_battle_receipt("⛓️‍💥 STREAK RECOVERY CLAIMED", msg, color=0x2ECC71)
            await bot.refresh_all_dashboards()
        await interaction.followup.send(msg, ephemeral=True)
        return

    rq = bot.db.get_active_recovery_quest()
    if not rq:
        await interaction.followup.send(
            "ℹ️ No active 24-hour Streak Recovery Quest right now.", ephemeral=True
        )
        return

    exp_dt = datetime.fromisoformat(rq["expires_at_iso"])
    embed = discord.Embed(
        title="⛓️ 24-HOUR STREAK RECOVERY QUEST",
        description=(
            f"**Expires:** `{exp_dt.strftime('%d %b %Y %I:%M %p IST')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Study Time:** `{rq['prog_minutes']} / {rq['req_minutes']} mins`\n"
            f"• **MCQ Volume:** `{rq['prog_mcqs']} / {rq['req_mcqs']} MCQs`\n"
            f"• **Curses Cleansed:** `{rq['prog_curses_cleansed']} / {rq['req_curses_cleansed']}`"
        ),
        color=0xE67E22,
        timestamp=now_ist(),
    )
    embed.set_footer(text="Once all 3 targets are met before expiration, run /redeem action:claim.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="weekly_report",
    description="Generate your rolling 7-day NEET 2027 accountability & pace report.",
)
async def weekly_report_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=build_weekly_report_embed(bot.db), ephemeral=True)


# --- /boss, /focus, /mcq, /topic, /test_log, /curse, /shop, /buy, /use, /shield, /stats ---

boss_group = app_commands.Group(name="boss", description="Manage NEET Chapter Bosses and track syllabus HP.")


@boss_group.command(name="add", description="Spawn a Chapter Boss with HP proportional to total_topics.")
@app_commands.describe(
    subject="Subject name",
    chapter="Chapter name",
    total_topics="Total sub-topics in this chapter (100 HP per topic)",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
async def boss_add(
    interaction: discord.Interaction,
    subject: str,
    chapter: str,
    total_topics: app_commands.Range[int, 1, 100],
):
    await interaction.response.defer(ephemeral=True)
    matched, _ = bot.db.resolve_chapter(chapter)
    chap_title = matched["chapter_name"] if matched else chapter.strip()

    with bot.db._connect() as conn:
        existing = conn.execute(
            "SELECT * FROM bosses WHERE LOWER(subject) = LOWER(%s) AND LOWER(chapter) = LOWER(%s)",
            (subject.strip(), chap_title),
        ).fetchone()
        if existing:
            new_total = max(total_topics, existing["completed_topics"])
            status = "DEFEATED" if existing["completed_topics"] >= new_total else "ACTIVE"
            conn.execute(
                "UPDATE bosses SET total_topics = %s, status = %s WHERE id = %s",
                (new_total, status, existing["id"]),
            )
            boss_id = existing["id"]
        else:
            row = conn.execute(
                """
                INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                VALUES (%s, %s, %s, 0, 'ACTIVE', %s)
                RETURNING id
                """,
                (subject.strip(), chap_title, total_topics, now_ist().isoformat()),
            ).fetchone()
            boss_id = row["id"]
        conn.commit()
        boss = dict(conn.execute("SELECT * FROM bosses WHERE id = %s", (boss_id,)).fetchone())

    await bot.refresh_all_dashboards()
    max_hp = boss["total_topics"] * BOSS_HP_PER_TOPIC
    cur_hp = max(0, boss["total_topics"] - boss["completed_topics"]) * BOSS_HP_PER_TOPIC
    await interaction.followup.send(
        f"👹 **Chapter Boss Ready!** `[ID {boss['id']}] {boss['subject']} — {boss['chapter']}`\n"
        f"**HP:** `{cur_hp} / {max_hp} HP`",
        ephemeral=True,
    )


@boss_group.command(name="list", description="View all active and defeated Chapter Bosses.")
async def boss_list(interaction: discord.Interaction):
    with bot.db._connect() as conn:
        bosses = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM bosses ORDER BY CASE WHEN status = 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 15"
            ).fetchall()
        ]
    if not bosses:
        await interaction.response.send_message("📭 No Chapter Bosses yet.", ephemeral=True)
        return

    lines = []
    for b in bosses:
        rem_t = max(0, b["total_topics"] - b["completed_topics"])
        cur_hp = rem_t * BOSS_HP_PER_TOPIC
        max_hp = b["total_topics"] * BOSS_HP_PER_TOPIC
        pct = int(round((b["completed_topics"] / max(1, b["total_topics"])) * 100))
        icon = "🏆" if b["status"] == "DEFEATED" else "👹"
        lines.append(
            f"{icon} **[ID {b['id']}] {b['subject']} — {b['chapter']}** (`{pct}%`) — `{cur_hp}/{max_hp} HP`"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


bot.tree.add_command(boss_group)

focus_group = app_commands.Group(name="focus", description="Manual study focus session timer controls.")


@focus_group.command(name="start", description="Starts a manual deep-focus study timer.")
async def focus_start(interaction: discord.Interaction):
    started, iso_str = bot.db.start_focus(source="Manual /focus")
    if not started:
        start_dt = datetime.fromisoformat(iso_str)
        elapsed = int((now_ist() - start_dt).total_seconds() // 60)
        await interaction.response.send_message(
            f"⚠️ Focus session already active (`{elapsed} mins` elapsed since `{start_dt.strftime('%I:%M %p IST')}`).",
            ephemeral=True,
        )
        return
    await bot.refresh_all_dashboards()
    await interaction.response.send_message("🔥 **Focus Session Started!**", ephemeral=True)


@focus_group.command(name="stop", description="Stops the active focus session and claims XP, Gold & END.")
async def focus_stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = bot.db.stop_focus()
    if not result:
        await interaction.followup.send("⚠️ No active focus session running.", ephemeral=True)
        return
    mins, src = result
    summary = await bot.finalize_focus_session(mins, src)
    await interaction.followup.send(summary, ephemeral=True)


bot.tree.add_command(focus_group)


@bot.tree.command(name="mcq", description="Log completed practice MCQs for Bio, Physics, or Chem.")
@app_commands.describe(subject="Bio, Physics, or Chem", count="Number of MCQs solved")
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Bio", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chem", value="Chem"),
    ]
)
async def mcq_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    count: app_commands.Range[int, 1, 2000],
):
    await interaction.response.defer(ephemeral=True)
    receipt = await bot.process_mcq_log(subject=subject.value, count=count, source="/mcq command")
    await interaction.followup.send(f"✅ **MCQs Logged!**\n{receipt}", ephemeral=True)


topic_group = app_commands.Group(name="topic", description="Track syllabus topic completions and boss raid progress.")


@topic_group.command(name="done", description="Log a completed Concept or Revision topic to damage the Syllabus Boss.")
@app_commands.describe(
    subject="Subject name",
    chapter="NEET 2027 chapter name",
    topic="Specific subtopic or NCERT section",
    mode="Concept (+INT) or Revision (+WIS)",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Concept", value="Concept"),
        app_commands.Choice(name="Revision", value="Revision"),
    ]
)
async def topic_done(
    interaction: discord.Interaction,
    subject: str,
    chapter: str,
    topic: str,
    mode: app_commands.Choice[str],
):
    await interaction.response.defer(ephemeral=True)
    matched, _ = bot.db.resolve_chapter(chapter)
    chap_title = matched["chapter_name"] if matched else chapter.strip()
    receipt = await bot.process_topic_log(
        subject=subject,
        chapter=chap_title,
        topic=topic,
        mode=mode.value,
    )
    await interaction.followup.send(f"✅ **Topic Cleared!**\n{receipt}", ephemeral=True)


bot.tree.add_command(topic_group)


@bot.tree.command(name="test_log", description="Log a mock/part test score and banish weak topics to #cursed-vault.")
@app_commands.describe(
    name="Name of the test",
    score="Marks scored",
    max_score="Maximum marks of the test",
    wrong_topics="Comma-separated weak/wrong topics (or 'None')",
)
async def test_log(
    interaction: discord.Interaction,
    name: str,
    score: int,
    max_score: app_commands.Range[int, 1, 2000],
    wrong_topics: str,
):
    await interaction.response.defer(ephemeral=True)
    pct = round((score / max_score) * 100, 2)
    prof_before = bot.db.get_profile()
    exam_active = bool(prof_before.get("exam_mode_active"))

    xp_gain = max(150, int(pct * 5))
    gold_gain = max(75, int(pct * 2.5))
    wis_gain = 4 if exam_active else 2

    curses_added = bot.db.log_test_and_curses(name, score, max_score, wrong_topics)
    prof, leveled_up = bot.db.award_rewards(
        xp_gain=xp_gain,
        gold_gain=gold_gain,
        d_int=2,
        d_wis=wis_gain,
        d_tests=1,
    )

    guild = interaction.guild
    rank_msg = await bot.sync_hunter_roles(guild) if guild else None

    receipt = (
        f"**Mock Test:** `{name}`\n"
        f"**Score:** `{score} / {max_score}` (**{pct}%**)\n"
        f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+2 INT` • `+{wis_gain} WIS`\n"
        f"**Cursed Topics Banished:** `{len(curses_added)}`"
    )
    if leveled_up:
        receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    if rank_msg:
        receipt += f"\n{rank_msg}"

    await bot.post_battle_receipt("📝 MOCK TEST BATTLE REPORT", receipt, color=0xF1C40F)

    if curses_added and guild:
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id:
            vault_ch = guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                lines = "\n".join(
                    f"🔮 **ID `{cid}`** | `{topic_txt}` *(from {name})*"
                    for cid, topic_txt in curses_added
                )
                await vault_ch.send(
                    embed=discord.Embed(
                        title=f"🩸 NEW CURSES SEALED — {name}",
                        description=f"{lines}\n\nCleanse with `/curse cleanse id:<ID>`.",
                        color=0x8E44AD,
                        timestamp=now_ist(),
                    )
                )

    await bot.refresh_all_dashboards()
    await interaction.followup.send(f"✅ **Test Recorded!**\n{receipt}", ephemeral=True)


curse_group = app_commands.Group(name="curse", description="Mistake Vault & Cursed Raids.")


@curse_group.command(name="add", description="Seal a tricky question, NCERT line, or mistake into #cursed-vault (+WIS).")
@app_commands.describe(subject="Subject name", note="Mistake or NCERT point")
async def curse_add(interaction: discord.Interaction, subject: str, note: str):
    await interaction.response.defer(ephemeral=True)
    curse_id = bot.db.add_curse(subject, note, source_test="Manual Mistake Log")
    exam_active = bool(bot.db.get_profile().get("exam_mode_active"))
    wis_gain = 2 if exam_active else 1
    xp_gain = 55 if exam_active else 35

    prof, leveled_up = bot.db.award_rewards(xp_gain=xp_gain, gold_gain=15, d_wis=wis_gain)

    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id:
            vault_ch = interaction.guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                await vault_ch.send(
                    embed=discord.Embed(
                        title=f"🩸 CURSE SEALED [ID `{curse_id}`] — {subject.upper()}",
                        description=f"**Mistake / NCERT Trap:** `{note}`\n*Cleanse with `/curse cleanse id:{curse_id}`*",
                        color=0x8E44AD,
                        timestamp=now_ist(),
                    )
                )

    await bot.refresh_all_dashboards()
    msg = (
        f"🩸 **Mistake Sealed in Cursed Vault (ID `{curse_id}`)**\n"
        f"**Rewards:** `+{xp_gain} XP` • `+15 Gold` • `+{wis_gain} WIS`"
    )
    if leveled_up:
        msg += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    await interaction.followup.send(msg, ephemeral=True)


@curse_group.command(name="raid", description="Spawn a Cursed Raid session presenting up to 5 uncleared mistakes.")
async def curse_raid(interaction: discord.Interaction):
    curses = bot.db.get_uncleared_curses(limit=5)
    if not curses:
        await interaction.response.send_message("✨ **Zero active curses!**", ephemeral=True)
        return
    lines = [
        f"⚔️ **ID `{c['id']}`** `[{c['subject']}]` — **{c['topic_or_note']}** *({c['source_test']})*"
        for c in curses
    ]
    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"👹 CURSED RAID ({len(curses)} TARGETS)",
            description="\n\n".join(lines) + "\n\nCleanse via `/curse cleanse id:<ID>`.",
            color=0x8E44AD,
            timestamp=now_ist(),
        ),
        ephemeral=True,
    )


@curse_group.command(name="cleanse", description="Mark a cursed mistake as mastered (+WIS, +XP, +Gold).")
@app_commands.describe(id="Numeric ID of the curse to cleanse")
async def curse_cleanse(interaction: discord.Interaction, id: int):
    await interaction.response.defer(ephemeral=True)
    cleansed = bot.db.cleanse_curse(id)
    if not cleansed:
        await interaction.followup.send(f"❌ No active curse found with ID `{id}`.", ephemeral=True)
        return

    exam_active = bool(bot.db.get_profile().get("exam_mode_active"))
    wis_gain = 3 if exam_active else 2
    xp_gain = 110 if exam_active else 80

    prof, leveled_up = bot.db.award_rewards(xp_gain=xp_gain, gold_gain=40, d_wis=wis_gain)
    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id:
            vault_ch = interaction.guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                await vault_ch.send(
                    f"✨ **CLEANSED [ID `{id}`]:** ~~{cleansed['topic_or_note']}~~ *({cleansed['subject']})*"
                )

    await bot.refresh_all_dashboards()
    msg = (
        f"✨ **CURSE CLEANSED [ID `{id}`]!**\n"
        f"**Mastered:** `{cleansed['topic_or_note']}`\n"
        f"**Rewards:** `+{xp_gain} XP` • `+40 Gold` • `+{wis_gain} WIS`"
    )
    if leveled_up:
        msg += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    await bot.post_battle_receipt("✨ CURSED RAID CLEANSE", msg, color=0x1ABC9C)
    await interaction.followup.send(msg, ephemeral=True)


bot.tree.add_command(curse_group)


@bot.tree.command(name="shop", description="View purchasable rewards, Rest Shields, and your Gold balance.")
async def shop_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = build_shop_embed(bot.db)
    if interaction.guild:
        shop_id = bot.db.get_config("ch_inventory-and-shop")
        if shop_id:
            shop_ch = interaction.guild.get_channel(shop_id)
            if isinstance(shop_ch, discord.TextChannel):
                await shop_ch.send(embed=embed)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="buy", description="Purchase a Rest Shield or real-life reward token with Gold.")
@app_commands.choices(
    item=[
        app_commands.Choice(name="Rest / Post-Test Shield (450 Gold)", value="rest_shield"),
        app_commands.Choice(name="Anime / Show Episode Token (200 Gold)", value="anime_episode"),
        app_commands.Choice(name="Cheat Meal Voucher (500 Gold)", value="cheat_meal"),
        app_commands.Choice(name="Guilt-Free 1-Hour Break Block (350 Gold)", value="break_block"),
    ]
)
async def buy_command(interaction: discord.Interaction, item: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    ok, msg = bot.db.buy_shop_item(item.value)
    if ok:
        await bot.post_battle_receipt("🛒 ARMORY PURCHASE", msg, color=0xF1C40F)
        await bot.refresh_all_dashboards()
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="use", description="Redeem an item or activate a Rest Shield from your inventory.")
@app_commands.choices(
    item=[
        app_commands.Choice(name="Rest / Post-Test Shield", value="rest_shield"),
        app_commands.Choice(name="Anime / Show Episode Token", value="anime_episode"),
        app_commands.Choice(name="Cheat Meal Voucher", value="cheat_meal"),
        app_commands.Choice(name="Guilt-Free 1-Hour Break Block", value="break_block"),
    ]
)
async def use_command(interaction: discord.Interaction, item: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    ok, msg = bot.db.use_inventory_item(item.value)
    if ok:
        await bot.post_battle_receipt("🎟️ INVENTORY ITEM ACTIVATED", msg, color=0x3498DB)
        await bot.refresh_all_dashboards()
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="shield", description="Activate a Rest / Post-Test Shield for today (max 2 per 7 days).")
async def shield_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    ok, msg = bot.db.activate_rest_shield()
    if ok:
        await bot.post_battle_receipt("🛡️ REST SHIELD DEPLOYED", msg, color=0x3498DB)
        await bot.refresh_all_dashboards()
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="stats", description="Refreshes all live dashboards and displays your Hunter Status Window.")
async def stats_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
    await bot.refresh_all_dashboards()
    await interaction.followup.send(embed=build_status_window_embed(bot.db), ephemeral=True)


# --- /gemini_quiz & /gemini_analyze ---


@bot.tree.command(
    name="gemini_quiz",
    description="Generate 10-20 NEET 2027 MCQs via Gemini with interactive answer buttons & DEX XP.",
)
@app_commands.describe(
    subject="Bio, Physics, or Chem",
    chapter="NEET 2027 chapter name",
    count="Number of questions to generate (10 to 20)",
    focus_topic="Optional specific subtopic or NCERT point",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Bio", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chem", value="Chem"),
    ]
)
async def gemini_quiz_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    chapter: str,
    count: app_commands.Range[int, 10, 20],
    focus_topic: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)
    matched, candidates = bot.db.resolve_chapter(chapter)
    chap_name = matched["chapter_name"] if matched else chapter.strip()
    if not matched and candidates:
        chap_name = candidates[0].split(" (")[0]

    sys_inst = (
        "You are a NEET 2027 exam question author. Generate strictly NEET/NCERT-aligned "
        "single-choice MCQs with zero JEE-advanced or Olympiad detours. Each option must be "
        "prefixed as 'A) ', 'B) ', 'C) ', 'D) '."
    )
    focus_clause = f" Focus specifically on: {focus_topic}." if focus_topic else ""
    prompt = (
        f"Generate {int(count)} NEET 2027 multiple-choice questions for Subject: {subject.value}, "
        f"Chapter: {chap_name}.{focus_clause} Return JSON matching the schema."
    )

    parsed_obj, err = await bot.gemini._call_structured(prompt, sys_inst, QuizSetPayload)
    if err or not isinstance(parsed_obj, QuizSetPayload):
        await interaction.followup.send(err or "⚠️ Failed to generate quiz set.", ephemeral=True)
        return

    q_dicts = [q.model_dump() for q in parsed_obj.questions[: int(count)]]
    session_id = bot.db.save_quiz_session(
        subject=subject.value,
        chapter=chap_name,
        focus_topic=focus_topic,
        questions_data=q_dicts,
    )
    sess = bot.db.get_quiz_session(session_id)
    embed = build_gemini_quiz_question_embed(sess, 0)
    view = GeminiQuizView(bot, session_id=session_id, q_idx=0)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(
    name="gemini_analyze",
    description="Diagnose Strength & Focus areas via Gemini, log weak spots to #cursed-vault & earn WIS XP.",
)
@app_commands.describe(
    subject="Bio, Physics, or Chem",
    chapter="NEET 2027 chapter name",
    performance_notes="Missed question numbers, wrong topics, or test/practice notes",
    session_id="Optional Gemini quiz session ID to include question context",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Bio", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chem", value="Chem"),
    ]
)
async def gemini_analyze_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    chapter: str,
    performance_notes: str,
    session_id: Optional[int] = None,
):
    await interaction.response.defer(ephemeral=True)
    matched, _ = bot.db.resolve_chapter(chapter)
    chap_name = matched["chapter_name"] if matched else chapter.strip()

    quiz_ctx = None
    sess = bot.db.get_quiz_session(session_id) if session_id is not None else None
    if sess:
        lines = []
        for idx, q in enumerate(sess.get("questions", []), start=1):
            ua = q.get("user_answer", "Unanswered")
            co = q.get("correct_option", "?")
            st = q.get("subtopic_tag", "Topic")
            lines.append(f"Q{idx} [{st}]: user={ua}, correct={co} — {q.get('question', '')[:120]}")
        quiz_ctx = "\n".join(lines)

    sys_inst = (
        "You are a direct, matter-of-fact NEET 2027 performance coach. Diagnose strength areas, "
        "high-priority focus areas (NCERT traps/subtopics), and give one concrete action step."
    )
    prompt = (
        f"Subject: {subject.value}\nChapter: {chap_name}\n"
        f"Student Notes / Missed Items: {performance_notes}\n"
        + (f"Quiz Session Telemetry:\n{quiz_ctx}\n" if quiz_ctx else "")
        + "Provide structured strength_areas, focus_areas, action_step, and accuracy_summary."
    )

    report, err = await bot.gemini._call_structured(prompt, sys_inst, DiagnosticReportPayload)
    if err or not isinstance(report, DiagnosticReportPayload):
        await interaction.followup.send(err or "⚠️ Failed to analyze performance.", ephemeral=True)
        return

    logged_curses: List[Tuple[int, str]] = []
    for focus_item in report.focus_areas:
        cleaned = focus_item.strip()
        if cleaned:
            cid = bot.db.add_curse(
                subject=subject.value,
                note=f"[{chap_name}] {cleaned}",
                source_test=f"Gemini Diagnostic ({chap_name})",
            )
            logged_curses.append((cid, cleaned))

    exam_active = bool(bot.db.get_profile().get("exam_mode_active"))
    wis_gain = 4 if exam_active else 2
    xp_gain = 150 if exam_active else 100

    prof, leveled_up = bot.db.award_rewards(xp_gain=xp_gain, gold_gain=50, d_wis=wis_gain)
    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id and logged_curses:
            vault_ch = interaction.guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                vault_lines = "\n".join(
                    f"🔮 **ID `{cid}`** | `{item}` *({chap_name})*" for cid, item in logged_curses
                )
                await vault_ch.send(
                    embed=discord.Embed(
                        title=f"🩸 GEMINI FOCUS AREAS SEALED — {chap_name}",
                        description=f"{vault_lines}\n\nCleanse with `/curse cleanse id:<ID>`.",
                        color=0x8E44AD,
                        timestamp=now_ist(),
                    )
                )

    await bot.refresh_all_dashboards()

    strength_txt = "\n".join(f"• {s}" for s in report.strength_areas) or "• None listed"
    focus_txt = (
        "\n".join(f"• {f} *(Curse #{cid})*" for (cid, f) in logged_curses)
        if logged_curses
        else ("\n".join(f"• {f}" for f in report.focus_areas) or "• None flagged")
    )

    diag_embed = discord.Embed(
        title=f"🩺 GEMINI DIAGNOSTIC — {subject.value} ({chap_name})",
        description=(
            f"**Summary:** {report.accuracy_summary}\n"
            f"**Rewards:** `+{xp_gain} XP` • `+50 Gold` • `+{wis_gain} WIS` • `{len(logged_curses)}` curses logged to `#cursed-vault`"
            + (f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!" if leveled_up else "")
        ),
        color=0x9B59B6,
        timestamp=now_ist(),
    )
    diag_embed.add_field(name="💪 Strength Areas", value=strength_txt, inline=False)
    diag_embed.add_field(name="🎯 Focus Areas (Logged to #cursed-vault)", value=focus_txt, inline=False)
    diag_embed.add_field(name="⚡ Next Action Step", value=report.action_step, inline=False)

    await interaction.followup.send(embed=diag_embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        return
    logger.error("Command error: %s\n%s", error, traceback.format_exc())
    err_msg = f"⚠️ **System Glitch:** `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(err_msg, ephemeral=True)
    else:
        await interaction.response.send_message(err_msg, ephemeral=True)


# ==============================================================================
# 8. KEEP-ALIVE SERVER (RENDER + UPTIMEROBOT) & ENTRY POINT
# ==============================================================================


class KeepAliveHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP handler supporting HEAD and GET pings for UptimeRobot on Render."""

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"NEET 2027 Accountability RPG Bot is alive.")

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_keep_alive_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Keep-alive HTTP server listening on 0.0.0.0:%s (HEAD & GET ready)", port)


if __name__ == "__main__":
    start_keep_alive_server()
    bot.run(DISCORD_TOKEN)
