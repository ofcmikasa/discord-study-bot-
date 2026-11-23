"""
NEET 2027 Accountability RPG Discord Bot (Single-File Architecture)
==================================================================
A private, single-user gamified study RPG bot built with discord.py (v2.7+)
and SQLite3, tailored for NEET 2027 preparation with a syllabus mastery
deadline of December 15, 2026 (Asia/Kolkata IST).

Includes:
1. Pre-Seeded NEET 2027-Only Chapters (Class 11 & 12 across Physics,
   Chemistry, and Biology — deleted NMC/NCERT chapters excluded).
2. Common 5-Stage Per-Chapter Revision Checklist (Notes, Topics, MCQs,
   Mistakes, Revised = 20% each), interactive paginated browser in
   #codex-topics & /chapters, /chapter_toggle with autocomplete, Boss HP sync,
   and Notes-Only Trap detection.
3. Exam Mode (/exam_mode) with +50% WIS XP on tests/mistakes, clean test
   session handling, and live badges on #quest-board and #status-window.
4. Rolling 7-Day Weak-Subject & Habit Balance Tracker (Bio vs. Physics MCQs
   and Notes/Concept vs. MCQ/Revision execution) with clear balance indicators
   and XP nudges.
5. Next-Topic Queue (/queue_add & /next_topic) with priority, completion
   actions, and live pace math toward December 15, 2026.
6. 24-Hour Streak Recovery Quest (/redeem) triggered automatically when a
   streak breaks at the 23:55 IST audit.
7. Weekly Report (/weekly_report + Sunday 21:00 IST auto-post).
"""

import os
import sqlite3
import logging
import traceback
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Render injects environment variables natively; python-dotenv is optional
    pass

# ==============================================================================
# 1. ENVIRONMENT, LOGGING & CONSTANTS
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("StudyRPG")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID")
ALLOWED_USER_ID_RAW = os.getenv("ALLOWED_USER_ID")

missing_vars = [
    name for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("GUILD_ID", GUILD_ID_RAW),
        ("ALLOWED_USER_ID", ALLOWED_USER_ID_RAW),
    ]
    if not val
]

if missing_vars:
    raise RuntimeError(
        f"Missing required environment variable(s): {', '.join(missing_vars)}. "
        "Add them in Render -> Environment (or your local .env file)."
    )

GUILD_ID = int(GUILD_ID_RAW)
ALLOWED_USER_ID = int(ALLOWED_USER_ID_RAW)

# Timezone & Target Constants
IST = ZoneInfo("Asia/Kolkata")
TARGET_MASTERY_DATE = date(2026, 12, 15)
TOTAL_SYLLABUS_TOPICS = 180  # Baseline NEET syllabus topics
BOSS_HP_PER_TOPIC = 100      # Each topic in a chapter boss equals 100 Boss HP

# Daily Quest Targets (Audited at 23:55 IST / Reset after audit)
DAILY_TARGET_MINUTES = 360   # 6 Hours -> END Stat
DAILY_TARGET_BIO = 300       # 300 Bio MCQs -> DEX Stat
DAILY_TARGET_PHY = 120       # 120 Physics MCQs -> DEX Stat
DAILY_TARGET_TOPICS = 1      # >= 1 Topic Concept/Revision -> INT / WIS Stat

# 5-Stage Revision Checklist Definitions
REVISION_STAGES: List[Tuple[str, str, str]] = [
    ("short_notes", "Notes", "Concept + Short Notes"),
    ("topics_done", "Topics", "Topic-Wise Sweep"),
    ("mcqs_done", "MCQs", "MCQ Grind"),
    ("mistakes_logged", "Mistakes", "Mistake / NCERT Sweep"),
    ("revised", "Revised", "Test / Revision Lock"),
]
STAGE_KEYS = [s[0] for s in REVISION_STAGES]

