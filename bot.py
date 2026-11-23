"""
NEET 2027 Accountability RPG Discord Bot (Single-File Architecture)
==================================================================
A private, single-user gamified study RPG bot built with discord.py (v2.7+)
and SQLite3, tailored for NEET 2027 preparation with a syllabus mastery
deadline of December 15, 2026 (Asia/Kolkata IST).

Architecture Overview (Consolidated into main.py):
1. Configuration & Security:
   - Loads DISCORD_TOKEN, GUILD_ID, and ALLOWED_USER_ID from .env.
   - Enforces a strict global security check so ONLY ALLOWED_USER_ID can
     interact with slash commands, persistent buttons, or voice tracking.
   - All timestamps, daily resets (00:00 IST), and countdowns operate in
     Asia/Kolkata (IST).

2. Database Layer (DatabaseManager):
   - Manages SQLite3 (`study_rpg.db`) with automatic schema initialization.
   - Stores RPG character stats (Level, XP, HP, Gold, Streak, INT, END, DEX, WIS),
     daily quest progress, channel/message IDs for live-updating embeds,
     syllabus topics/bosses, cursed vault error logs, and active focus sessions.

3. Persistent UI Views (QuestBoardView & CursedVaultView):
   - Survives bot restarts via `bot.add_view()`.
   - Provides one-click logging (+50 Bio MCQs, +30 Physics MCQs, +1 Topic Cleared,
     and Toggle Focus Timer) directly on the pinned #quest-board embed.

4. Automated World Builder (/setup_world):
   - Idempotently creates or fetches all 4 private categories, 9 channels
     (8 text + 1 voice), progression roles (E-Rank to S-Rank), and the
     ⚠️ Fatigued debuff role, locked strictly to ALLOWED_USER_ID.
   - Posts, pins, and saves message IDs for live embeds in #quest-board,
     #status-window, and #boss-raids.

5. Core Gameplay Loop, Voice Auto-Tracker & Midnight Tribunal:
   - Voice auto-tracker awards +2 XP, +1 Gold, and +END per minute in #focus-dungeon.
   - Slash commands (/focus, /mcq, /topic, /test_log, /stats, /cursed) update stats,
     log receipts to #battle-logs, track wrong topics in #cursed-vault, and
     refresh pinned dashboards in place.
   - Automated 00:00 IST task evaluates daily targets, applies HP damage &
     ⚠️ Fatigued role on missed quests, updates streaks, and resets daily counters.

Setup Instructions:
1. Install dependencies:
   pip install -U discord.py python-dotenv
2. Create a `.env` file in the same directory as `main.py`:
   DISCORD_TOKEN=your_bot_token_here
   GUILD_ID=your_discord_server_id_here
   ALLOWED_USER_ID=your_discord_user_id_here
3. Enable "Server Members Intent" and "Message Content Intent" in the Discord
   Developer Portal for your bot.
4. Run `python main.py` and execute `/setup_world` in your server.
"""

import os
import math
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("StudyRPG")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID")
ALLOWED_USER_ID_RAW = os.getenv("ALLOWED_USER_ID")

if not DISCORD_TOKEN or not GUILD_ID_RAW or not ALLOWED_USER_ID_RAW:
    raise RuntimeError(
        "Missing required environment variables! Ensure DISCORD_TOKEN, GUILD_ID, "
        "and ALLOWED_USER_ID are defined in your .env file."
    )

GUILD_ID = int(GUILD_ID_RAW)
ALLOWED_USER_ID = int(ALLOWED_USER_ID_RAW)

# Timezone & Target Constants
IST = ZoneInfo("Asia/Kolkata")
TARGET_MASTERY_DATE = date(2026, 12, 15)
TOTAL_SYLLABUS_TOPICS = 180  # Baseline NEET syllabus topics (dynamically scales with bosses)
BOSS_HP_PER_TOPIC = 100      # Each topic in a chapter boss equals 100 Boss HP