# NEET 2027-Only Syllabus Chapters (Excludes deleted NCERT/NMC chapters)
NEET_2027_CHAPTERS: List[Tuple[str, int, str]] = [
    # Physics — Class 11 (10 chapters)
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
    # Physics — Class 12 (10 chapters)
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

    # Chemistry — Class 11 (9 chapters)
    ("Chemistry", 11, "Some Basic Concepts of Chemistry"),
    ("Chemistry", 11, "Structure of Atom"),
    ("Chemistry", 11, "Classification of Elements and Periodicity in Properties"),
    ("Chemistry", 11, "Chemical Bonding and Molecular Structure"),
    ("Chemistry", 11, "Chemical Thermodynamics"),
    ("Chemistry", 11, "Equilibrium"),
    ("Chemistry", 11, "Redox Reactions"),
    ("Chemistry", 11, "Organic Chemistry: Some Basic Principles and Techniques"),
    ("Chemistry", 11, "Hydrocarbons"),
    # Chemistry — Class 12 (11 chapters)
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

    # Biology — Class 11 (19 chapters)
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
    # Biology — Class 12 (13 chapters)
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

# RPG Progression Ranks (Level Threshold -> Role Name & Color)
RANK_TIERS: List[Tuple[int, str, int]] = [
    (1,  "E-Rank Novice",          0x95A5A6),
    (5,  "D-Rank Apprentice",      0x2ECC71),
    (12, "C-Rank Aspirant",        0x3498DB),
    (22, "B-Rank Vanguard",        0x9B59B6),
    (35, "A-Rank Grandmaster",     0xE67E22),
    (50, "S-Rank NEET Sovereign",  0xF1C40F),
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


def now_ist() -> datetime:
    """Returns the current timezone-aware datetime in Asia/Kolkata (IST)."""
    return datetime.now(IST)


def today_ist_str() -> str:
    """Returns today's date string (YYYY-MM-DD) in IST."""
    return now_ist().date().isoformat()


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
    """Renders the 5-stage mastery bar (20% per stage)."""
    c = max(0, min(5, completed_stages))
    return f"[{'🟩' * c}{'⬜' * (5 - c)}]"


def get_rank_for_level(level: int) -> str:
    """Determines the Hunter Rank title for a given character level."""
    current_rank = RANK_TIERS[0][1]
    for req_lvl, rank_name, _ in RANK_TIERS:
        if level >= req_lvl:
            current_rank = rank_name
    return current_rank


def normalize_subject_name(subj: str) -> str:
    s = subj.strip().lower()
    if s in ("bio", "biology", "botany", "zoology"):
        return "Biology"
    if s in ("phy", "physics"):
        return "Physics"
    if s in ("chem", "chemistry", "organic chem", "inorganic chem", "physical chem"):
        return "Chemistry"
    return subj.strip().capitalize()


# ==============================================================================
# 2. SQLITE DATABASE MANAGER
# ==============================================================================

class DatabaseManager:
    """
    Thread-safe SQLite3 persistence layer for `study_rpg.db`.
    Handles NEET 2027 chapter seeding, 5-stage revision tracking, 7-day
    balance telemetry, topic queues, 24h streak recovery quests, weekly stats,
    curses, shop/inventory, and daily audits.
    """

    def __init__(self, db_path: str = "study_rpg.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes all tables, runs safe migrations, and idempotently seeds data."""
        with self._connect() as conn:
            cur = conn.cursor()

            # Core Hunter Profile & Daily Quest Progress
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hunter_profile (
                    user_id INTEGER PRIMARY KEY,
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
                    exam_mode_started_iso TEXT,
                    last_weekly_report_date TEXT
                )
            """)

            # Safe column migrations for existing databases
            existing_cols = {
                row["name"] for row in cur.execute("PRAGMA table_info(hunter_profile)").fetchall()
            }
            migrations = {
                "daily_gold_earned": "ALTER TABLE hunter_profile ADD COLUMN daily_gold_earned INTEGER DEFAULT 0",
                "active_shield_date": "ALTER TABLE hunter_profile ADD COLUMN active_shield_date TEXT",
                "last_audit_date": "ALTER TABLE hunter_profile ADD COLUMN last_audit_date TEXT",
                "exam_mode_active": "ALTER TABLE hunter_profile ADD COLUMN exam_mode_active INTEGER DEFAULT 0",
                "exam_mode_test_name": "ALTER TABLE hunter_profile ADD COLUMN exam_mode_test_name TEXT",
                "exam_mode_started_iso": "ALTER TABLE hunter_profile ADD COLUMN exam_mode_started_iso TEXT",
                "last_weekly_report_date": "ALTER TABLE hunter_profile ADD COLUMN last_weekly_report_date TEXT",
            }
            for col_name, alter_sql in migrations.items():
                if col_name not in existing_cols:
                    cur.execute(alter_sql)

            # Discord World Config
            cur.execute("""
                CREATE TABLE IF NOT EXISTS world_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )
            """)

            # Active Focus Session State
            cur.execute("""
                CREATE TABLE IF NOT EXISTS active_focus (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    start_time_iso TEXT,
                    source TEXT,
                    is_exam_session INTEGER DEFAULT 0
                )
            """)
            focus_cols = {
                row["name"] for row in cur.execute("PRAGMA table_info(active_focus)").fetchall()
            }
            if "is_exam_session" not in focus_cols:
                cur.execute("ALTER TABLE active_focus ADD COLUMN is_exam_session INTEGER DEFAULT 0")

            # Chapter Bosses & Syllabus Topics Tables
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bosses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    boss_id INTEGER,
                    topic_name TEXT NOT NULL,
                    concept_done INTEGER DEFAULT 0,
                    revision_done INTEGER DEFAULT 0,
                    FOREIGN KEY (boss_id) REFERENCES bosses(id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS topic_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT,
                    chapter TEXT,
                    topic TEXT,
                    mode TEXT,
                    logged_at TEXT
                )
            """)

            # NEET 2027 Chapter Revision Checklist Table (5 stages = 20% each)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

            # Rolling 7-Day Telemetry Ledger (Balance Tracker & Weekly Reports)
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
                    exam_mode_minutes INTEGER DEFAULT 0,
                    chapters_progressed INTEGER DEFAULT 0
                )
            """)
            act_cols = {
                row["name"] for row in cur.execute("PRAGMA table_info(daily_activity_log)").fetchall()
            }
            if "chapters_progressed" not in act_cols:
                cur.execute("ALTER TABLE daily_activity_log ADD COLUMN chapters_progressed INTEGER DEFAULT 0")

            # Next-Topic Queue Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS topic_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

            # 24-Hour Streak Recovery Quest Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recovery_quests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    triggered_date TEXT NOT NULL,
                    expires_at_iso TEXT NOT NULL,
                    req_minutes INTEGER DEFAULT 360,
                    req_mcqs INTEGER DEFAULT 420,
                    req_curses_cleansed INTEGER DEFAULT 2,
                    prog_minutes INTEGER DEFAULT 0,
                    prog_mcqs INTEGER DEFAULT 0,
                    prog_curses_cleansed INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ACTIVE'
                )
            """)

            # Mistake Vault & Curses Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS curses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    topic_or_note TEXT NOT NULL,
                    source_test TEXT DEFAULT 'Manual Entry',
                    cleared INTEGER DEFAULT 0,
                    created_at TEXT,
                    cleared_at TEXT
                )
            """)
            curse_cols = {
                row["name"] for row in cur.execute("PRAGMA table_info(curses)").fetchall()
            }
            if "cleared_at" not in curse_cols:
                cur.execute("ALTER TABLE curses ADD COLUMN cleared_at TEXT")

            # Shop Items, Inventory & Shield Usage History
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shop_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    category TEXT DEFAULT 'REWARD'
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT UNIQUE NOT NULL,
                    quantity INTEGER DEFAULT 0
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS shield_usage_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    used_at_iso TEXT NOT NULL,
                    covered_date TEXT NOT NULL
                )
            """)

            # Test Logs History
            cur.execute("""
                CREATE TABLE IF NOT EXISTS test_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_name TEXT,
                    score INTEGER,
                    max_score INTEGER,
                    percentage REAL,
                    logged_at TEXT
                )
            """)

            # Seed default user if not exists
            today_str = today_ist_str()
            cur.execute("SELECT user_id FROM hunter_profile WHERE user_id = ?", (ALLOWED_USER_ID,))
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO hunter_profile (user_id, last_reset_date)
                    VALUES (?, ?)
                    """,
                    (ALLOWED_USER_ID, today_str),
                )

            # Ensure today's row exists in daily_activity_log
            cur.execute(
                "INSERT INTO daily_activity_log (log_date) VALUES (?) ON CONFLICT(log_date) DO NOTHING",
                (today_str,),
            )

            # Seed default NEET-tailored Shop Items
            default_shop = [
                (
                    "rest_shield",
                    "Rest / Post-Test Shield",
                    "Waives today's daily targets & preserves your streak for 24h (Max 2 per rolling 7 days).",
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
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(item_key) DO NOTHING
                    """,
                    (item_key, name, desc, price, cat),
                )

            conn.commit()

        # Idempotently seed NEET 2027-only chapters & linked bosses
        self.seed_neet_2027_chapters()

    def seed_neet_2027_chapters(self) -> int:
        """
        Idempotently seeds all NEET 2027-only chapters (Physics, Chemistry, Biology —
        Class 11 & 12) and links each to a 5-topic Chapter Boss (500 HP = 100 HP per stage).
        Never duplicates existing rows.
        """
        inserted_count = 0
        now_iso = now_ist().isoformat()

        with self._connect() as conn:
            for subject, class_level, chap_name in NEET_2027_CHAPTERS:
                existing = conn.execute(
                    "SELECT id, boss_id FROM chapters WHERE LOWER(chapter_name) = LOWER(?)",
                    (chap_name,),
                ).fetchone()

                if existing:
                    # Ensure subject/class_level stay canonical for NEET 2027
                    conn.execute(
                        "UPDATE chapters SET subject = ?, class_level = ? WHERE id = ?",
                        (subject, class_level, existing["id"]),
                    )
                    if not existing["boss_id"]:
                        boss_row = conn.execute(
                            "SELECT id FROM bosses WHERE LOWER(subject) = LOWER(?) AND LOWER(chapter) = LOWER(?)",
                            (subject, chap_name),
                        ).fetchone()
                        if boss_row:
                            boss_id = boss_row["id"]
                        else:
                            b_cur = conn.execute(
                                """
                                INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                                VALUES (?, ?, 5, 0, 'ACTIVE', ?)
                                """,
                                (subject, chap_name, now_iso),
                            )
                            boss_id = b_cur.lastrowid
                        conn.execute(
                            "UPDATE chapters SET boss_id = ? WHERE id = ?",
                            (boss_id, existing["id"]),
                        )
                    continue

                # Create linked 5-stage boss (500 HP max = 100 HP per stage)
                boss_row = conn.execute(
                    "SELECT id FROM bosses WHERE LOWER(subject) = LOWER(?) AND LOWER(chapter) = LOWER(?)",
                    (subject, chap_name),
                ).fetchone()
                if boss_row:
                    boss_id = boss_row["id"]
                else:
                    b_cur = conn.execute(
                        """
                        INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                        VALUES (?, ?, 5, 0, 'ACTIVE', ?)
                        """,
                        (subject, chap_name, now_iso),
                    )
                    boss_id = b_cur.lastrowid

                conn.execute(
                    """
                    INSERT INTO chapters (
                        subject, class_level, chapter_name,
                        short_notes, topics_done, mcqs_done, mistakes_logged, revised,
                        boss_id, updated_at
                    )
                    VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?)
                    """,
                    (subject, class_level, chap_name, boss_id, now_iso),
                )
                inserted_count += 1

            conn.commit()

        return inserted_count

    def _ensure_activity_row(self, conn: sqlite3.Connection, log_date: Optional[str] = None) -> str:
        d_str = log_date or today_ist_str()
        conn.execute(
            "INSERT INTO daily_activity_log (log_date) VALUES (?) ON CONFLICT(log_date) DO NOTHING",
            (d_str,),
        )
        return d_str

    def record_activity_telemetry(
        self,
        minutes: int = 0,
        bio_mcqs: int = 0,
        phy_mcqs: int = 0,
        chem_mcqs: int = 0,
        concept_delta: int = 0,
        execution_delta: int = 0,
        tests_delta: int = 0,
        exam_minutes: int = 0,
        chapters_delta: int = 0,
    ) -> None:
        """Records daily telemetry for rolling 7-day balance & weekly reports."""
        with self._connect() as conn:
            d_str = self._ensure_activity_row(conn)
            conn.execute(
                """
                UPDATE daily_activity_log
                SET study_minutes = MAX(0, study_minutes + ?),
                    bio_mcqs = MAX(0, bio_mcqs + ?),
                    phy_mcqs = MAX(0, phy_mcqs + ?),
                    chem_mcqs = MAX(0, chem_mcqs + ?),
                    concept_count = MAX(0, concept_count + ?),
                    execution_count = MAX(0, execution_count + ?),
                    tests_logged = MAX(0, tests_logged + ?),
                    exam_mode_minutes = MAX(0, exam_mode_minutes + ?),
                    chapters_progressed = MAX(0, chapters_progressed + ?)
                WHERE log_date = ?
                """,
                (
                    minutes,
                    bio_mcqs,
                    phy_mcqs,
                    chem_mcqs,
                    concept_delta,
                    execution_delta,
                    tests_delta,
                    exam_minutes,
                    chapters_delta,
                    d_str,
                ),
            )
            conn.commit()

    def get_rolling_7day_stats(self) -> Dict[str, Any]:
        """Aggregates the last 7 days (including today in IST) from `daily_activity_log`."""
        cutoff_date = (now_ist().date() - timedelta(days=6)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(study_minutes), 0) AS total_minutes,
                    COALESCE(SUM(bio_mcqs), 0) AS bio_mcqs,
                    COALESCE(SUM(phy_mcqs), 0) AS phy_mcqs,
                    COALESCE(SUM(chem_mcqs), 0) AS chem_mcqs,
                    COALESCE(SUM(concept_count), 0) AS concept_count,
                    COALESCE(SUM(execution_count), 0) AS execution_count,
                    COALESCE(SUM(tests_logged), 0) AS tests_logged,
                    COALESCE(SUM(exam_mode_minutes), 0) AS exam_mode_minutes,
                    COALESCE(SUM(chapters_progressed), 0) AS chapters_progressed
                FROM daily_activity_log
                WHERE log_date >= ?
                """,
                (cutoff_date,),
            ).fetchone()
            return dict(row)

    def get_balance_assessment(self) -> Dict[str, Any]:
        """
        Evaluates rolling 7-day subject & habit balance:
        - Bio vs. Physics MCQ ratio (flags if Bio >= 300 and Bio > 3.5x Physics)
        - Concept/Short Notes vs. Execution (MCQs/Revision) ratio (flags if concept > 2.5x execution and >= 4)
        Returns status, multipliers/nudges, and human-readable summary lines.
        """
        s = self.get_rolling_7day_stats()
        bio = s["bio_mcqs"]
        phy = s["phy_mcqs"]
        concepts = s["concept_count"]
        exec_cnt = s["execution_count"]

        bio_heavy = (bio >= 250) and (phy == 0 or (bio / max(1, phy)) > 3.5)
        phy_heavy = (phy >= 120) and (bio == 0 or (phy / max(1, bio)) > 2.0)
        notes_heavy = (concepts >= 4) and (concepts > max(1, exec_cnt) * 2.5)

        flags: List[str] = []
        xp_multipliers: Dict[str, float] = {
            "Bio": 1.0,
            "Physics": 1.0,
            "Chem": 1.0,
            "Concept": 1.0,
            "Revision": 1.0,
        }

        if bio_heavy:
            flags.append(f"Bio-Heavy Skew ({bio} Bio vs {phy} Phy MCQs in 7d) → Bio XP nudged to 0.75x")
            xp_multipliers["Bio"] = 0.75
        elif phy_heavy:
            flags.append(f"Physics-Heavy Skew ({phy} Phy vs {bio} Bio MCQs in 7d) → Phy XP nudged to 0.80x")
            xp_multipliers["Physics"] = 0.80

        if notes_heavy:
            flags.append(f"Notes/Concept Skew ({concepts} Concept vs {exec_cnt} Execution in 7d) → Concept XP nudged to 0.75x")
            xp_multipliers["Concept"] = 0.75

        is_balanced = len(flags) == 0
        summary = (
            f"7d MCQs: Bio `{bio}` | Phy `{phy}` | Chem `{s['chem_mcqs']}` • "
            f"7d Concept vs Exec: `{concepts}` vs `{exec_cnt}`"
        )
        return {
            "balanced": is_balanced,
            "flags": flags,
            "multipliers": xp_multipliers,
            "summary": summary,
            "stats": s,
        }

    # --- Exam Mode Helpers ---

    def set_exam_mode(self, active: bool, test_name: Optional[str] = None) -> Dict[str, Any]:
        clean_name = (test_name or "").strip() or ("NEET Mock / Part Test" if active else None)
        started_iso = now_ist().isoformat() if active else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hunter_profile
                SET exam_mode_active = ?,
                    exam_mode_test_name = ?,
                    exam_mode_started_iso = ?
                WHERE user_id = ?
                """,
                (1 if active else 0, clean_name if active else None, started_iso, ALLOWED_USER_ID),
            )
            conn.commit()
        return self.get_profile()

    # --- World Config Helpers ---

    def set_config(self, key: str, value: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO world_config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def get_config(self, key: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM world_config WHERE key = ?", (key,)).fetchone()
            return int(row["value"]) if row else None

    # --- Profile & Stat Helpers ---

    def get_profile(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM hunter_profile WHERE user_id = ?", (ALLOWED_USER_ID,)
            ).fetchone()
            return dict(row)

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
        heal_hp: int = 0,
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Atomically updates XP, Gold, Core Stats, Daily Quests, and Level-Ups.
        Also forwards progress into any active 24-hour Streak Recovery Quest.
        Returns `(updated_profile, leveled_up)`.
        """
        prof = self.get_profile()
        new_xp = prof["xp"] + max(0, xp_gain)
        new_level = prof["level"]
        leveled_up = False

        while new_xp >= xp_for_next_level(new_level):
            new_xp -= xp_for_next_level(new_level)
            new_level += 1
            leveled_up = True

        new_max_hp = 100 + (new_level - 1) * 10
        new_hp = min(new_max_hp, prof["hp"] + heal_hp + (25 if leveled_up else 0))

        total_new_mcqs = max(0, d_bio) + max(0, d_phy) + max(0, d_chem)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hunter_profile
                SET level = ?,
                    xp = ?,
                    hp = ?,
                    max_hp = ?,
                    gold = gold + ?,
                    daily_gold_earned = daily_gold_earned + ?,
                    stat_int = stat_int + ?,
                    stat_end = stat_end + ?,
                    stat_dex = stat_dex + ?,
                    stat_wis = stat_wis + ?,
                    daily_minutes = daily_minutes + ?,
                    daily_bio_mcqs = daily_bio_mcqs + ?,
                    daily_phy_mcqs = daily_phy_mcqs + ?,
                    daily_chem_mcqs = daily_chem_mcqs + ?,
                    daily_topics = daily_topics + ?,
                    total_topics_cleared = total_topics_cleared + ?
                WHERE user_id = ?
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

            # Advance active 24h Streak Recovery Quest if present
            if d_minutes > 0 or total_new_mcqs > 0:
                conn.execute(
                    """
                    UPDATE recovery_quests
                    SET prog_minutes = prog_minutes + ?,
                        prog_mcqs = prog_mcqs + ?
                    WHERE status = 'ACTIVE'
                    """,
                    (max(0, d_minutes), total_new_mcqs),
                )

            conn.commit()

        return self.get_profile(), leveled_up

    # --- Focus Session Management ---

    def get_active_focus(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT start_time_iso, source, is_exam_session FROM active_focus WHERE id = 1"
            ).fetchone()
            return dict(row) if row and row["start_time_iso"] else None

    def start_focus(self, source: str = "Manual", is_exam_session: bool = False) -> Tuple[bool, str]:
        active = self.get_active_focus()
        if active:
            return False, active["start_time_iso"]

        iso_str = now_ist().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO active_focus (id, start_time_iso, source, is_exam_session)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    start_time_iso = excluded.start_time_iso,
                    source = excluded.source,
                    is_exam_session = excluded.is_exam_session
                """,
                (iso_str, source, 1 if is_exam_session else 0),
            )
            conn.commit()
        return True, iso_str

    def stop_focus(self) -> Optional[Tuple[int, str, bool]]:
        """Stops active focus session and returns `(elapsed_minutes, source, is_exam_session)`."""
        active = self.get_active_focus()
        if not active:
            return None

        start_dt = datetime.fromisoformat(active["start_time_iso"])
        elapsed_mins = max(0, int((now_ist() - start_dt).total_seconds() // 60))
        is_exam = bool(active.get("is_exam_session"))

        with self._connect() as conn:
            conn.execute("DELETE FROM active_focus WHERE id = 1")
            conn.commit()

        return elapsed_mins, active["source"], is_exam

    # --- NEET 2027 Chapter Revision Checklist & Boss Sync ---

    def get_filtered_chapters(
        self, subject_filter: str = "ALL", class_filter: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM chapters WHERE 1=1"
        params: List[Any] = []

        if subject_filter and subject_filter.upper() != "ALL":
            if subject_filter.upper() == "TRAPS":
                query += (
                    " AND short_notes = 1 AND mcqs_done = 0 AND revised = 0"
                )
            else:
                query += " AND LOWER(subject) = LOWER(?)"
                params.append(normalize_subject_name(subject_filter))

        if class_filter in (11, 12):
            query += " AND class_level = ?"
            params.append(class_filter)

        query += " ORDER BY id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            result = []
            now_dt = now_ist()
            for r in rows:
                d = dict(r)
                stages_done = sum(int(d[k]) for k in STAGE_KEYS)
                d["stages_done"] = stages_done
                d["completion_pct"] = stages_done * 20
                is_notes_only = bool(d["short_notes"]) and not bool(d["mcqs_done"]) and not bool(d["revised"])
                days_in_trap = 0
                if is_notes_only and d.get("notes_only_since"):
                    try:
                        since_dt = datetime.fromisoformat(d["notes_only_since"])
                        days_in_trap = max(0, (now_dt - since_dt).days)
                    except ValueError:
                        days_in_trap = 0
                d["is_notes_only_trap"] = is_notes_only and (days_in_trap >= 1)
                d["days_in_trap"] = days_in_trap
                result.append(d)
            return result

    def search_chapters_autocomplete(self, query_str: str) -> List[Dict[str, Any]]:
        q = f"%{query_str.strip().lower()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, subject, class_level, chapter_name
                FROM chapters
                WHERE LOWER(chapter_name) LIKE ? OR LOWER(subject) LIKE ?
                ORDER BY id ASC
                LIMIT 25
                """,
                (q, q),
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_chapter(self, name_input: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """
        Matches a chapter by exact case-insensitive name first, then substring match.
        Returns `(matched_chapter_dict, candidate_names)`.
        """
        cleaned = name_input.strip()
        with self._connect() as conn:
            exact = conn.execute(
                "SELECT * FROM chapters WHERE LOWER(chapter_name) = LOWER(?)",
                (cleaned,),
            ).fetchone()
            if exact:
                return dict(exact), []

            matches = conn.execute(
                "SELECT * FROM chapters WHERE LOWER(chapter_name) LIKE ? ORDER BY id ASC LIMIT 8",
                (f"%{cleaned.lower()}%",),
            ).fetchall()
            if len(matches) == 1:
                return dict(matches[0]), []
            if len(matches) > 1:
                return None, [f"{m['chapter_name']} ({m['subject']} Cl {m['class_level']})" for m in matches]
            return None, []

    def add_custom_chapter(self, subject: str, class_level: int, chapter_name: str) -> Tuple[bool, str]:
        subj = normalize_subject_name(subject)
        chap = chapter_name.strip()
        now_iso = now_ist().isoformat()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM chapters WHERE LOWER(chapter_name) = LOWER(?)", (chap,)
            ).fetchone()
            if existing:
                return False, f"⚠️ Chapter **{chap}** already exists in the NEET 2027 Codex."

            b_cur = conn.execute(
                """
                INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                VALUES (?, ?, 5, 0, 'ACTIVE', ?)
                """,
                (subj, chap, now_iso),
            )
            boss_id = b_cur.lastrowid

            conn.execute(
                """
                INSERT INTO chapters (
                    subject, class_level, chapter_name,
                    short_notes, topics_done, mcqs_done, mistakes_logged, revised,
                    boss_id, updated_at
                )
                VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, ?)
                """,
                (subj, class_level, chap, boss_id, now_iso),
            )
            conn.commit()
        return True, f"✅ Added custom chapter **{chap}** (`{subj}` • Class `{class_level}`) with a linked 500 HP Boss!"

    def toggle_chapter_stage(self, chapter_id: int, stage_key: str) -> Optional[Dict[str, Any]]:
        """
        Flips a single stage (0 <-> 1) on a chapter, updates `notes_only_since`,
        syncs the linked Chapter Boss HP (100 HP per stage), and returns detailed telemetry.
        """
        if stage_key not in STAGE_KEYS:
            return None

        now_dt = now_ist()
        now_iso = now_dt.isoformat()

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
            if not row:
                return None

            chap = dict(row)
            old_val = int(chap[stage_key])
            new_val = 0 if old_val else 1
            chap[stage_key] = new_val

            stages_done = sum(int(chap[k]) for k in STAGE_KEYS)
            completion_pct = stages_done * 20

            # Track Notes-Only Trap timestamp
            is_notes_only = bool(chap["short_notes"]) and not bool(chap["mcqs_done"]) and not bool(chap["revised"])
            notes_only_since = chap.get("notes_only_since")
            if is_notes_only and not notes_only_since:
                notes_only_since = now_iso
            elif not is_notes_only:
                notes_only_since = None

            conn.execute(
                f"""
                UPDATE chapters
                SET {stage_key} = ?,
                    notes_only_since = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_val, notes_only_since, now_iso, chapter_id),
            )

            # Sync linked Chapter Boss (5 topics = 500 HP max; completed_topics = stages_done)
            boss_id = chap.get("boss_id")
            just_defeated = False
            boss_hp_cur = max(0, 5 - stages_done) * BOSS_HP_PER_TOPIC
            boss_hp_max = 5 * BOSS_HP_PER_TOPIC

            if boss_id:
                boss_row = conn.execute("SELECT * FROM bosses WHERE id = ?", (boss_id,)).fetchone()
                if boss_row:
                    was_active = boss_row["status"] == "ACTIVE"
                    new_total = max(5, int(boss_row["total_topics"]))
                    new_comp = min(new_total, stages_done)
                    new_status = "DEFEATED" if new_comp >= new_total else "ACTIVE"
                    just_defeated = was_active and (new_status == "DEFEATED")
                    boss_hp_cur = max(0, new_total - new_comp) * BOSS_HP_PER_TOPIC
                    boss_hp_max = new_total * BOSS_HP_PER_TOPIC
                    conn.execute(
                        "UPDATE bosses SET completed_topics = ?, status = ? WHERE id = ?",
                        (new_comp, new_status, boss_id),
                    )

            conn.commit()

            return {
                "chapter_name": chap["chapter_name"],
                "subject": chap["subject"],
                "class_level": chap["class_level"],
                "stage_key": stage_key,
                "new_val": new_val,
                "stages_done": stages_done,
                "completion_pct": completion_pct,
                "is_notes_only_now": is_notes_only,
                "boss_hp_cur": boss_hp_cur,
                "boss_hp_max": boss_hp_max,
                "just_defeated": just_defeated,
            }

    # --- Chapter Bosses & Topics ---

    def add_boss(self, subject: str, chapter: str, total_topics: int) -> Dict[str, Any]:
        subj = normalize_subject_name(subject)
        chap = chapter.strip()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM bosses WHERE LOWER(subject) = LOWER(?) AND LOWER(chapter) = LOWER(?)",
                (subj, chap),
            ).fetchone()
            if existing:
                new_total = max(total_topics, existing["completed_topics"])
                status = "DEFEATED" if existing["completed_topics"] >= new_total else "ACTIVE"
                conn.execute(
                    "UPDATE bosses SET total_topics = ?, status = ? WHERE id = ?",
                    (new_total, status, existing["id"]),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM bosses WHERE id = ?", (existing["id"],)).fetchone()
                return dict(row)

            cur = conn.execute(
                """
                INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                VALUES (?, ?, ?, 0, 'ACTIVE', ?)
                """,
                (subj, chap, total_topics, now_ist().isoformat()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM bosses WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row)

    def get_all_bosses(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bosses
                ORDER BY CASE WHEN status = 'ACTIVE' THEN 0 ELSE 1 END, id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_syllabus_totals(self) -> Tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(completed_topics), 0) AS comp, COALESCE(SUM(total_topics), 0) AS tot FROM bosses"
            ).fetchone()
            boss_comp = int(row["comp"])
            boss_tot = int(row["tot"])

        prof = self.get_profile()
        cleared = max(prof["total_topics_cleared"], boss_comp)
        total = max(TOTAL_SYLLABUS_TOPICS, boss_tot, cleared)
        return cleared, total

    def record_topic_and_damage_boss(
        self, subject: str, chapter: str, topic_name: str, mode: str
    ) -> Optional[Dict[str, Any]]:
        subj = normalize_subject_name(subject)
        chap = chapter.strip()
        top = topic_name.strip()
        is_concept = mode.lower() == "concept"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_logs (subject, chapter, topic, mode, logged_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (subj, chap, top, mode, now_ist().isoformat()),
            )

            boss = conn.execute(
                "SELECT * FROM bosses WHERE LOWER(chapter) = LOWER(?)",
                (chap,),
            ).fetchone()

            if not boss:
                cur = conn.execute(
                    """
                    INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                    VALUES (?, ?, 5, 0, 'ACTIVE', ?)
                    """,
                    (subj, chap, now_ist().isoformat()),
                )
                boss = conn.execute("SELECT * FROM bosses WHERE id = ?", (cur.lastrowid,)).fetchone()

            boss_id = boss["id"]
            topic_row = conn.execute(
                "SELECT * FROM topics WHERE boss_id = ? AND LOWER(topic_name) = LOWER(?)",
                (boss_id, top),
            ).fetchone()

            is_new_unique_topic = False
            if not topic_row:
                is_new_unique_topic = True
                conn.execute(
                    """
                    INSERT INTO topics (boss_id, topic_name, concept_done, revision_done)
                    VALUES (?, ?, ?, ?)
                    """,
                    (boss_id, top, 1 if is_concept else 0, 0 if is_concept else 1),
                )
            else:
                conn.execute(
                    """
                    UPDATE topics
                    SET concept_done = CASE WHEN ? THEN 1 ELSE concept_done END,
                        revision_done = CASE WHEN ? THEN 1 ELSE revision_done END
                    WHERE id = ?
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
                "UPDATE bosses SET completed_topics = ?, status = ? WHERE id = ?",
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

    # --- Next-Topic Queue Helpers ---

    def add_to_queue(
        self, subject: str, chapter_name: str, topic_name: str, priority: int = 2, chapter_id: Optional[int] = None
    ) -> int:
        subj = normalize_subject_name(subject)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO topic_queue (subject, chapter_id, chapter_name, topic_name, priority, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (subj, chapter_id, chapter_name.strip(), topic_name.strip(), priority, now_ist().isoformat()),
            )
            conn.commit()
            return int(cur.lastrowid)

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
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM topic_queue WHERE status = 'PENDING'"
            ).fetchone()
            return int(row["cnt"])

    def complete_queued_topic(self, queue_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_queue WHERE id = ? AND status = 'PENDING'", (queue_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE topic_queue SET status = 'COMPLETED', completed_at = ? WHERE id = ?",
                (now_ist().isoformat(), queue_id),
            )
            conn.commit()
            return dict(row)

    # --- Mistake Vault & Curses ---

    def add_curse(self, subject: str, note: str, source_test: str = "Manual Entry") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (normalize_subject_name(subject), note.strip(), source_test.strip(), now_ist().isoformat()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def log_test_and_curses(
        self, test_name: str, score: int, max_score: int, wrong_topics: str
    ) -> List[Tuple[int, str]]:
        pct = round((score / max_score) * 100, 2) if max_score > 0 else 0.0
        parsed_curses = [
            t.strip() for t in wrong_topics.split(",") if t.strip() and t.strip().lower() != "none"
        ]
        inserted: List[Tuple[int, str]] = []

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO test_logs (test_name, score, max_score, percentage, logged_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (test_name, score, max_score, pct, now_ist().isoformat()),
            )
            for item in parsed_curses:
                cur = conn.execute(
                    """
                    INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    ("Mock Error", item, test_name, now_ist().isoformat()),
                )
                inserted.append((int(cur.lastrowid), item))
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
                LIMIT ?
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
                "SELECT * FROM curses WHERE id = ? AND cleared = 0", (curse_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE curses SET cleared = 1, cleared_at = ? WHERE id = ?",
                (now_ist().isoformat(), curse_id),
            )
            # Advance active recovery quest curse counter
            conn.execute(
                """
                UPDATE recovery_quests
                SET prog_curses_cleansed = prog_curses_cleansed + 1
                WHERE status = 'ACTIVE'
                """
            )
            conn.commit()
            return dict(row)

    # --- 24-Hour Streak Recovery Quest Helpers ---

    def create_recovery_quest(self, triggered_date: str) -> Dict[str, Any]:
        now_dt = now_ist()
        expires_dt = now_dt + timedelta(hours=24)
        with self._connect() as conn:
            # Expire any prior lingering active quest
            conn.execute(
                "UPDATE recovery_quests SET status = 'EXPIRED' WHERE status = 'ACTIVE'"
            )
            cur = conn.execute(
                """
                INSERT INTO recovery_quests (
                    triggered_date, expires_at_iso,
                    req_minutes, req_mcqs, req_curses_cleansed,
                    prog_minutes, prog_mcqs, prog_curses_cleansed,
                    status
                )
                VALUES (?, ?, 360, 420, 2, 0, 0, 0, 'ACTIVE')
                """,
                (triggered_date, expires_dt.isoformat()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM recovery_quests WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row)

    def get_active_recovery_quest(self) -> Optional[Dict[str, Any]]:
        now_dt = now_ist()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recovery_quests WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            q = dict(row)
            exp_dt = datetime.fromisoformat(q["expires_at_iso"])
            if now_dt > exp_dt:
                conn.execute("UPDATE recovery_quests SET status = 'EXPIRED' WHERE id = ?", (q["id"],))
                conn.commit()
                return None
            q["remaining_hours"] = max(0.0, (exp_dt - now_dt).total_seconds() / 3600.0)
            q["is_complete"] = (
                q["prog_minutes"] >= q["req_minutes"]
                and q["prog_mcqs"] >= q["req_mcqs"]
                and q["prog_curses_cleansed"] >= q["req_curses_cleansed"]
            )
            return q

    def claim_recovery_quest(self) -> Tuple[bool, str]:
        q = self.get_active_recovery_quest()
        if not q:
            return False, "❌ No active 24-hour Streak Recovery Quest found (or it has expired)."

        if not q["is_complete"]:
            return (
                False,
                "⏳ **Recovery Requirements Not Met Yet:**\n"
                f"• Focus: `{q['prog_minutes']} / {q['req_minutes']} mins`\n"
                f"• MCQs: `{q['prog_mcqs']} / {q['req_mcqs']}`\n"
                f"• Curses Cleansed: `{q['prog_curses_cleansed']} / {q['req_curses_cleansed']}`\n"
                f"• Time Left: `{q['remaining_hours']:.1f} hours`",
            )

        prof = self.get_profile()
        restored_hp = min(prof["max_hp"], prof["hp"] + 35)
        restored_streak = max(prof["streak"], 1)

        with self._connect() as conn:
            conn.execute("UPDATE recovery_quests SET status = 'REDEEMED' WHERE id = ?", (q["id"],))
            conn.execute(
                """
                UPDATE hunter_profile
                SET hp = ?,
                    streak = ?,
                    is_fatigued = 0,
                    xp = xp + 200,
                    gold = gold + 150
                WHERE user_id = ?
                """,
                (restored_hp, restored_streak, ALLOWED_USER_ID),
            )
            conn.commit()

        return (
            True,
            "⛓️‍💥 **STREAK RECOVERY QUEST REDEEMED!**\n"
            f"• **HP Restored:** `+35 HP` (`{restored_hp}/{prof['max_hp']} HP`)\n"
            f"• **Fatigue Cleared:** `⚠️ Fatigued` removed\n"
            f"• **Recovery Bounty:** `+200 XP` • `+150 Gold` • Streak set to `🔥 {restored_streak} Day`\n"
            "*Note: The original missed-day log remains permanently recorded in `#midnight-tribunal`.*",
        )

    # --- Weekly Report Data Aggregation ---

    def get_weekly_report_data(self) -> Dict[str, Any]:
        cutoff_iso = (now_ist() - timedelta(days=7)).isoformat()
        cutoff_date = (now_ist().date() - timedelta(days=6)).isoformat()
        s = self.get_rolling_7day_stats()

        with self._connect() as conn:
            tests = conn.execute(
                """
                SELECT test_name, score, max_score, percentage
                FROM test_logs
                WHERE logged_at >= ?
                ORDER BY id DESC
                """,
                (cutoff_iso,),
            ).fetchall()

            uncleared_curses = conn.execute(
                "SELECT COUNT(*) AS cnt FROM curses WHERE cleared = 0"
            ).fetchone()["cnt"]

            chapters_rows = conn.execute("SELECT * FROM chapters").fetchall()
            full_chapters = 0
            in_prog_chapters = 0
            trap_chapters = 0
            for c in chapters_rows:
                done = sum(int(c[k]) for k in STAGE_KEYS)
                if done == 5:
                    full_chapters += 1
                elif done > 0:
                    in_prog_chapters += 1
                if c["short_notes"] and not c["mcqs_done"] and not c["revised"]:
                    trap_chapters += 1

        days_left = max(0, (TARGET_MASTERY_DATE - now_ist().date()).days)
        cleared_topics, total_topics = self.get_syllabus_totals()
        rem_topics = max(0, total_topics - cleared_topics)
        req_pace = (rem_topics / days_left) if days_left > 0 else float(rem_topics)

        return {
            "since_date": cutoff_date,
            "days_left": days_left,
            "req_pace": req_pace,
            "rem_topics": rem_topics,
            "study_hours": round(s["total_minutes"] / 60.0, 1),
            "study_minutes": s["total_minutes"],
            "bio_mcqs": s["bio_mcqs"],
            "phy_mcqs": s["phy_mcqs"],
            "chem_mcqs": s["chem_mcqs"],
            "tests": [dict(t) for t in tests],
            "uncleared_curses": int(uncleared_curses),
            "total_chapters": len(chapters_rows),
            "full_chapters": full_chapters,
            "in_prog_chapters": in_prog_chapters,
            "trap_chapters": trap_chapters,
            "balance": self.get_balance_assessment(),
        }

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
                WHERE LOWER(item_key) = ? OR LOWER(name) LIKE ?
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
                "UPDATE hunter_profile SET gold = gold - ? WHERE user_id = ?",
                (item["price"], ALLOWED_USER_ID),
            )
            conn.execute(
                """
                INSERT INTO inventory (item_key, quantity) VALUES (?, 1)
                ON CONFLICT(item_key) DO UPDATE SET quantity = quantity + 1
                """,
                (item["item_key"],),
            )
            conn.commit()

        return True, f"🛒 Purchased **{item['name']}** for `{item['price']} G`! Stored in your Inventory."

    def get_shields_used_last_7_days(self) -> int:
        cutoff = (now_ist() - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM shield_usage_log WHERE used_at_iso >= ?",
                (cutoff,),
            ).fetchone()
            return int(row["cnt"])

    def activate_rest_shield(self) -> Tuple[bool, str]:
        prof = self.get_profile()
        today_str = today_ist_str()

        if prof.get("active_shield_date") == today_str:
            return False, "🛡️ A **Rest / Post-Test Shield** is already active for today!"

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
                "UPDATE hunter_profile SET active_shield_date = ? WHERE user_id = ?",
                (today_str, ALLOWED_USER_ID),
            )
            conn.execute(
                "INSERT INTO shield_usage_log (used_at_iso, covered_date) VALUES (?, ?)",
                (now_ist().isoformat(), today_str),
            )
            conn.commit()

        return (
            True,
            f"🛡️ **REST / POST-TEST SHIELD ACTIVATED!**\n"
            f"Today's daily quest targets (`{today_str}`) are waived, and your streak is protected for 24h.\n"
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
                "SELECT quantity FROM inventory WHERE item_key = ?", (item["item_key"],)
            ).fetchone()
            if not inv or inv["quantity"] <= 0:
                return False, f"❌ You don't have any **{item['name']}** in your inventory."

            conn.execute(
                "UPDATE inventory SET quantity = quantity - 1 WHERE item_key = ?",
                (item["item_key"],),
            )
            conn.commit()

        return True, f"🎟️ Redeemed **{item['name']}**! Enjoy your earned reward—then return to the grind."

    # --- Ruthless 23:55 IST Midnight Audit & Daily Reset ---

    def run_midnight_audit(self) -> Optional[Dict[str, Any]]:
        prof = self.get_profile()
        today_str = today_ist_str()

        if prof.get("last_audit_date") == today_str:
            return None

        mins_ok = prof["daily_minutes"] >= DAILY_TARGET_MINUTES
        bio_ok = prof["daily_bio_mcqs"] >= DAILY_TARGET_BIO
        phy_ok = prof["daily_phy_mcqs"] >= DAILY_TARGET_PHY
        top_ok = prof["daily_topics"] >= DAILY_TARGET_TOPICS

        missed_items: List[str] = []
        if not mins_ok:
            missed_items.append(
                f"• **Study Time:** `{prof['daily_minutes']} / {DAILY_TARGET_MINUTES} mins` (Short by `{DAILY_TARGET_MINUTES - prof['daily_minutes']} mins`)"
            )
        if not bio_ok:
            missed_items.append(
                f"• **Biology MCQs:** `{prof['daily_bio_mcqs']} / {DAILY_TARGET_BIO}` (Short by `{DAILY_TARGET_BIO - prof['daily_bio_mcqs']} MCQs`)"
            )
        if not phy_ok:
            missed_items.append(
                f"• **Physics MCQs:** `{prof['daily_phy_mcqs']} / {DAILY_TARGET_PHY}` (Short by `{DAILY_TARGET_PHY - prof['daily_phy_mcqs']} MCQs`)"
            )
        if not top_ok:
            missed_items.append(
                f"• **Syllabus Topic:** `{prof['daily_topics']} / {DAILY_TARGET_TOPICS}` (Zero concepts/revisions cleared)"
            )

        shield_active = prof.get("active_shield_date") == today_str
        all_passed = len(missed_items) == 0

        new_level = prof["level"]
        new_gold = prof["gold"]
        new_max_hp = prof["max_hp"]
        level_dropped = False
        gold_halved = False
        bonus_gold = 0
        recovery_quest: Optional[Dict[str, Any]] = None

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

            recovery_quest = self.create_recovery_quest(today_str)

        next_day_str = (now_ist().date() + timedelta(days=1)).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hunter_profile
                SET level = ?,
                    hp = ?,
                    max_hp = ?,
                    gold = ?,
                    streak = ?,
                    is_fatigued = ?,
                    daily_minutes = 0,
                    daily_bio_mcqs = 0,
                    daily_phy_mcqs = 0,
                    daily_chem_mcqs = 0,
                    daily_topics = 0,
                    daily_gold_earned = 0,
                    active_shield_date = NULL,
                    last_reset_date = ?,
                    last_audit_date = ?
                WHERE user_id = ?
                """,
                (
                    new_level,
                    new_hp,
                    new_max_hp,
                    new_gold,
                    new_streak,
                    fatigued,
                    today_str,
                    today_str,
                    ALLOWED_USER_ID,
                ),
            )
            self._ensure_activity_row(conn, next_day_str)
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
            "recovery_quest": recovery_quest,
        }


# ==============================================================================
# 3. EMBED BUILDERS (LIVE DASHBOARDS, CHAPTER CODEX & WEEKLY REPORT)
# ==============================================================================

def build_quest_board_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the live-updating Daily Quest Board embed."""
    prof = db.get_profile()
    active_focus = db.get_active_focus()
    now = now_ist()

    audit_dt = datetime.combine(now.date(), time(23, 55), tzinfo=IST)
    if now >= audit_dt:
        audit_dt += timedelta(days=1)
    rem_sec = int((audit_dt - now).total_seconds())
    rem_hrs, rem_mins = divmod(rem_sec // 60, 60)

    def status_icon(curr: int, target: int) -> str:
        return "✅" if curr >= target else "⏳"

    m_curr = prof["daily_minutes"]
    b_curr = prof["daily_bio_mcqs"]
    p_curr = prof["daily_phy_mcqs"]
    t_curr = prof["daily_topics"]

    banners: List[str] = []
    if prof.get("exam_mode_active"):
        t_name = prof.get("exam_mode_test_name") or "Active Test"
        banners.append(f"🎯 **EXAM MODE ACTIVE ({t_name})** — `+50% WIS XP` on tests & mistakes; test blocks protected.")

    shield_active = prof.get("active_shield_date") == today_ist_str()
    if shield_active:
        banners.append("🛡️ **REST / POST-TEST SHIELD ACTIVE** — Today's targets waived & streak protected!")

    recovery_q = db.get_active_recovery_quest()
    if recovery_q:
        banners.append(
            f"⛓️ **24H RECOVERY QUEST ({recovery_q['remaining_hours']:.1f}h left):** "
            f"Focus `{recovery_q['prog_minutes']}/{recovery_q['req_minutes']}m` • "
            f"MCQs `{recovery_q['prog_mcqs']}/{recovery_q['req_mcqs']}` • "
            f"Curses `{recovery_q['prog_curses_cleansed']}/{recovery_q['req_curses_cleansed']}` (`/redeem`)"
        )

    balance = db.get_balance_assessment()
    if not balance["balanced"]:
        banners.append(f"⚖️ **BALANCE NUDGE:** {' | '.join(balance['flags'])}")

    focus_banner = "🟢 **IDLE** — Click `Toggle Focus` or join `#focus-dungeon`"
    if active_focus:
        start_dt = datetime.fromisoformat(active_focus["start_time_iso"])
        elapsed = int((now - start_dt).total_seconds() // 60)
        tag = " [EXAM]" if active_focus.get("is_exam_session") else ""
        focus_banner = (
            f"🔥 **ACTIVE SESSION ({active_focus['source'].upper()}{tag})** — "
            f"Running for `{elapsed} mins` (Started {start_dt.strftime('%I:%M %p IST')})"
        )

    banner_block = ("\n".join(banners) + "\n") if banners else ""

    embed = discord.Embed(
        title="⚔️ DAILY HUNTER QUESTS — NEET 2027",
        description=(
            f"**Date (IST):** `{now.strftime('%d %b %Y')}` • **23:55 Audit In:** `{rem_hrs}h {rem_mins}m`\n"
            f"{banner_block}"
            f"**Focus Status:** {focus_banner}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0xE67E22 if prof.get("exam_mode_active") else (0x3498DB if shield_active else (0xE74C3C if prof["is_fatigued"] else 0x2B2D31)),
        timestamp=now,
    )

    embed.add_field(
        name=f"{status_icon(m_curr, DAILY_TARGET_MINUTES)} Deep Study Endurance (END)",
        value=f"`{m_curr} / {DAILY_TARGET_MINUTES} mins`\n{build_progress_bar(m_curr, DAILY_TARGET_MINUTES)}",
        inline=False,
    )
    embed.add_field(
        name=f"{status_icon(b_curr, DAILY_TARGET_BIO)} Biology MCQ Drill (DEX)",
        value=f"`{b_curr} / {DAILY_TARGET_BIO} MCQs`\n{build_progress_bar(b_curr, DAILY_TARGET_BIO)}",
        inline=True,
    )
    embed.add_field(
        name=f"{status_icon(p_curr, DAILY_TARGET_PHY)} Physics MCQ Drill (DEX)",
        value=f"`{p_curr} / {DAILY_TARGET_PHY} MCQs`\n{build_progress_bar(p_curr, DAILY_TARGET_PHY)}",
        inline=True,
    )
    embed.add_field(
        name=f"{status_icon(t_curr, DAILY_TARGET_TOPICS)} Syllabus Mastery (INT / WIS)",
        value=f"`{t_curr} / {DAILY_TARGET_TOPICS} Topic Cleared`\n{build_progress_bar(t_curr, DAILY_TARGET_TOPICS)}",
        inline=False,
    )

    if prof["daily_chem_mcqs"] > 0:
        embed.add_field(
            name="🧪 Bonus Chemistry MCQs Today",
            value=f"`{prof['daily_chem_mcqs']} MCQs logged`",
            inline=False,
        )

    embed.set_footer(text="Audit executes at 23:55 IST: -15 HP per missed quest without an active Rest Shield.")
    return embed


def build_status_window_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the Hunter Status Window (Character Sheet) embed."""
    prof = db.get_profile()
    level = prof["level"]
    rank = get_rank_for_level(level)
    req_xp = xp_for_next_level(level)
    shield_active = prof.get("active_shield_date") == today_ist_str()

    if shield_active:
        condition = "🛡️ SHIELDED (24h Rest Protection)"
        color = 0x3498DB
    elif prof["is_fatigued"]:
        condition = "⚠️ FATIGUED (Debuff Active)"
        color = 0xE67E22
    else:
        condition = "⚡ OPTIMAL"
        color = 0x00D2FF

    exam_line = (
        f"**Exam Mode:** `🎯 ON — {prof.get('exam_mode_test_name') or 'Test Prep'}`\n"
        if prof.get("exam_mode_active")
        else "**Exam Mode:** `Off`\n"
    )

    balance = db.get_balance_assessment()
    balance_val = (
        f"✅ **Balanced** — {balance['summary']}"
        if balance["balanced"]
        else f"⚠️ **Imbalanced** — {balance['summary']}\n• " + "\n• ".join(balance["flags"])
    )

    traps = db.get_filtered_chapters(subject_filter="TRAPS")
    trap_line = f"`{len(traps)} chapter(s)`" if traps else "`0 chapters (Clean)`"

    inv = db.get_inventory()
    inv_str = ", ".join(f"{i['name']} `x{i['quantity']}`" for i in inv) if inv else "Empty"

    embed = discord.Embed(
        title="📊 HUNTER STATUS WINDOW",
        description=(
            f"**Class:** `NEET 2027 Sovereign Aspirant`\n"
            f"**Rank:** `{rank}` • **Level:** `{level}`\n"
            f"**Condition:** `{condition}`\n"
            f"{exam_line}"
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
    embed.add_field(name="⚖️ 7-Day Subject & Habit Balance", value=balance_val, inline=False)
    embed.add_field(name="🚨 Notes-Only Chapters", value=trap_line, inline=True)
    embed.add_field(name="🎒 Inventory Pouch", value=inv_str, inline=True)

    embed.set_footer(text="Target Mastery Date: December 15, 2026 (IST)")
    return embed


def build_boss_raids_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the Syllabus Boss Raid & Dec 15, 2026 Countdown embed."""
    today = now_ist().date()
    days_left = max(0, (TARGET_MASTERY_DATE - today).days)

    cleared, total = db.get_syllabus_totals()
    remaining_topics = max(0, total - cleared)

    if days_left > 0:
        required_pace = remaining_topics / days_left
        pace_str = f"`{required_pace:.2f} topics/day` ({remaining_topics} topics left over {days_left} days)"
    elif remaining_topics == 0:
        pace_str = "`0.00 topics/day` — SYLLABUS CONQUERED!"
    else:
        pace_str = f"`DEADLINE REACHED` — {remaining_topics} topics remaining!"

    all_bosses = db.get_all_bosses()
    active_bosses = [b for b in all_bosses if b["status"] == "ACTIVE"][:6]
    defeated_count = sum(1 for b in all_bosses if b["status"] == "DEFEATED")

    if active_bosses:
        boss_lines = []
        for b in active_bosses:
            rem_t = max(0, b["total_topics"] - b["completed_topics"])
            cur_hp = rem_t * BOSS_HP_PER_TOPIC
            max_hp = b["total_topics"] * BOSS_HP_PER_TOPIC
            bar = build_progress_bar(cur_hp, max_hp, length=10)
            boss_lines.append(
                f"👹 **[ID {b['id']}] {b['subject']} — {b['chapter']}**\n"
                f"   HP: `{cur_hp}/{max_hp}` {bar} • `{rem_t}` topics left"
            )
        chapter_lines = "\n".join(boss_lines)
    else:
        chapter_lines = "*No active Chapter Bosses. Spawn one with `/boss add` or `/topic done`!*"

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
        name=f"🏰 Overall Syllabus Conquest ({cleared} / {total} Topics)",
        value=build_progress_bar(cleared, total, length=18),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Active Chapter Bosses",
        value=chapter_lines,
        inline=False,
    )

    embed.set_footer(text="Use /chapters or /chapter_toggle to advance 5-stage chapter mastery & Boss HP.")
    return embed


def build_chapter_codex_embed(
    db: DatabaseManager,
    subject_filter: str = "ALL",
    class_filter: Optional[int] = None,
    page: int = 0,
    per_page: int = 6,
) -> Tuple[discord.Embed, int, int]:
    """
    Builds a paginated NEET 2027 Chapter Revision embed.
    Returns `(embed, safe_page_index, total_pages)`.
    """
    chapters = db.get_filtered_chapters(subject_filter=subject_filter, class_filter=class_filter)
    total_items = len(chapters)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    safe_page = max(0, min(page, total_pages - 1))

    # Overall mastery across the filtered set
    max_possible_stages = total_items * 5
    done_stages = sum(c["stages_done"] for c in chapters)
    overall_pct = int(round((done_stages / max(1, max_possible_stages)) * 100)) if total_items else 0

    filter_label = subject_filter if subject_filter == "ALL" else (
        "Notes-Only Trap Chapters" if subject_filter == "TRAPS" else subject_filter
    )
    if class_filter in (11, 12) and subject_filter != "TRAPS":
        filter_label = f"{filter_label} • Class {class_filter}"

    start_idx = safe_page * per_page
    end_idx = start_idx + per_page
    slice_items = chapters[start_idx:end_idx]

    if not slice_items:
        body = "*No chapters match this filter.*"
    else:
        cards = []
        for idx, c in enumerate(slice_items, start=start_idx + 1):
            n_ico = "✅" if c["short_notes"] else "⬜"
            t_ico = "✅" if c["topics_done"] else "⬜"
            q_ico = "✅" if c["mcqs_done"] else "⬜"
            m_ico = "✅" if c["mistakes_logged"] else "⬜"
            r_ico = "✅" if c["revised"] else "⬜"
            trap_tag = " 🚨 *Notes-Only Trap*" if c["is_notes_only_trap"] else ""
            cards.append(
                f"**{idx}. {c['chapter_name']}** (`{c['subject'][:3]} • Cl {c['class_level']}`) — `{c['completion_pct']}%` {build_stage_bar(c['stages_done'])}{trap_tag}\n"
                f"   Notes {n_ico} | Topics {t_ico} | MCQs {q_ico} | Mistakes {m_ico} | Revised {r_ico}"
            )
        body = "\n".join(cards)

    embed = discord.Embed(
        title="📚 NEET 2027 CHAPTER CODEX (5-STAGE REVISION)",
        description=(
            f"**Filter:** `{filter_label}` • **Chapters:** `{total_items}` • **Page:** `{safe_page + 1}/{total_pages}`\n"
            f"**Filter Mastery:** {build_progress_bar(done_stages, max(1, max_possible_stages), length=14)} (`{overall_pct}%`)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{body}"
        ),
        color=0x1ABC9C,
        timestamp=now_ist(),
    )
    embed.set_footer(text="Toggle stages via /chapter_toggle chapter:<name> stage:<short_notes|topics_done|mcqs_done|mistakes_logged|revised>")
    return embed, safe_page, total_pages


def build_weekly_report_embed(db: DatabaseManager) -> discord.Embed:
    """Builds the 7-day rolling accountability & NEET 2027 pace report embed."""
    data = db.get_weekly_report_data()
    bal = data["balance"]

    if data["tests"]:
        tests_txt = "\n".join(
            f"• **{t['test_name']}**: `{t['score']}/{t['max_score']}` (`{t['percentage']}%`)"
            for t in data["tests"][:5]
        )
    else:
        tests_txt = "*No mock/part tests logged in the past 7 days.*"

    bal_txt = (
        f"✅ **Balanced** — {bal['summary']}"
        if bal["balanced"]
        else f"⚠️ **Needs Rebalance** — {bal['summary']}\n• " + "\n• ".join(bal["flags"])
    )

    trap_note = (
        f" (`{data['trap_chapters']}` sitting on Notes without MCQs/Revision)"
        if data["trap_chapters"] > 0
        else ""
    )

    embed = discord.Embed(
        title="📈 WEEKLY NEET 2027 ACCOUNTABILITY REPORT (7-DAY IST)",
        description=(
            f"**Window:** `{data['since_date']}` → `{today_ist_str()}`\n"
            f"**Dec 15, 2026 Countdown:** `⏳ {data['days_left']} Days Left` • **Required Pace:** `{data['req_pace']:.2f} topics/day` (`{data['rem_topics']}` left)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x3498DB,
        timestamp=now_ist(),
    )

    embed.add_field(
        name="⏱️ Total Focus Time",
        value=f"`{data['study_hours']} hrs` (`{data['study_minutes']} mins`)",
        inline=True,
    )
    embed.add_field(
        name="🎯 7-Day MCQ Volume",
        value=f"🧬 Bio: `{data['bio_mcqs']}`\n⚛️ Phy: `{data['phy_mcqs']}`\n🧪 Chem: `{data['chem_mcqs']}`",
        inline=True,
    )
    embed.add_field(
        name="📚 Chapter Mastery Status",
        value=(
            f"🏆 100% Locked: `{data['full_chapters']}`\n"
            f"🔄 In Progress: `{data['in_prog_chapters']}`\n"
            f"🚨 Notes-Only: `{data['trap_chapters']}`"
        ),
        inline=True,
    )
    embed.add_field(name="📝 Tests Logged (7d)", value=tests_txt, inline=False)
    embed.add_field(name="⚖️ Subject & Habit Balance", value=bal_txt, inline=False)
    embed.add_field(
        name="🩸 Remaining Cursed Vault Backlog",
        value=f"`{data['uncleared_curses']}` uncleared mistakes{trap_note}",
        inline=False,
    )

    embed.set_footer(text="Auto-posted every Sunday at 21:00 IST • Pull anytime via /weekly_report")
    return embed


def build_shop_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the Hunter Armory & Reward Shop embed."""
    prof = db.get_profile()
    items = db.get_shop_items()
    inv = db.get_inventory()
    shields_7d = db.get_shields_used_last_7_days()

    embed = discord.Embed(
        title="🏪 HUNTER ARMORY & REWARD SHOP",
        description=(
            f"**Available Treasury:** `🪙 {prof['gold']:,} Gold`\n"
            f"**Rolling 7-Day Shields Used:** `🛡️ {shields_7d} / 2`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0xF1C40F,
        timestamp=now_ist(),
    )

    for it in items:
        embed.add_field(
            name=f"{it['name']} — `{it['price']} Gold` (`{it['item_key']}`)",
            value=it["description"],
            inline=False,
        )

    inv_text = (
        "\n".join(f"• **{i['name']}** (`{i['item_key']}`): `x{i['quantity']}`" for i in inv)
        if inv
        else "*Your inventory is currently empty.*"
    )
    embed.add_field(name="🎒 Your Current Inventory", value=inv_text, inline=False)
    embed.set_footer(text="Purchase with /buy item:<key> • Activate with /use item:<key> or /shield")
    return embed


# ==============================================================================
# 4. PERSISTENT DISCORD UI VIEWS (QUEST BOARD & CHAPTER CODEX)
# ==============================================================================

class QuestBoardView(discord.ui.View):
    """
    Persistent interactive button panel attached to #quest-board.
    Survives bot restarts because timeout=None and every button has a static custom_id.
    """

    def __init__(self, bot: "StudyRPGBot"):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message(
                "⛔ **Access Denied:** This terminal is biometrically locked to the Sovereign Hunter.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="+50 Bio MCQs",
        style=discord.ButtonStyle.success,
        emoji="🧬",
        custom_id="rpg_quest_bio_50",
    )
    async def quick_bio_50(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_mcq_log(
            subject="Bio", count=50, source="Quick Button", actor=interaction.user
        )
        await interaction.followup.send("✅ Logged **+50 Biology MCQs**!", ephemeral=True)

    @discord.ui.button(
        label="+30 Physics MCQs",
        style=discord.ButtonStyle.primary,
        emoji="⚛️",
        custom_id="rpg_quest_phy_30",
    )
    async def quick_phy_30(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_mcq_log(
            subject="Physics", count=30, source="Quick Button", actor=interaction.user
        )
        await interaction.followup.send("✅ Logged **+30 Physics MCQs**!", ephemeral=True)

    @discord.ui.button(
        label="+1 Topic Cleared",
        style=discord.ButtonStyle.secondary,
        emoji="📘",
        custom_id="rpg_quest_topic_1",
    )
    async def quick_topic_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_topic_log(
            subject="Biology",
            chapter="Quick Syllabus Block",
            topic=f"Quick Clear ({now_ist().strftime('%H:%M IST')})",
            mode="Revision",
            actor=interaction.user,
        )
        await interaction.followup.send("✅ Logged **+1 Topic Cleared**!", ephemeral=True)

    @discord.ui.button(
        label="Toggle Focus",
        style=discord.ButtonStyle.danger,
        emoji="⏱️",
        custom_id="rpg_quest_toggle_focus",
    )
    async def toggle_focus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        active = self.bot.db.get_active_focus()
        if not active:
            prof = self.bot.db.get_profile()
            _, iso_str = self.bot.db.start_focus(
                source="Quest Board Button", is_exam_session=bool(prof.get("exam_mode_active"))
            )
            await self.bot.refresh_all_dashboards()
            await interaction.followup.send(
                f"🔥 **Focus Session Started** at `{datetime.fromisoformat(iso_str).strftime('%I:%M %p IST')}`!\n"
                "Click **Toggle Focus** again when you finish to claim your XP, Gold & END stats.",
                ephemeral=True,
            )
        else:
            result = self.bot.db.stop_focus()
            if result:
                mins, src, is_exam = result
                summary = await self.bot.finalize_focus_session(mins, src, interaction.user, is_exam_session=is_exam)
                await interaction.followup.send(summary, ephemeral=True)


class ChapterCodexView(discord.ui.View):
    """
    Persistent paginated & filterable view for the NEET 2027 Chapter Codex
    (#codex-topics and /chapters).
    """

    def __init__(
        self,
        bot: "StudyRPGBot",
        subject_filter: str = "ALL",
        class_filter: Optional[int] = None,
        page: int = 0,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.subject_filter = subject_filter
        self.class_filter = class_filter
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message(
                "⛔ **Access Denied:** Locked to the Sovereign Hunter.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.select(
        placeholder="Filter by Subject / Class / Notes-Only Trap...",
        custom_id="codex_filter_select",
        options=[
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
        ],
    )
    async def filter_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        chosen = select.values[0]
        subj_part, cls_part = chosen.split("|", 1)
        self.subject_filter = subj_part
        cls_int = int(cls_part)
        self.class_filter = cls_int if cls_int in (11, 12) else None
        self.page = 0

        embed, self.page, _ = build_chapter_codex_embed(
            self.bot.db,
            subject_filter=self.subject_filter,
            class_filter=self.class_filter,
            page=self.page,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Prev",
        style=discord.ButtonStyle.secondary,
        emoji="◀️",
        custom_id="codex_prev_page",
    )
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        embed, self.page, _ = build_chapter_codex_embed(
            self.bot.db,
            subject_filter=self.subject_filter,
            class_filter=self.class_filter,
            page=self.page,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.secondary,
        emoji="▶️",
        custom_id="codex_next_page",
    )
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        embed, self.page, _ = build_chapter_codex_embed(
            self.bot.db,
            subject_filter=self.subject_filter,
            class_filter=self.class_filter,
            page=self.page,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Notes-Only Reality Check",
        style=discord.ButtonStyle.danger,
        emoji="🚨",
        custom_id="codex_trap_check",
    )
    async def trap_reality_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        traps = self.bot.db.get_filtered_chapters(subject_filter="TRAPS")
        if not traps:
            await interaction.response.send_message(
                "✅ **Reality Check Passed:** No chapters are stuck in a Notes-Only trap right now.",
                ephemeral=True,
            )
            return

        listed = "\n".join(
            f"• **{c['chapter_name']}** (`{c['subject']} Cl {c['class_level']}`) — Notes ✅, MCQs ⬜, Revised ⬜"
            for c in traps[:15]
        )
        await interaction.response.send_message(
            f"🚨 **NOTES-ONLY TRAP REALITY CHECK ({len(traps)} chapters):**\n"
            "Collecting notes without MCQ practice or revision does not lock NEET marks. Grind MCQs & revise!\n"
            f"{listed}",
            ephemeral=True,
        )


# ==============================================================================
# 5. CORE RPG BOT ENGINE & SCHEDULED LOOPS
# ==============================================================================

class StudyRPGBot(commands.Bot):
    """Main Discord Bot subclass encapsulating RPG logic, tasks, and voice tracking."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.guilds = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = DatabaseManager("study_rpg.db")

    async def setup_hook(self) -> None:
        """Registers persistent views, global user checks, and syncs slash commands."""
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

        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)
        await self.tree.sync(guild=guild_obj)
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        logger.info("Slash commands synced to Guild ID %s (stale global commands cleared)", GUILD_ID)

        if not self.midnight_tribunal_task.is_running():
            self.midnight_tribunal_task.start()
        if not self.weekly_report_task.is_running():
            self.weekly_report_task.start()
        if not self.dashboard_heartbeat_task.is_running():
            self.dashboard_heartbeat_task.start()

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (ID: %s) | IST Time: %s",
            self.user,
            self.user.id,
            now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        )

    async def get_owner_member(self, guild: discord.Guild) -> Optional[discord.Member]:
        member = guild.get_member(ALLOWED_USER_ID)
        if member:
            return member
        try:
            return await guild.fetch_member(ALLOWED_USER_ID)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def sync_hunter_roles(self, guild: discord.Guild) -> Optional[str]:
        member = await self.get_owner_member(guild)
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

        for ch_key, msg_key, embed_fn, view_obj in [
            ("quest-board", "msg_quest-board", lambda: build_quest_board_embed(self.db), QuestBoardView(self)),
            ("status-window", "msg_status-window", lambda: build_status_window_embed(self.db), None),
            ("boss-raids", "msg_boss-raids", lambda: build_boss_raids_embed(self.db), None),
        ]:
            ch_id = self.db.get_config(f"ch_{ch_key}")
            msg_id = self.db.get_config(msg_key)
            if ch_id and msg_id:
                ch = guild.get_channel(ch_id)
                if isinstance(ch, discord.TextChannel):
                    try:
                        msg = await ch.fetch_message(msg_id)
                        await msg.edit(embed=embed_fn(), view=view_obj)
                    except discord.NotFound:
                        logger.warning("Pinned message for %s not found.", ch_key)

    async def refresh_codex_channel(self) -> None:
        guild = self.get_guild(GUILD_ID)
        if not guild:
            return
        codex_ch_id = self.db.get_config("ch_codex-topics")
        codex_msg_id = self.db.get_config("msg_codex-topics")
        if codex_ch_id and codex_msg_id:
            ch = guild.get_channel(codex_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(codex_msg_id)
                    embed, _, _ = build_chapter_codex_embed(self.db)
                    await msg.edit(embed=embed, view=ChapterCodexView(self))
                except discord.NotFound:
                    pass

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

    # --- Unified Gameplay Action Processors ---

    async def finalize_focus_session(
        self, minutes: int, source: str, actor: discord.abc.User, is_exam_session: bool = False
    ) -> str:
        if minutes < 1:
            await self.refresh_all_dashboards()
            return "⚠️ Focus session ended under **1 minute** — no XP or Gold awarded."

        prof_now = self.db.get_profile()
        exam_active = bool(prof_now.get("exam_mode_active")) or is_exam_session

        xp_gain = minutes * 2
        gold_gain = minutes * 1
        end_gain = max(1, minutes // 30)
        wis_bonus = max(1, minutes // 60) if exam_active else 0

        self.db.record_activity_telemetry(
            minutes=minutes,
            exam_minutes=minutes if exam_active else 0,
        )

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_end=end_gain,
            d_wis=wis_bonus,
            d_minutes=minutes,
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None

        exam_tag = " [Exam / Test Block]" if exam_active else ""
        wis_line = f" • `+{wis_bonus} WIS (Exam Mode)`" if wis_bonus > 0 else ""

        receipt = (
            f"**Source:** `{source}{exam_tag}`\n"
            f"**Duration:** `{minutes} minutes`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{end_gain} END`{wis_line}\n"
            f"**Daily Study Progress:** `{prof['daily_minutes']} / {DAILY_TARGET_MINUTES} mins`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt("⏱️ FOCUS DUNGEON CLEARED", receipt, color=0x3498DB)
        await self.refresh_all_dashboards()
        return f"✅ **Focus Session Logged ({minutes} mins):** `+{xp_gain} XP`, `+{gold_gain} Gold`, `+{end_gain} END`{wis_line}."

    async def process_mcq_log(
        self, subject: str, count: int, source: str, actor: discord.abc.User
    ) -> str:
        subj_norm = normalize_subject_name(subject)
        bal = self.db.get_balance_assessment()

        if subj_norm == "Biology":
            base_rate = 1.0
            mult = bal["multipliers"]["Bio"]
            d_bio, d_phy, d_chem = count, 0, 0
            label = "Biology"
        elif subj_norm == "Physics":
            base_rate = 2.0
            mult = bal["multipliers"]["Physics"]
            d_bio, d_phy, d_chem = 0, count, 0
            label = "Physics"
        else:
            base_rate = 1.5
            mult = bal["multipliers"]["Chem"]
            d_bio, d_phy, d_chem = 0, 0, count
            label = "Chemistry"

        xp_gain = max(1, int(round(count * base_rate * mult)))
        gold_gain = max(1, count // 2)
        dex_gain = max(1, count // 25)

        self.db.record_activity_telemetry(
            bio_mcqs=d_bio,
            phy_mcqs=d_phy,
            chem_mcqs=d_chem,
            execution_delta=max(1, count // 50),
        )

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_dex=dex_gain,
            d_bio=d_bio,
            d_phy=d_phy,
            d_chem=d_chem,
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None

        nudge_line = f"\n⚖️ *Balance Nudge Applied (`{mult:.2f}x` XP)*" if mult < 1.0 else ""

        receipt = (
            f"**Subject:** `{label}` ({source})\n"
            f"**Solved:** `+{count} MCQs`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{dex_gain} DEX`{nudge_line}\n"
            f"**Today's Tally:** Bio `{prof['daily_bio_mcqs']}/{DAILY_TARGET_BIO}` | "
            f"Phy `{prof['daily_phy_mcqs']}/{DAILY_TARGET_PHY}`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt(f"🎯 {label.upper()} MCQ BARRAGE", receipt, color=0x2ECC71)
        await self.refresh_all_dashboards()
        return receipt

    async def process_topic_log(
        self, subject: str, chapter: str, topic: str, mode: str, actor: discord.abc.User
    ) -> str:
        boss_telemetry = self.db.record_topic_and_damage_boss(subject, chapter, topic, mode)
        bal = self.db.get_balance_assessment()

        is_concept = mode.lower() == "concept"
        if is_concept:
            mult = bal["multipliers"]["Concept"]
            xp_gain = max(20, int(round(120 * mult)))
            gold_gain = 60
            d_int, d_wis = 2, 0
            stat_label = "+2 INT (Concept / Short Notes)"
            self.db.record_activity_telemetry(concept_delta=1)
        else:
            mult = bal["multipliers"]["Revision"]
            xp_gain = max(20, int(round(100 * mult)))
            gold_gain = 50
            d_int, d_wis = 0, 2
            stat_label = "+2 WIS (Active Revision)"
            self.db.record_activity_telemetry(execution_delta=1)

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
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None
        cleared, total = self.db.get_syllabus_totals()

        boss_hp_line = ""
        if boss_telemetry:
            boss_hp_line = (
                f"\n👹 **Boss HP ({boss_telemetry['chapter']}):** "
                f"`{boss_telemetry['current_hp']} / {boss_telemetry['max_hp']} HP` "
                f"(`{boss_telemetry['completed_topics']}/{boss_telemetry['total_topics']} topics`)"
            )

        nudge_line = f"\n⚖️ *Balance Nudge Applied (`{mult:.2f}x` XP)*" if mult < 1.0 else ""

        receipt = (
            f"**Subject:** `{normalize_subject_name(subject)}` • **Chapter:** `{chapter}`\n"
            f"**Topic:** `{topic}` (`{mode}`)\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `{stat_label}`{nudge_line}"
            f"{boss_hp_line}"
            f"{boss_bonus_text}\n"
            f"**Overall Syllabus:** `{cleared} / {total} Topics Cleared`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt(
            "🏆 CHAPTER BOSS VANQUISHED!" if (boss_telemetry and boss_telemetry["just_defeated"]) else "📘 SYLLABUS BOSS STRIKE",
            receipt,
            color=0xF1C40F if (boss_telemetry and boss_telemetry["just_defeated"]) else 0x9B59B6,
        )

        if guild:
            codex_id = self.db.get_config("ch_codex-topics")
            if codex_id:
                codex_ch = guild.get_channel(codex_id)
                if isinstance(codex_ch, discord.TextChannel):
                    await codex_ch.send(
                        f"📗 `[{now_ist().strftime('%d %b %Y')}]` **{normalize_subject_name(subject)}** › *{chapter}* › **{topic}** `[{mode}]`"
                    )

        await self.refresh_all_dashboards()
        return receipt

    # --- Voice Auto-Tracker (#focus-dungeon) ---

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
            prof = self.db.get_profile()
            started, _ = self.db.start_focus(
                source="Voice #focus-dungeon", is_exam_session=bool(prof.get("exam_mode_active"))
            )
            if started:
                await self.refresh_all_dashboards()
        elif left_dungeon:
            result = self.db.stop_focus()
            if result:
                mins, src, is_exam = result
                await self.finalize_focus_session(mins, src, member, is_exam_session=is_exam)

    # --- Scheduled Tasks (23:55 IST Audit, Sunday 21:00 IST Weekly Report & Heartbeat) ---

    @tasks.loop(time=time(hour=23, minute=55, second=0, tzinfo=IST))
    async def midnight_tribunal_task(self) -> None:
        logger.info("Executing 23:55 IST Ruthless Midnight Audit at %s", now_ist())
        verdict = self.db.run_midnight_audit()
        if not verdict:
            logger.info("Audit already executed for today; skipping duplicate tick.")
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
                            else "⚖️ **ALL DAILY TARGETS CRUSHED — 2X GOLD BONUS AWARDED!**"
                        )
                        desc = (
                            f"{reason}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• **Daily Clear Gold Bonus:** `+{verdict['bonus_gold']} G` (Total: `{verdict['new_gold']:,} G`)\n"
                            f"• **HP Recovery:** `{verdict['hp_delta']:+d} HP` (`{verdict['new_hp']}/{verdict['max_hp']} HP`)\n"
                            f"• **Accountability Streak:** `🔥 {verdict['new_streak']} Days`"
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
                            rq_block = (
                                "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                "⛓️ **24-HOUR RECOVERY QUEST GENERATED (`/redeem`)**\n"
                                f"• Complete `{rq['req_minutes']} mins` focus\n"
                                f"• Solve `{rq['req_mcqs']}` combined MCQs\n"
                                f"• Cleanse `{rq['req_curses_cleansed']}` curses from `#cursed-vault` within **24 hours** "
                                "to restore `+35 HP`, clear `⚠️ Fatigued`, and claim `+200 XP` / `+150 G`!"
                            )

                        desc = (
                            f"💀 **VERDICT: UNEXCUSED FAILURE (NO DAILY CLASSES EXCUSE)**\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Missed Quests (`-15 HP` each):**\n{missed_block}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• **HP Penalty:** `{verdict['hp_delta']} HP` (`{verdict['new_hp']}/{verdict['max_hp']} HP`)\n"
                            f"• **Streak Obliterated:** `0 Days`\n"
                            f"• **Status Debuff:** `⚠️ Fatigued`"
                            f"{catastrophe_lines}"
                            f"{rq_block}"
                        )
                        color = 0xE74C3C

                    embed = discord.Embed(
                        title="💀 RUTHLESS MIDNIGHT AUDIT (23:55 IST)",
                        description=desc,
                        color=color,
                        timestamp=now_ist(),
                    )
                    await ch.send(content=f"<@{ALLOWED_USER_ID}>", embed=embed)

        await self.refresh_all_dashboards()

    @tasks.loop(time=time(hour=21, minute=0, second=0, tzinfo=IST))
    async def weekly_report_task(self) -> None:
        """Automatically posts the Weekly Report every Sunday at 21:00 IST."""
        now_dt = now_ist()
        if now_dt.weekday() != 6:  # Sunday == 6
            return

        prof = self.db.get_profile()
        today_str = now_dt.date().isoformat()
        if prof.get("last_weekly_report_date") == today_str:
            return

        with self.db._connect() as conn:
            conn.execute(
                "UPDATE hunter_profile SET last_weekly_report_date = ? WHERE user_id = ?",
                (today_str, ALLOWED_USER_ID),
            )
            conn.commit()

        guild = self.get_guild(GUILD_ID)
        if guild:
            tribunal_ch_id = self.db.get_config("ch_midnight-tribunal")
            if tribunal_ch_id:
                ch = guild.get_channel(tribunal_ch_id)
                if isinstance(ch, discord.TextChannel):
                    await ch.send(content=f"<@{ALLOWED_USER_ID}>", embed=build_weekly_report_embed(self.db))

    @tasks.loop(minutes=15)
    async def dashboard_heartbeat_task(self) -> None:
        await self.refresh_all_dashboards()

    @weekly_report_task.before_loop
    @dashboard_heartbeat_task.before_loop
    @midnight_tribunal_task.before_loop
    async def before_tasks(self) -> None:
        await self.wait_until_ready()


bot = StudyRPGBot()


# ==============================================================================
# 6. SLASH COMMANDS (APP COMMANDS)
# ==============================================================================

async def chapter_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    rows = bot.db.search_chapters_autocomplete(current)
    choices: List[app_commands.Choice[str]] = []
    for r in rows[:25]:
        label = f"{r['chapter_name']} ({r['subject']} • Cl {r['class_level']})"
        choices.append(app_commands.Choice(name=label[:100], value=r["chapter_name"][:100]))
    return choices


@bot.tree.command(
    name="setup_world",
    description="Builds/repairs all private RPG channels, roles, NEET 2027 chapter codex & live embeds.",
)
async def setup_world(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Must be run inside a server.", ephemeral=True)
        return

    owner_member = await bot.get_owner_member(guild)
    if not owner_member:
        await interaction.followup.send(
            f"❌ Could not find `ALLOWED_USER_ID` (`{ALLOWED_USER_ID}`) in this server. "
            "Make sure **Server Members Intent** is enabled in the Discord Developer Portal!",
            ephemeral=True,
        )
        return

    seeded_now = bot.db.seed_neet_2027_chapters()

    for _, role_name, color_hex in RANK_TIERS:
        existing_role = discord.utils.get(guild.roles, name=role_name)
        if not existing_role:
            existing_role = await guild.create_role(
                name=role_name,
                color=discord.Color(color_hex),
                hoist=True,
                reason="NEET 2027 RPG World Builder",
            )
        bot.db.set_config(f"role_{role_name}", existing_role.id)

    fatigued_role = discord.utils.get(guild.roles, name=FATIGUED_ROLE_NAME)
    if not fatigued_role:
        fatigued_role = await guild.create_role(
            name=FATIGUED_ROLE_NAME,
            color=discord.Color(0xE74C3C),
            hoist=True,
            reason="NEET 2027 RPG Debuff Role",
        )
    bot.db.set_config(f"role_{FATIGUED_ROLE_NAME}", fatigued_role.id)

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
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(
                name=category_name,
                overwrites=private_overwrites,
                reason="NEET 2027 RPG World Builder",
            )
        else:
            await category.edit(overwrites=private_overwrites)

        for ch_name, ch_type in channels:
            if ch_type == "text":
                ch = discord.utils.get(category.text_channels, name=ch_name)
                if not ch:
                    ch = await guild.create_text_channel(
                        name=ch_name,
                        category=category,
                        overwrites=private_overwrites,
                        reason="NEET 2027 RPG World Builder",
                    )
                else:
                    await ch.edit(overwrites=private_overwrites)
            else:
                ch = discord.utils.get(category.voice_channels, name=ch_name)
                if not ch:
                    ch = await guild.create_voice_channel(
                        name=ch_name,
                        category=category,
                        overwrites=private_overwrites,
                        reason="NEET 2027 RPG World Builder",
                    )
                else:
                    await ch.edit(overwrites=private_overwrites)

            bot.db.set_config(f"ch_{ch_name}", ch.id)
            created_channels[ch_name] = ch

    async def ensure_pinned_dashboard(
        ch_key: str,
        msg_key: str,
        embed: discord.Embed,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        ch = created_channels.get(ch_key)
        if not isinstance(ch, discord.TextChannel):
            return

        existing_msg_id = bot.db.get_config(msg_key)
        if existing_msg_id:
            try:
                msg = await ch.fetch_message(existing_msg_id)
                await msg.edit(embed=embed, view=view)
                if not msg.pinned:
                    await msg.pin(reason="RPG Live Dashboard")
                return
            except discord.NotFound:
                pass

        new_msg = await ch.send(embed=embed, view=view)
        await new_msg.pin(reason="RPG Live Dashboard")
        bot.db.set_config(msg_key, new_msg.id)

    await ensure_pinned_dashboard(
        "quest-board", "msg_quest-board", build_quest_board_embed(bot.db), view=QuestBoardView(bot)
    )
    await ensure_pinned_dashboard(
        "status-window", "msg_status-window", build_status_window_embed(bot.db)
    )
    await ensure_pinned_dashboard(
        "boss-raids", "msg_boss-raids", build_boss_raids_embed(bot.db)
    )

    codex_embed, _, _ = build_chapter_codex_embed(bot.db)
    await ensure_pinned_dashboard(
        "codex-topics", "msg_codex-topics", codex_embed, view=ChapterCodexView(bot)
    )

    shop_ch = created_channels.get("inventory-and-shop")
    if isinstance(shop_ch, discord.TextChannel):
        history = [m async for m in shop_ch.history(limit=1)]
        if not history:
            await shop_ch.send(embed=build_shop_embed(bot.db))

    await bot.sync_hunter_roles(guild)

    await interaction.followup.send(
        "✅ **World Construction Complete!**\n"
        f"• Seeded `{seeded_now}` new NEET 2027 chapters (`{len(NEET_2027_CHAPTERS)}` total across Physics, Chemistry & Biology).\n"
        "• Pinned live dashboards in `#quest-board`, `#status-window`, `#boss-raids`, and `#codex-topics`.",
        ephemeral=True,
    )


# --- NEET 2027 Chapter Codex Commands (/chapters, /chapter_toggle, /chapter_add) ---

@bot.tree.command(
    name="chapters",
    description="Open the interactive paginated NEET 2027 chapter & 5-stage revision checklist browser.",
)
@app_commands.describe(
    subject="Optional subject filter (All, Physics, Chemistry, Biology, or Traps)",
    class_level="Optional class filter (11 or 12)",
)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="All Subjects", value="ALL"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chemistry", value="Chemistry"),
        app_commands.Choice(name="Biology", value="Biology"),
        app_commands.Choice(name="Notes-Only Trap Chapters", value="TRAPS"),
    ],
    class_level=[
        app_commands.Choice(name="Class 11", value=11),
        app_commands.Choice(name="Class 12", value=12),
    ],
)
async def chapters_command(
    interaction: discord.Interaction,
    subject: Optional[app_commands.Choice[str]] = None,
    class_level: Optional[app_commands.Choice[int]] = None,
):
    subj_val = subject.value if subject else "ALL"
    cls_val = class_level.value if class_level else None
    embed, page_idx, _ = build_chapter_codex_embed(
        bot.db, subject_filter=subj_val, class_filter=cls_val, page=0
    )
    view = ChapterCodexView(bot, subject_filter=subj_val, class_filter=cls_val, page=page_idx)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(
    name="chapter_toggle",
    description="Toggle one of the 5 revision stages (20% each) on a NEET 2027 chapter & sync Boss HP.",
)
@app_commands.describe(
    chapter="NEET 2027 chapter name",
    stage="Which of the 5 revision stages to toggle",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    stage=[
        app_commands.Choice(name="1. Short Notes (Concept + Crisp Notes)", value="short_notes"),
        app_commands.Choice(name="2. Topics Done (Subtopic-Wise Sweep)", value="topics_done"),
        app_commands.Choice(name="3. MCQs Done (Chapter MCQ Grind)", value="mcqs_done"),
        app_commands.Choice(name="4. Mistakes Logged (#cursed-vault / NCERT)", value="mistakes_logged"),
        app_commands.Choice(name="5. Revised (Full Revision / Test Lock)", value="revised"),
    ]
)
async def chapter_toggle_command(
    interaction: discord.Interaction,
    chapter: str,
    stage: app_commands.Choice[str],
):
    await interaction.response.defer(ephemeral=True)
    matched, candidates = bot.db.resolve_chapter(chapter)
    if not matched:
        if candidates:
            await interaction.followup.send(
                "⚠️ Multiple chapters matched `" + chapter + "`. Pick one specifically:\n• "
                + "\n• ".join(candidates),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"❌ Chapter `{chapter}` not found in the NEET 2027 syllabus.", ephemeral=True
            )
        return

    res = bot.db.toggle_chapter_stage(matched["id"], stage.value)
    if not res:
        await interaction.followup.send("❌ Could not toggle stage.", ephemeral=True)
        return

    stage_key = stage.value
    turned_on = res["new_val"] == 1
    reward_line = ""
    leveled_up = False

    if turned_on:
        if stage_key == "short_notes":
            bot.db.record_activity_telemetry(concept_delta=1, chapters_delta=1)
            prof, leveled_up = bot.db.award_rewards(xp_gain=90, gold_gain=45, d_int=2, d_topics=1)
        elif stage_key == "topics_done":
            bot.db.record_activity_telemetry(concept_delta=1, chapters_delta=1)
            prof, leveled_up = bot.db.award_rewards(xp_gain=100, gold_gain=50, d_int=2, d_topics=1)
        elif stage_key == "mcqs_done":
            bot.db.record_activity_telemetry(execution_delta=1, chapters_delta=1)
            prof, leveled_up = bot.db.award_rewards(xp_gain=120, gold_gain=60, d_dex=2, d_topics=1)
        elif stage_key == "mistakes_logged":
            prof_now = bot.db.get_profile()
            wis_g = 3 if prof_now.get("exam_mode_active") else 2
            xp_g = 120 if prof_now.get("exam_mode_active") else 95
            bot.db.record_activity_telemetry(execution_delta=1, chapters_delta=1)
            prof, leveled_up = bot.db.award_rewards(xp_gain=xp_g, gold_gain=50, d_wis=wis_g, d_topics=1)
        else:  # revised
            bot.db.record_activity_telemetry(execution_delta=1, chapters_delta=1)
            prof, leveled_up = bot.db.award_rewards(xp_gain=140, gold_gain=70, d_wis=2, d_topics=1)

        if res["just_defeated"]:
            prof, lvl2 = bot.db.award_rewards(xp_gain=250, gold_gain=150)
            leveled_up = leveled_up or lvl2

        reward_line = f"\n✅ Stage **COMPLETED** (`{res['completion_pct']}%` mastery • Boss HP: `{res['boss_hp_cur']}/{res['boss_hp_max']}`)"
        if res["just_defeated"]:
            reward_line += f"\n🏆 **CHAPTER BOSS DEFEATED!** (`+250 XP`, `+150 Gold` bonus)"
        if leveled_up:
            reward_line += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    else:
        reward_line = f"\n↩️ Stage **UNCHECKED** (`{res['completion_pct']}%` mastery • Boss HP: `{res['boss_hp_cur']}/{res['boss_hp_max']}`)"

    trap_warning = ""
    if res["is_notes_only_now"]:
        trap_warning = (
            "\n🚨 **Notes-Only Warning:** You now have `Notes ✅` without `MCQs ✅` or `Revised ✅`. "
            "Don't stall on notes—push MCQ practice and revision!"
        )

    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
    await bot.refresh_all_dashboards()
    await bot.refresh_codex_channel()
    await bot.post_battle_receipt(
        "📘 CHAPTER STAGE UPDATED",
        f"**Chapter:** `{res['chapter_name']}` (`{res['subject']} Cl {res['class_level']}`)\n"
        f"**Stage:** `{stage.name}` → `{'ON ✅' if turned_on else 'OFF ⬜'}`\n"
        f"**Mastery:** `{res['completion_pct']}%` {build_stage_bar(res['stages_done'])}{reward_line}{trap_warning}",
        color=0x1ABC9C if turned_on else 0x95A5A6,
    )

    await interaction.followup.send(
        f"**{res['chapter_name']}** (`{res['completion_pct']}%` {build_stage_bar(res['stages_done'])})"
        f"{reward_line}{trap_warning}",
        ephemeral=True,
    )


@bot.tree.command(
    name="chapter_add",
    description="Add a custom chapter/sub-module to the NEET 2027 tracker.",
)
@app_commands.describe(
    subject="Subject (Physics, Chemistry, or Biology)",
    class_level="Class 11 or Class 12",
    chapter="Custom chapter or sub-module name",
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
        await bot.refresh_codex_channel()
    await interaction.followup.send(msg, ephemeral=True)


# --- Exam Mode (/exam_mode) ---

@bot.tree.command(
    name="exam_mode",
    description="Toggle Exam Mode for test blocks (+50% WIS XP on tests/mistakes, protected session tags).",
)
@app_commands.describe(
    state="Turn Exam Mode on or off",
    test_name="Optional test name (e.g., Allen Minor Test 05)",
)
@app_commands.choices(
    state=[
        app_commands.Choice(name="ON — Activate Exam Mode", value="on"),
        app_commands.Choice(name="OFF — Return to Regular Grind", value="off"),
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
        active_label = prof.get("exam_mode_test_name") or "Test Prep"
        msg = (
            f"🎯 **EXAM MODE ENABLED (`{active_label}`)**\n"
            "• `/test_log`, `/curse add`, and `/curse cleanse` award **+50% bonus WIS XP**.\n"
            "• Focus sessions started while active are tagged as **Exam / Post-Test Analysis**.\n"
            "• Badge displayed on `#quest-board` and `#status-window`."
        )
    else:
        msg = "🔄 **EXAM MODE DISABLED.** Returned to standard daily grind mode."

    await bot.post_battle_receipt("🎯 EXAM MODE STATUS", msg, color=0xE67E22 if is_on else 0x95A5A6)
    await interaction.followup.send(msg, ephemeral=True)


# --- Next-Topic Queue (/queue_add & /next_topic) ---

@bot.tree.command(
    name="queue_add",
    description="Add a topic to your NEET 2027 priority queue tied to a chapter and Dec 15, 2026 target.",
)
@app_commands.describe(
    subject="Bio, Physics, or Chem",
    chapter="NEET 2027 chapter name",
    topic="Specific subtopic or NCERT section",
    priority="1 = Urgent/High, 2 = Normal, 3 = Low",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Bio", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chem", value="Chem"),
    ],
    priority=[
        app_commands.Choice(name="1 — High Priority", value=1),
        app_commands.Choice(name="2 — Normal Priority", value=2),
        app_commands.Choice(name="3 — Low Priority", value=3),
    ],
)
async def queue_add_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    chapter: str,
    topic: str,
    priority: Optional[app_commands.Choice[int]] = None,
):
    prio_val = priority.value if priority else 2
    matched_chap, _ = bot.db.resolve_chapter(chapter)
    chap_id = matched_chap["id"] if matched_chap else None
    chap_canonical = matched_chap["chapter_name"] if matched_chap else chapter

    qid = bot.db.add_to_queue(
        subject=subject.value,
        chapter_name=chap_canonical,
        topic_name=topic,
        priority=prio_val,
        chapter_id=chap_id,
    )
    pending_cnt = bot.db.get_pending_queue_count()
    await interaction.response.send_message(
        f"📌 **Queued Topic `[ID {qid}]`:** **{topic}**\n"
        f"• **Subject / Chapter:** `{subject.value}` › *{chap_canonical}*\n"
        f"• **Priority:** `P{prio_val}` • **Pending Queue Size:** `{pending_cnt}`",
        ephemeral=True,
    )


@bot.tree.command(
    name="next_topic",
    description="View the top-priority queued topic, Dec 15, 2026 pace, or mark it completed.",
)
@app_commands.describe(
    action="View current top topic or mark it completed as Concept or Revision",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="View Next Topic & Pace", value="view"),
        app_commands.Choice(name="Complete as Concept (+INT XP)", value="complete_concept"),
        app_commands.Choice(name="Complete as Revision (+WIS XP)", value="complete_revision"),
    ]
)
async def next_topic_command(
    interaction: discord.Interaction,
    action: Optional[app_commands.Choice[str]] = None,
):
    act = action.value if action else "view"
    current_top = bot.db.get_next_pending_topic()

    if not current_top:
        await interaction.response.send_message(
            "📭 **Topic Queue Empty!** Add topics with `/queue_add subject:... chapter:... topic:...`.",
            ephemeral=True,
        )
        return

    if act in ("complete_concept", "complete_revision"):
        await interaction.response.defer(ephemeral=True)
        completed = bot.db.complete_queued_topic(current_top["id"])
        if completed:
            mode_str = "Concept" if act == "complete_concept" else "Revision"
            await bot.process_topic_log(
                subject=completed["subject"],
                chapter=completed["chapter_name"],
                topic=completed["topic_name"],
                mode=mode_str,
                actor=interaction.user,
            )
            current_top = bot.db.get_next_pending_topic()
            next_info = (
                f"\n\n➡️ **Up Next:** **{current_top['topic_name']}** (`{current_top['subject']}` • *{current_top['chapter_name']}*)"
                if current_top
                else "\n\n🎉 **All queued topics cleared!**"
            )
            await interaction.followup.send(
                f"✅ Completed queued topic **{completed['topic_name']}** (`{mode_str}`){next_info}",
                ephemeral=True,
            )
            return

    days_left = max(0, (TARGET_MASTERY_DATE - now_ist().date()).days)
    pending_cnt = bot.db.get_pending_queue_count()
    pace = (pending_cnt / days_left) if days_left > 0 else float(pending_cnt)

    embed = discord.Embed(
        title="🎯 NEXT PRIORITY TOPIC — NEET 2027 QUEUE",
        description=(
            f"**Topic:** **{current_top['topic_name']}**\n"
            f"**Subject:** `{current_top['subject']}` • **Chapter:** *{current_top['chapter_name']}*\n"
            f"**Priority:** `P{current_top['priority']}` • **Queue ID:** `#{current_top['id']}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Pending Queued Topics:** `{pending_cnt}`\n"
            f"**Days Until Dec 15, 2026:** `{days_left} days` (`{pace:.2f} queued topics/day`)\n"
            f"*Workflow reminder: Keep concept & short notes strictly NEET-focused (no JEE-level detours), then sweep topics & MCQs.*"
        ),
        color=0x2ECC71,
        timestamp=now_ist(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Streak Recovery Quest (/redeem) ---

@bot.tree.command(
    name="redeem",
    description="Check progress on or claim your 24-hour Streak Recovery Quest after a broken streak.",
)
@app_commands.describe(action="Check status or claim completion reward")
@app_commands.choices(
    action=[
        app_commands.Choice(name="Check Recovery Status", value="status"),
        app_commands.Choice(name="Claim Recovery Reward", value="claim"),
    ]
)
async def redeem_command(
    interaction: discord.Interaction,
    action: Optional[app_commands.Choice[str]] = None,
):
    act = action.value if action else "status"
    if act == "claim":
        await interaction.response.defer(ephemeral=True)
        ok, msg = bot.db.claim_recovery_quest()
        if ok:
            if interaction.guild:
                await bot.sync_hunter_roles(interaction.guild)
            await bot.refresh_all_dashboards()
            await bot.post_battle_receipt("⛓️‍💥 STREAK RECOVERY REDEEMED", msg, color=0x2ECC71)
        await interaction.followup.send(msg, ephemeral=True)
        return

    q = bot.db.get_active_recovery_quest()
    if not q:
        await interaction.response.send_message(
            "✨ **No Active Recovery Quest.** Your streak is either intact or no 24-hour recovery window is open.",
            ephemeral=True,
        )
        return

    ready_str = "✅ **READY TO CLAIM (`/redeem action:claim`)**" if q["is_complete"] else "⏳ **IN PROGRESS**"
    embed = discord.Embed(
        title=f"⛓️ 24-HOUR STREAK RECOVERY QUEST (#{q['id']})",
        description=(
            f"**Status:** {ready_str}\n"
            f"**Time Remaining:** `{q['remaining_hours']:.1f} hours`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Focus Time:** `{q['prog_minutes']} / {q['req_minutes']} mins`\n"
            f"• **Combined MCQs:** `{q['prog_mcqs']} / {q['req_mcqs']}`\n"
            f"• **Curses Cleansed:** `{q['prog_curses_cleansed']} / {q['req_curses_cleansed']}`"
        ),
        color=0xE67E22,
        timestamp=now_ist(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Weekly Report (/weekly_report) ---

@bot.tree.command(
    name="weekly_report",
    description="Generate your rolling 7-day NEET 2027 accountability, balance & pace report.",
)
async def weekly_report_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_weekly_report_embed(bot.db), ephemeral=True)


# --- /boss Group (/boss add & /boss list) ---

boss_group = app_commands.Group(name="boss", description="Manage NEET Chapter Bosses and track syllabus HP.")


@boss_group.command(name="add", description="Spawn a Chapter Boss with HP proportional to total_topics.")
@app_commands.describe(
    subject="Subject name (e.g., Biology, Physics, Chemistry)",
    chapter="Chapter name (e.g., Electrostatics, Human Reproduction)",
    total_topics="Total sub-topics in this chapter (100 HP per topic)",
)
async def boss_add(
    interaction: discord.Interaction,
    subject: str,
    chapter: str,
    total_topics: app_commands.Range[int, 1, 100],
):
    await interaction.response.defer(ephemeral=True)
    boss = bot.db.add_boss(subject, chapter, total_topics)
    await bot.refresh_all_dashboards()

    max_hp = boss["total_topics"] * BOSS_HP_PER_TOPIC
    cur_hp = max(0, boss["total_topics"] - boss["completed_topics"]) * BOSS_HP_PER_TOPIC
    await interaction.followup.send(
        f"👹 **Chapter Boss Spawned!**\n"
        f"**Boss:** `[ID {boss['id']}] {boss['subject']} — {boss['chapter']}`\n"
        f"**Boss HP:** `{cur_hp} / {max_hp} HP` (`{boss['total_topics']} topics`)",
        ephemeral=True,
    )


@boss_group.command(name="list", description="View all active and defeated Chapter Bosses with HP bars.")
async def boss_list(interaction: discord.Interaction):
    bosses = bot.db.get_all_bosses()
    if not bosses:
        await interaction.response.send_message(
            "📭 No Chapter Bosses spawned yet.", ephemeral=True
        )
        return

    lines = []
    for b in bosses[:15]:
        rem_t = max(0, b["total_topics"] - b["completed_topics"])
        cur_hp = rem_t * BOSS_HP_PER_TOPIC
        max_hp = b["total_topics"] * BOSS_HP_PER_TOPIC
        pct = int(round((b["completed_topics"] / max(1, b["total_topics"])) * 100))
        icon = "🏆" if b["status"] == "DEFEATED" else "👹"
        bar = build_progress_bar(cur_hp, max_hp, length=10)
        lines.append(
            f"{icon} **[ID {b['id']}] {b['subject']} — {b['chapter']}** (`{pct}%` cleared)\n"
            f"   HP: `{cur_hp}/{max_hp}` {bar}"
        )

    embed = discord.Embed(
        title="🐉 CHAPTER BOSS REGISTRY",
        description="\n".join(lines),
        color=0x9B59B6,
        timestamp=now_ist(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(boss_group)


# --- /focus Group (/focus start & /focus stop) ---

focus_group = app_commands.Group(name="focus", description="Manual study focus session timer controls.")


@focus_group.command(name="start", description="Starts a manual deep-focus study timer.")
async def focus_start(interaction: discord.Interaction):
    prof = bot.db.get_profile()
    started, iso_str = bot.db.start_focus(
        source="Manual /focus", is_exam_session=bool(prof.get("exam_mode_active"))
    )
    if not started:
        start_dt = datetime.fromisoformat(iso_str)
        elapsed = int((now_ist() - start_dt).total_seconds() // 60)
        await interaction.response.send_message(
            f"⚠️ A focus session is already active (started `{start_dt.strftime('%I:%M %p IST')}`, `{elapsed} mins` elapsed).",
            ephemeral=True,
        )
        return

    await bot.refresh_all_dashboards()
    await interaction.response.send_message(
        f"🔥 **Focus Session Started** at `{now_ist().strftime('%I:%M %p IST')}`!\n"
        "Use `/focus stop` when done.",
        ephemeral=True,
    )


@focus_group.command(name="stop", description="Stops the active focus session and claims XP, Gold & END.")
async def focus_stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = bot.db.stop_focus()
    if not result:
        await interaction.followup.send("⚠️ No active focus session is currently running.", ephemeral=True)
        return

    mins, src, is_exam = result
    summary = await bot.finalize_focus_session(mins, src, interaction.user, is_exam_session=is_exam)
    await interaction.followup.send(summary, ephemeral=True)


bot.tree.add_command(focus_group)


# --- /mcq Command ---

@bot.tree.command(name="mcq", description="Log completed practice MCQs for Bio, Physics, or Chem.")
@app_commands.describe(
    subject="Select the subject (Bio, Physics, or Chem)",
    count="Number of MCQs solved",
)
@app_commands.choices(
    subject=[
        app_commands.Choice(name="Biology", value="Bio"),
        app_commands.Choice(name="Physics", value="Physics"),
        app_commands.Choice(name="Chemistry", value="Chem"),
    ]
)
async def mcq_command(
    interaction: discord.Interaction,
    subject: app_commands.Choice[str],
    count: app_commands.Range[int, 1, 1000],
):
    await interaction.response.defer(ephemeral=True)
    receipt = await bot.process_mcq_log(
        subject=subject.value,
        count=count,
        source="/mcq command",
        actor=interaction.user,
    )
    await interaction.followup.send(f"✅ **MCQs Logged!**\n{receipt}", ephemeral=True)


# --- /topic Group (/topic done) ---

topic_group = app_commands.Group(name="topic", description="Track syllabus topic completions and boss raid progress.")


@topic_group.command(name="done", description="Log a completed Concept or Revision topic to damage the Syllabus Boss.")
@app_commands.describe(
    subject="Subject name (e.g., Biology, Physics, Chemistry)",
    chapter="NEET 2027 chapter name",
    topic="Specific sub-topic or NCERT section mastered",
    mode="Concept/Short Notes (+INT) or Active Revision (+WIS)",
)
@app_commands.autocomplete(chapter=chapter_autocomplete)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Concept (Short Notes / First Mastery -> +INT)", value="Concept"),
        app_commands.Choice(name="Revision (Active Recall / Re-read -> +WIS)", value="Revision"),
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
    receipt = await bot.process_topic_log(
        subject=subject,
        chapter=chapter,
        topic=topic,
        mode=mode.value,
        actor=interaction.user,
    )
    await interaction.followup.send(f"✅ **Topic Cleared!**\n{receipt}", ephemeral=True)


bot.tree.add_command(topic_group)


# --- /test_log Command ---

@bot.tree.command(name="test_log", description="Log a mock/part test score and banish weak topics to #cursed-vault.")
@app_commands.describe(
    name="Name of the test (e.g., Allen Major Test 04, Aakash FTS-2)",
    score="Marks scored",
    max_score="Maximum marks of the test (e.g., 720, 180)",
    wrong_topics="Comma-separated weak/wrong topics (or 'None' if flawless)",
)
async def test_log(
    interaction: discord.Interaction,
    name: str,
    score: int,
    max_score: app_commands.Range[int, 1, 2000],
    wrong_topics: str,
):
    await interaction.response.defer(ephemeral=True)

    prof_now = bot.db.get_profile()
    exam_active = bool(prof_now.get("exam_mode_active"))

    pct = round((score / max_score) * 100, 2)
    xp_gain = max(150, int(pct * 5))
    gold_gain = max(75, int(pct * 2.5))
    wis_gain = 3 if exam_active else 2

    bot.db.record_activity_telemetry(tests_delta=1, execution_delta=1)
    curses_added = bot.db.log_test_and_curses(name, score, max_score, wrong_topics)
    prof, leveled_up = bot.db.award_rewards(
        xp_gain=xp_gain,
        gold_gain=gold_gain,
        d_int=2,
        d_wis=wis_gain,
    )

    guild = interaction.guild
    rank_msg = await bot.sync_hunter_roles(guild) if guild else None
    exam_note = " *(Exam Mode +50% WIS)*" if exam_active else ""

    receipt = (
        f"**Mock Test:** `{name}`\n"
        f"**Score:** `{score} / {max_score}` (**{pct}%**)\n"
        f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+2 INT` • `+{wis_gain} WIS`{exam_note}\n"
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
                curse_embed = discord.Embed(
                    title=f"🩸 NEW CURSES SEALED — {name}",
                    description=(
                        f"{lines}\n\n"
                        "Use `/curse raid` to review or `/curse cleanse id:<ID>` once mastered!"
                    ),
                    color=0x8E44AD,
                    timestamp=now_ist(),
                )
                await vault_ch.send(embed=curse_embed)

    await bot.refresh_all_dashboards()
    await interaction.followup.send(f"✅ **Test Recorded!**\n{receipt}", ephemeral=True)


# --- /curse Group (/curse add, /curse raid, /curse cleanse) ---

curse_group = app_commands.Group(name="curse", description="Mistake Vault & Cursed Raids for weak topics and NCERT traps.")


@curse_group.command(name="add", description="Seal a tricky question, NCERT line, or mistake into #cursed-vault (+WIS).")
@app_commands.describe(
    subject="Subject name (e.g., Biology, Physics, Chemistry)",
    note="Tricky concept, NCERT line, or mistake description",
)
async def curse_add(interaction: discord.Interaction, subject: str, note: str):
    await interaction.response.defer(ephemeral=True)
    curse_id = bot.db.add_curse(subject, note, source_test="Manual Mistake Log")

    prof_now = bot.db.get_profile()
    exam_active = bool(prof_now.get("exam_mode_active"))
    xp_gain = 52 if exam_active else 35
    wis_gain = 2 if exam_active else 1

    prof, leveled_up = bot.db.award_rewards(xp_gain=xp_gain, gold_gain=15, d_wis=wis_gain)

    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id:
            vault_ch = interaction.guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                embed = discord.Embed(
                    title=f"🩸 CURSE SEALED [ID `{curse_id}`] — {normalize_subject_name(subject).upper()}",
                    description=f"**Mistake / NCERT Trap:** `{note}`\n*Cleanse with `/curse cleanse id:{curse_id}`*",
                    color=0x8E44AD,
                    timestamp=now_ist(),
                )
                await vault_ch.send(embed=embed)

    await bot.refresh_all_dashboards()
    exam_tag = " [Exam Mode +50% WIS XP]" if exam_active else ""
    msg = (
        f"🩸 **Mistake Sealed in Cursed Vault (ID `{curse_id}`)**{exam_tag}\n"
        f"**Honesty Bonus:** `+{xp_gain} XP` • `+15 Gold` • `+{wis_gain} WIS`"
    )
    if leveled_up:
        msg += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    await interaction.followup.send(msg, ephemeral=True)


@curse_group.command(name="raid", description="Spawn a Cursed Raid session presenting up to 5 uncleared mistakes.")
async def curse_raid(interaction: discord.Interaction):
    curses = bot.db.get_uncleared_curses(limit=5)
    if not curses:
        await interaction.response.send_message(
            "✨ **Zero active curses!** Your Mistake Vault is completely purified.", ephemeral=True
        )
        return

    lines = [
        f"⚔️ **Target ID `{c['id']}`** `[{c['subject']}]` — **{c['topic_or_note']}**\n"
        f"   *Source:* `{c['source_test']}`"
        for c in curses
    ]
    embed = discord.Embed(
        title=f"👹 CURSED RAID SPAWNED ({len(curses)} TARGETS)",
        description=(
            "Review and solve each target below. As you master each one, execute "
            "`/curse cleanse id:<ID>` to banish it!\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(lines)
        ),
        color=0x8E44AD,
        timestamp=now_ist(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@curse_group.command(name="cleanse", description="Mark a cursed mistake as mastered, boosting WIS and awarding XP + Gold.")
@app_commands.describe(id="Numeric ID of the curse to cleanse")
async def curse_cleanse(interaction: discord.Interaction, id: int):
    await interaction.response.defer(ephemeral=True)
    cleansed = bot.db.cleanse_curse(id)
    if not cleansed:
        await interaction.followup.send(f"❌ No active curse found with ID `{id}`.", ephemeral=True)
        return

    prof_now = bot.db.get_profile()
    exam_active = bool(prof_now.get("exam_mode_active"))
    xp_gain = 120 if exam_active else 80
    wis_gain = 3 if exam_active else 2

    bot.db.record_activity_telemetry(execution_delta=1)
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
    exam_tag = " [Exam Mode +50% WIS XP]" if exam_active else ""
    msg = (
        f"✨ **CURSE CLEANSED [ID `{id}`]!**{exam_tag}\n"
        f"**Mastered:** `{cleansed['topic_or_note']}`\n"
        f"**Rewards:** `+{xp_gain} XP` • `+40 Gold` • `+{wis_gain} WIS`"
    )
    if leveled_up:
        msg += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"

    await bot.post_battle_receipt("✨ CURSED RAID CLEANSE", msg, color=0x1ABC9C)
    await interaction.followup.send(msg, ephemeral=True)


bot.tree.add_command(curse_group)


# --- /shop, /buy, /use, /shield Commands ---

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
@app_commands.describe(item="Item key or name")
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
@app_commands.describe(item="Item key or name to redeem")
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


# --- /stats Command ---

@bot.tree.command(name="stats", description="Refreshes all live dashboards and displays your Hunter Status Window.")
async def stats_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
    await bot.refresh_all_dashboards()
    await interaction.followup.send(embed=build_status_window_embed(bot.db), ephemeral=True)


# --- Global Slash Command Error Handler ---

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return

    logger.error("Command error: %s\n%s", error, traceback.format_exc())
    err_msg = f"⚠️ **System Glitch:** `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(err_msg, ephemeral=True)
    else:
        await interaction.response.send_message(err_msg, ephemeral=True)


# ==============================================================================
# 7. KEEP-ALIVE SERVER (RENDER + UPTIMEROBOT) & ENTRY POINT
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
    """Starts the background HTTP health-check server bound to Render's PORT."""
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Keep-alive HTTP server listening on 0.0.0.0:%s (HEAD & GET ready)", port)


if __name__ == "__main__":
    start_keep_alive_server()
    bot.run(DISCORD_TOKEN)