# Daily Quest Targets (Reset at 00:00 IST / Audited at 23:55 IST)
DAILY_TARGET_MINUTES = 360   # 6 Hours -> END Stat
DAILY_TARGET_BIO = 300       # 300 Bio MCQs -> DEX Stat
DAILY_TARGET_PHY = 120       # 120 Physics MCQs -> DEX Stat
DAILY_TARGET_TOPICS = 1      # >= 1 Topic Concept/Revision -> INT / WIS Stat

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


def get_rank_for_level(level: int) -> str:
    """Determines the Hunter Rank title for a given character level."""
    current_rank = RANK_TIERS[0][1]
    for req_lvl, rank_name, _ in RANK_TIERS:
        if level >= req_lvl:
            current_rank = rank_name
    return current_rank


# ==============================================================================
# 2. SQLITE DATABASE MANAGER
# ==============================================================================

class DatabaseManager:
    """
    Thread-safe SQLite3 persistence layer for `study_rpg.db`.
    Handles schema creation, player stats, daily quest state, world IDs,
    chapter bosses & topics, curses, shop/inventory, rest shields, and focus timers.
    """

    def __init__(self, db_path: str = "study_rpg.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes all tables, runs safe migrations, and seeds defaults."""
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
                    last_audit_date TEXT
                )
            """)

            # Safe column migrations for existing Prompt 1 databases
            existing_cols = {
                row["name"] for row in cur.execute("PRAGMA table_info(hunter_profile)").fetchall()
            }
            migrations = {
                "daily_gold_earned": "ALTER TABLE hunter_profile ADD COLUMN daily_gold_earned INTEGER DEFAULT 0",
                "active_shield_date": "ALTER TABLE hunter_profile ADD COLUMN active_shield_date TEXT",
                "last_audit_date": "ALTER TABLE hunter_profile ADD COLUMN last_audit_date TEXT",
            }
            for col_name, alter_sql in migrations.items():
                if col_name not in existing_cols:
                    cur.execute(alter_sql)

            # Discord World Config (Channel IDs, Message IDs, Role IDs)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS world_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )
            """)

            # Active Focus Session State (Voice or Manual /focus start)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS active_focus (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    start_time_iso TEXT,
                    source TEXT
                )
            """)

            # 1. Chapter Bosses & Syllabus Topics Tables
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

            # Legacy Topic Logs (Retained for historical ledger compatibility)
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

            # 2. Mistake Vault & Curses Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS curses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    topic_or_note TEXT NOT NULL,
                    source_test TEXT DEFAULT 'Manual Entry',
                    cleared INTEGER DEFAULT 0,
                    created_at TEXT
                )
            """)

            # Migrate any legacy rows from Prompt 1's `cursed_vault` if present
            has_legacy_vault = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cursed_vault'"
            ).fetchone()
            if has_legacy_vault:
                legacy_rows = cur.execute("SELECT * FROM cursed_vault").fetchall()
                if legacy_rows:
                    for lr in legacy_rows:
                        cur.execute(
                            """
                            INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                "General",
                                lr["topic_text"],
                                lr["test_name"],
                                1 if lr["status"] == "PURIFIED" else 0,
                                lr["added_at"],
                            ),
                        )
                    cur.execute("DROP TABLE cursed_vault")

            # 3. Shop Items, Inventory & Shield Usage History
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
            cur.execute("SELECT user_id FROM hunter_profile WHERE user_id = ?", (ALLOWED_USER_ID,))
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO hunter_profile (user_id, last_reset_date)
                    VALUES (?, ?)
                    """,
                    (ALLOWED_USER_ID, today_ist_str()),
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
        Returns `(updated_profile, leveled_up)`.
        """
        prof = self.get_profile()
        new_xp = prof["xp"] + max(0, xp_gain)
        new_level = prof["level"]
        leveled_up = False

        # Process Level Ups
        while new_xp >= xp_for_next_level(new_level):
            new_xp -= xp_for_next_level(new_level)
            new_level += 1
            leveled_up = True

        new_max_hp = 100 + (new_level - 1) * 10
        new_hp = min(new_max_hp, prof["hp"] + heal_hp + (25 if leveled_up else 0))

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
            conn.commit()

        return self.get_profile(), leveled_up

    # --- Focus Session Management ---

    def get_active_focus(self) -> Optional[Dict[str, Any]]:
        """Returns the active focus session row if a timer is running, else None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT start_time_iso, source FROM active_focus WHERE id = 1"
            ).fetchone()
            return dict(row) if row and row["start_time_iso"] else None

    def start_focus(self, source: str = "Manual") -> Tuple[bool, str]:
        """Starts a new focus session if none is currently running."""
        active = self.get_active_focus()
        if active:
            return False, active["start_time_iso"]

        iso_str = now_ist().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO active_focus (id, start_time_iso, source)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    start_time_iso = excluded.start_time_iso,
                    source = excluded.source
                """,
                (iso_str, source),
            )
            conn.commit()
        return True, iso_str

    def stop_focus(self) -> Optional[Tuple[int, str]]:
        """Stops the active focus session and returns `(elapsed_minutes, source)`."""
        active = self.get_active_focus()
        if not active:
            return None

        start_dt = datetime.fromisoformat(active["start_time_iso"])
        elapsed_mins = max(0, int((now_ist() - start_dt).total_seconds() // 60))

        with self._connect() as conn:
            conn.execute("DELETE FROM active_focus WHERE id = 1")
            conn.commit()

        return elapsed_mins, active["source"]

    # --- 1. Chapter Boss Raids & Topics ---

    def add_boss(self, subject: str, chapter: str, total_topics: int) -> Dict[str, Any]:
        """Creates or updates a Chapter Boss in the `bosses` table."""
        subj = subject.strip()
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
        """Returns `(total_completed_topics, total_target_topics)` across bosses & baseline."""
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
        """
        Logs the topic into `topics` & `topic_logs`, finds or auto-links the matching
        Chapter Boss, deals 1 topic (100 HP) of damage, and returns boss battle telemetry.
        """
        subj = subject.strip()
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

            # Locate matching boss (case-insensitive)
            boss = conn.execute(
                "SELECT * FROM bosses WHERE LOWER(subject) = LOWER(?) AND LOWER(chapter) = LOWER(?)",
                (subj, chap),
            ).fetchone()

            if not boss:
                # Auto-create a Chapter Boss with 5 default topics if not pre-registered via /boss add
                cur = conn.execute(
                    """
                    INSERT INTO bosses (subject, chapter, total_topics, completed_topics, status, created_at)
                    VALUES (?, ?, 5, 0, 'ACTIVE', ?)
                    """,
                    (subj, chap, now_ist().isoformat()),
                )
                boss = conn.execute("SELECT * FROM bosses WHERE id = ?", (cur.lastrowid,)).fetchone()

            boss_id = boss["id"]

            # Upsert topic record under this boss
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

            # Deal damage to boss HP (if topic newly cleared or boss still has HP remaining)
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

    # --- 2. Mistake Vault & Curses ---

    def add_curse(self, subject: str, note: str, source_test: str = "Manual Entry") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO curses (subject, topic_or_note, source_test, cleared, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (subject.strip(), note.strip(), source_test.strip(), now_ist().isoformat()),
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

    def cleanse_curse(self, curse_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM curses WHERE id = ? AND cleared = 0", (curse_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE curses SET cleared = 1 WHERE id = ?", (curse_id,))
            conn.commit()
            return dict(row)

    # --- 3. Shop, Inventory & Rest Shields ---

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
        """Activates a Rest / Post-Test Shield for today (max 2 per rolling 7-day window)."""
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

    # --- 4. Ruthless 23:55 IST Midnight Audit & Daily Reset ---

    def run_midnight_audit(self) -> Optional[Dict[str, Any]]:
        """
        Executes the ruthless 23:55 IST daily accountability audit (idempotent per date):
        - Checks 360 mins, 300 Bio MCQs, 120 Physics MCQs, >= 1 Topic.
        - If all met OR active Rest Shield covers today:
          increments streak, awards 2x Gold daily clear bonus, clears Fatigue.
        - If any missed without shield:
          resets streak to 0, deducts 15 HP per missed target; if HP <= 0, drops level by 1
          (min 1), halves gold, sets HP to 50, and applies ⚠️ Fatigued role.
        - Resets daily quest counters after auditing.
        """
        prof = self.get_profile()
        today_str = today_ist_str()

        # Idempotency guard so bot restarts near 23:55 never double-punish or double-reward
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

        if all_passed or shield_active:
            new_streak = prof["streak"] + 1
            # 2x Gold daily clear bonus (doubles today's earned gold, minimum +100 G)
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
                new_hp = 50  # Revived at 50 HP after punishment
                new_gold = new_gold // 2

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
        }


# ==============================================================================
# 3. EMBED BUILDERS (LIVE DASHBOARDS)
# ==============================================================================

def build_quest_board_embed(db: DatabaseManager) -> discord.Embed:
    """Constructs the live-updating Daily Quest Board embed."""
    prof = db.get_profile()
    active_focus = db.get_active_focus()
    now = now_ist()

    # Calculate time left until 23:55 IST ruthless audit
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

    shield_active = prof.get("active_shield_date") == today_ist_str()
    shield_line = (
        "🛡️ **REST / POST-TEST SHIELD ACTIVE** — Today's targets waived & streak protected!\n"
        if shield_active
        else ""
    )

    focus_banner = "🟢 **IDLE** — Click `Toggle Focus` or join `#focus-dungeon`"
    if active_focus:
        start_dt = datetime.fromisoformat(active_focus["start_time_iso"])
        elapsed = int((now - start_dt).total_seconds() // 60)
        focus_banner = (
            f"🔥 **ACTIVE SESSION ({active_focus['source'].upper()})** — "
            f"Running for `{elapsed} mins` (Started {start_dt.strftime('%I:%M %p IST')})"
        )

    embed = discord.Embed(
        title="⚔️ DAILY HUNTER QUESTS — NEET 2027",
        description=(
            f"**Date (IST):** `{now.strftime('%d %b %Y')}` • **23:55 Audit In:** `{rem_hrs}h {rem_mins}m`\n"
            f"{shield_line}"
            f"**Focus Status:** {focus_banner}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x3498DB if shield_active else (0xE74C3C if prof["is_fatigued"] else 0x2B2D31),
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

    inv = db.get_inventory()
    inv_str = ", ".join(f"{i['name']} `x{i['quantity']}`" for i in inv) if inv else "Empty"

    embed = discord.Embed(
        title="📊 HUNTER STATUS WINDOW",
        description=(
            f"**Class:** `NEET 2027 Sovereign Aspirant`\n"
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
    embed.add_field(name="🎒 Inventory Pouch", value=inv_str, inline=False)

    embed.set_footer(text="Stats scale automatically via Focus Sessions, MCQs, Topic Clears & Mock Tests.")
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

    embed.set_footer(text="Use /boss add to spawn chapter bosses and /topic done to deal direct HP damage.")
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
# 4. PERSISTENT DISCORD UI VIEWS (BUTTONS)
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
        await interaction.followup.send("✅ Logged **+50 Biology MCQs** (+50 XP, +25 Gold, +2 DEX)!", ephemeral=True)

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
        await interaction.followup.send("✅ Logged **+30 Physics MCQs** (+60 XP, +30 Gold, +2 DEX)!", ephemeral=True)

    @discord.ui.button(
        label="+1 Topic Cleared",
        style=discord.ButtonStyle.secondary,
        emoji="📘",
        custom_id="rpg_quest_topic_1",
    )
    async def quick_topic_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_topic_log(
            subject="General",
            chapter="Quick Syllabus Block",
            topic=f"Quick Clear ({now_ist().strftime('%H:%M IST')})",
            mode="Revision",
            actor=interaction.user,
        )
        await interaction.followup.send("✅ Logged **+1 Topic Cleared** (+100 XP, +50 Gold, +2 WIS)!", ephemeral=True)

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
            _, iso_str = self.bot.db.start_focus(source="Quest Board Button")
            await self.bot.refresh_all_dashboards()
            await interaction.followup.send(
                f"🔥 **Focus Session Started** at `{datetime.fromisoformat(iso_str).strftime('%I:%M %p IST')}`!\n"
                "Click **Toggle Focus** again when you finish to claim your XP, Gold & END stats.",
                ephemeral=True,
            )
        else:
            result = self.bot.db.stop_focus()
            if result:
                mins, src = result
                summary = await self.bot.finalize_focus_session(mins, src, interaction.user)
                await interaction.followup.send(summary, ephemeral=True)


# ==============================================================================
# 5. CORE RPG BOT ENGINE & AUTOMATED WORLD BUILDER
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
        # Register persistent view so buttons work across bot restarts
        self.add_view(QuestBoardView(self))

        # Global interaction check for all slash commands
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

        # Sync slash commands directly to the target guild for instant availability
        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)
        await self.tree.sync(guild=guild_obj)
        logger.info("Slash commands synced to Guild ID %s", GUILD_ID)

        # Start scheduled tasks
        if not self.midnight_tribunal_task.is_running():
            self.midnight_tribunal_task.start()
        if not self.dashboard_heartbeat_task.is_running():
            self.dashboard_heartbeat_task.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s) | IST Time: %s", self.user, self.user.id, now_ist().strftime("%Y-%m-%d %H:%M:%S"))

    # --- Role & Discord Synchronization ---

    async def sync_hunter_roles(self, guild: discord.Guild) -> Optional[str]:
        """
        Ensures the user's Discord roles match their current RPG Level Rank
        and applies/removes the ⚠️ Fatigued role based on database state.
        Returns a string announcement if a rank-up occurred.
        """
        member = guild.get_member(ALLOWED_USER_ID)
        if not member:
            return None

        prof = self.db.get_profile()
        target_rank_name = get_rank_for_level(prof["level"])
        rank_names = {r[1] for r in RANK_TIERS}

        roles_to_add = []
        roles_to_remove = []
        rank_up_announcement = None

        # Sync Progression Rank Role
        target_role = discord.utils.get(guild.roles, name=target_rank_name)
        if target_role and target_role not in member.roles:
            roles_to_add.append(target_role)
            rank_up_announcement = f"🎊 **RANK ASCENSION!** You have ascended to **{target_rank_name}**!"

        for r in member.roles:
            if r.name in rank_names and r.name != target_rank_name:
                roles_to_remove.append(r)

        # Sync ⚠️ Fatigued Debuff Role
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

    # --- Live Dashboard Updaters ---

    async def refresh_all_dashboards(self) -> None:
        """Edits the pinned embeds in #quest-board, #status-window, and #boss-raids in place."""
        guild = self.get_guild(GUILD_ID)
        if not guild:
            return

        # 1. Refresh #quest-board
        qb_ch_id = self.db.get_config("ch_quest-board")
        qb_msg_id = self.db.get_config("msg_quest-board")
        if qb_ch_id and qb_msg_id:
            ch = guild.get_channel(qb_ch_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(qb_msg_id)
                    await msg.edit(embed=build_quest_board_embed(self.db), view=QuestBoardView(self))
                except discord.NotFound:
                    logger.warning("Pinned quest-board message not found. Re-run /setup_world to restore.")

        # 2. Refresh #status-window
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

        # 3. Refresh #boss-raids
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

    async def post_battle_receipt(self, title: str, details: str, color: int = 0x2ECC71) -> None:
        """Logs a formatted combat/study receipt into #battle-logs."""
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
        self, minutes: int, source: str, actor: discord.abc.User
    ) -> str:
        """Awards XP, Gold, and END for completed focus minutes and updates embeds."""
        if minutes < 1:
            await self.refresh_all_dashboards()
            return "⚠️ Focus session ended under **1 minute** — no XP or Gold awarded."

        xp_gain = minutes * 2
        gold_gain = minutes * 1
        end_gain = max(1, minutes // 30)  # +1 END per 30 mins focused (minimum +1)

        prof, leveled_up = self.db.award_rewards(
            xp_gain=xp_gain,
            gold_gain=gold_gain,
            d_end=end_gain,
            d_minutes=minutes,
        )

        guild = self.get_guild(GUILD_ID)
        rank_msg = await self.sync_hunter_roles(guild) if guild else None

        receipt = (
            f"**Source:** `{source}`\n"
            f"**Duration:** `{minutes} minutes`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{end_gain} END`\n"
            f"**Daily Study Progress:** `{prof['daily_minutes']} / {DAILY_TARGET_MINUTES} mins`"
        )
        if leveled_up:
            receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
        if rank_msg:
            receipt += f"\n{rank_msg}"

        await self.post_battle_receipt("⏱️ FOCUS DUNGEON CLEARED", receipt, color=0x3498DB)
        await self.refresh_all_dashboards()
        return f"✅ **Focus Session Logged ({minutes} mins):** `+{xp_gain} XP`, `+{gold_gain} Gold`, `+{end_gain} END`."

    async def process_mcq_log(
        self, subject: str, count: int, source: str, actor: discord.abc.User
    ) -> str:
        """Processes Biology, Physics, or Chemistry MCQ completions."""
        subj_norm = subject.strip().capitalize()
        if subj_norm == "Bio":
            xp_per_q = 1
            d_bio, d_phy, d_chem = count, 0, 0
            label = "Biology"
        elif subj_norm == "Physics":
            xp_per_q = 2  # Physics calculations yield double XP
            d_bio, d_phy, d_chem = 0, count, 0
            label = "Physics"
        else:
            xp_per_q = 1.5
            d_bio, d_phy, d_chem = 0, 0, count
            label = "Chemistry"

        xp_gain = int(round(count * xp_per_q))
        gold_gain = max(1, count // 2)
        dex_gain = max(1, count // 25)

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

        receipt = (
            f"**Subject:** `{label}` ({source})\n"
            f"**Solved:** `+{count} MCQs`\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+{dex_gain} DEX`\n"
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
        """Processes Concept/Revision topic completions and damages the Chapter Boss."""
        boss_telemetry = self.db.record_topic_and_damage_boss(subject, chapter, topic, mode)

        is_concept = mode.lower() == "concept"
        xp_gain = 120 if is_concept else 100
        gold_gain = 60 if is_concept else 50
        d_int = 2 if is_concept else 0
        d_wis = 0 if is_concept else 2
        stat_label = "+2 INT (Concept Mastery)" if is_concept else "+2 WIS (Active Revision)"

        # Bonus bounty if the Chapter Boss was just defeated!
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

        receipt = (
            f"**Subject:** `{subject}` • **Chapter:** `{chapter}`\n"
            f"**Topic:** `{topic}` (`{mode}`)\n"
            f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `{stat_label}`"
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

        # Also log permanent entry in #codex-topics
        if guild:
            codex_id = self.db.get_config("ch_codex-topics")
            if codex_id:
                codex_ch = guild.get_channel(codex_id)
                if isinstance(codex_ch, discord.TextChannel):
                    await codex_ch.send(
                        f"📗 `[{now_ist().strftime('%d %b %Y')}]` **{subject}** › *{chapter}* › **{topic}** `[{mode}]`"
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
        """Automatically starts/stops focus tracking when ALLOWED_USER_ID enters/leaves #focus-dungeon."""
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
            result = self.db.stop_focus()
            if result:
                mins, src = result
                await self.finalize_focus_session(mins, src, member)

    # --- Scheduled Tasks (23:55 IST Ruthless Midnight Audit & Heartbeat) ---

    @tasks.loop(time=time(hour=23, minute=55, second=0, tzinfo=IST))
    async def midnight_tribunal_task(self) -> None:
        """Executes the ruthless daily accountability audit at 23:55 IST."""
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
                        desc = (
                            f"💀 **VERDICT: UNEXCUSED FAILURE (NO DAILY CLASSES EXCUSE)**\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Missed Quests (`-15 HP` each):**\n{missed_block}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• **HP Penalty:** `{verdict['hp_delta']} HP` (`{verdict['new_hp']}/{verdict['max_hp']} HP`)\n"
                            f"• **Streak Obliterated:** `0 Days`\n"
                            f"• **Status Debuff:** `⚠️ Fatigued`"
                            f"{catastrophe_lines}"
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

    @tasks.loop(minutes=15)
    async def dashboard_heartbeat_task(self) -> None:
        """Keeps the live focus timer & countdown timers fresh every 15 minutes."""
        await self.refresh_all_dashboards()

    @dashboard_heartbeat_task.before_loop
    @midnight_tribunal_task.before_loop
    async def before_tasks(self) -> None:
        await self.wait_until_ready()


bot = StudyRPGBot()


# ==============================================================================
# 6. SLASH COMMANDS (APP COMMANDS)
# ==============================================================================

@bot.tree.command(
    name="setup_world",
    description="Automatically builds/repairs all private RPG categories, channels, roles, and live embeds.",
)
async def setup_world(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Must be run inside a server.", ephemeral=True)
        return

    owner_member = guild.get_member(ALLOWED_USER_ID)
    if not owner_member:
        await interaction.followup.send(
            f"❌ Could not find `ALLOWED_USER_ID` (`{ALLOWED_USER_ID}`) in this server.",
            ephemeral=True,
        )
        return

    # 1. Create or Fetch Progression Roles & ⚠️ Fatigued Role
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

    # Private Permission Overwrites: Deny @everyone, Allow Owner & Bot
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

    # 2. Create or Fetch Categories & Channels
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

    # 3. Post & Pin Live-Updating Embeds (or verify existing ones)
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

    # Seed helpful welcome info in #inventory-and-shop if empty
    shop_ch = created_channels.get("inventory-and-shop")
    if isinstance(shop_ch, discord.TextChannel):
        history = [m async for m in shop_ch.history(limit=1)]
        if not history:
            await shop_ch.send(embed=build_shop_embed(bot.db))

    # Sync Hunter roles
    await bot.sync_hunter_roles(guild)

    await interaction.followup.send(
        "✅ **World Construction Complete!**\n"
        "• Created/verified all 4 private categories, 9 channels, and progression roles.\n"
        "• Pinned live dashboards in `#quest-board`, `#status-window`, and `#boss-raids`.\n"
        "• Head to `#quest-board` or join `#focus-dungeon` to begin!",
        ephemeral=True,
    )


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
        f"**Boss HP:** `{cur_hp} / {max_hp} HP` (`{boss['total_topics']} topics`)\n"
        "Use `/topic done` with matching subject & chapter to deal damage!",
        ephemeral=True,
    )


@boss_group.command(name="list", description="View all active and defeated Chapter Bosses with HP bars.")
async def boss_list(interaction: discord.Interaction):
    bosses = bot.db.get_all_bosses()
    if not bosses:
        await interaction.response.send_message(
            "📭 No Chapter Bosses spawned yet. Use `/boss add` to summon one!", ephemeral=True
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
    started, iso_str = bot.db.start_focus(source="Manual /focus")
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
        "Use `/focus stop` (or click **Toggle Focus** on `#quest-board`) when done.",
        ephemeral=True,
    )


@focus_group.command(name="stop", description="Stops the active focus session and claims XP, Gold & END.")
async def focus_stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = bot.db.stop_focus()
    if not result:
        await interaction.followup.send("⚠️ No active focus session is currently running.", ephemeral=True)
        return

    mins, src = result
    summary = await bot.finalize_focus_session(mins, src, interaction.user)
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
    subject="Subject name (e.g., Biology, Physics, Organic Chem)",
    chapter="Chapter name (e.g., Rotational Motion, Molecular Basis of Inheritance)",
    topic="Specific sub-topic or NCERT section mastered",
    mode="Concept/Short Notes (+INT) or Active Revision (+WIS)",
)
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

    pct = round((score / max_score) * 100, 2)
    xp_gain = max(150, int(pct * 5))
    gold_gain = max(75, int(pct * 2.5))

    curses_added = bot.db.log_test_and_curses(name, score, max_score, wrong_topics)
    prof, leveled_up = bot.db.award_rewards(
        xp_gain=xp_gain,
        gold_gain=gold_gain,
        d_int=2,
        d_wis=2,
    )

    guild = interaction.guild
    rank_msg = await bot.sync_hunter_roles(guild) if guild else None

    receipt = (
        f"**Mock Test:** `{name}`\n"
        f"**Score:** `{score} / {max_score}` (**{pct}%**)\n"
        f"**Rewards:** `+{xp_gain} XP` • `+{gold_gain} Gold` • `+2 INT` • `+2 WIS`\n"
        f"**Cursed Topics Banished:** `{len(curses_added)}`"
    )
    if leveled_up:
        receipt += f"\n🎉 **LEVEL UP!** Reached **Level {prof['level']}**!"
    if rank_msg:
        receipt += f"\n{rank_msg}"

    await bot.post_battle_receipt("📝 MOCK TEST BATTLE REPORT", receipt, color=0xF1C40F)

    # Post cursed topics to #cursed-vault
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
    prof, leveled_up = bot.db.award_rewards(xp_gain=35, gold_gain=15, d_wis=1)

    if interaction.guild:
        await bot.sync_hunter_roles(interaction.guild)
        vault_id = bot.db.get_config("ch_cursed-vault")
        if vault_id:
            vault_ch = interaction.guild.get_channel(vault_id)
            if isinstance(vault_ch, discord.TextChannel):
                embed = discord.Embed(
                    title=f"🩸 CURSE SEALED [ID `{curse_id}`] — {subject.upper()}",
                    description=f"**Mistake / NCERT Trap:** `{note}`\n*Cleanse with `/curse cleanse id:{curse_id}`*",
                    color=0x8E44AD,
                    timestamp=now_ist(),
                )
                await vault_ch.send(embed=embed)

    await bot.refresh_all_dashboards()
    msg = (
        f"🩸 **Mistake Sealed in Cursed Vault (ID `{curse_id}`)**\n"
        f"**Honesty Bonus:** `+35 XP` • `+15 Gold` • `+1 WIS`"
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
            "`/curse cleanse id:<ID>` to banish it and claim `+80 XP`, `+40 Gold`, and `+2 WIS`!\n"
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

    prof, leveled_up = bot.db.award_rewards(xp_gain=80, gold_gain=40, d_wis=2)
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
        f"**Rewards:** `+80 XP` • `+40 Gold` • `+2 WIS`"
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
@app_commands.describe(item="Item key or name (e.g., rest_shield, anime_episode, cheat_meal, break_block)")
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
        return  # Already handled gracefully by global_owner_check

    logger.error("Command error: %s\n%s", error, traceback.format_exc())
    err_msg = f"⚠️ **System Glitch:** `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(err_msg, ephemeral=True)
    else:
        await interaction.response.send_message(err_msg, ephemeral=True)


# ==============================================================================
# 7. ENTRY POINT
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
        # Suppress noisy HTTP access logs in Render console
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
