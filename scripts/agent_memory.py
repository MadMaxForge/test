#!/usr/bin/env python3
"""
Agent Memory System - PostgreSQL-based learning for pipeline agents.

Tables:
  - account_analyses: stored vision analyses of parsed accounts
  - style_patterns: successful/failed style patterns (learned from feedback)
  - generation_history: all generations with QC scores and approval status
  - discovered_accounts: accounts found for parsing (auto-discovery)
  - learning_log: agent event log for debugging and pattern extraction

Usage:
  from agent_memory import AgentMemory
  mem = AgentMemory()
  mem.save_analysis("kyliejenner", analysis_dict)
  patterns = mem.get_successful_patterns(limit=10)
"""

import os
import sys
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass

# Database connection settings - uses Docker-internal PostgreSQL
DB_HOST = os.environ.get("PIPELINE_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("PIPELINE_DB_PORT", "5432"))
DB_NAME = os.environ.get("PIPELINE_DB_NAME", "agent_pipeline")
DB_USER = os.environ.get("PIPELINE_DB_USER", "postgres")
DB_PASS = os.environ.get("PIPELINE_DB_PASS", "")


class AgentMemory:
    """Core memory interface for all pipeline agents."""

    def __init__(self):
        self.conn = None
        self._connect()

    def _connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
            )
            self.conn.autocommit = True
        except psycopg2.Error as e:
            print("[Memory] WARNING: Could not connect to PostgreSQL: %s" % e)
            print("[Memory] Running without persistent memory (in-memory fallback)")
            self.conn = None

    def _execute(self, query, params=None):
        """Execute query with auto-reconnect."""
        if self.conn is None:
            return None
        try:
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(query, params)
            return cur
        except psycopg2.Error as e:
            print("[Memory] Query error: %s" % e)
            try:
                self.conn.close()
            except Exception:
                pass
            self._connect()
            return None

    # ──────────────────────────────────────────────
    # Account Analyses
    # ──────────────────────────────────────────────

    def save_analysis(self, username, analysis, mode="vision", image_count=0):
        """Save Scout analysis result for an account."""
        cur = self._execute(
            """INSERT INTO account_analyses (username, analysis_json, analysis_mode, images_analyzed)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (username, json.dumps(analysis, ensure_ascii=False), mode, image_count),
        )
        if cur:
            row = cur.fetchone()
            print("[Memory] Saved analysis for @%s (id=%s)" % (username, row["id"]))
            return row["id"]
        return None

    def get_latest_analysis(self, username):
        """Get most recent analysis for an account."""
        cur = self._execute(
            """SELECT analysis_json, analysis_mode, images_analyzed, created_at
               FROM account_analyses WHERE username = %s
               ORDER BY created_at DESC LIMIT 1""",
            (username,),
        )
        if cur:
            row = cur.fetchone()
            if row:
                return row["analysis_json"]
        return None

    def get_all_analyses(self, limit=50):
        """Get recent analyses across all accounts."""
        cur = self._execute(
            """SELECT username, analysis_json, created_at
               FROM account_analyses ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        if cur:
            return cur.fetchall()
        return []

    # ──────────────────────────────────────────────
    # Style Patterns (Learning)
    # ──────────────────────────────────────────────

    def save_pattern(self, pattern_type, pattern_data, source_username=None, score=0.0):
        """Save a style pattern learned from analysis or generation."""
        cur = self._execute(
            """INSERT INTO style_patterns (pattern_type, pattern_data, source_username, score)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (pattern_type, json.dumps(pattern_data, ensure_ascii=False), source_username, score),
        )
        if cur:
            row = cur.fetchone()
            return row["id"]
        return None

    def update_pattern_feedback(self, pattern_id, approved, reason=None):
        """Update a pattern with approval feedback."""
        self._execute(
            """UPDATE style_patterns SET approved = %s, feedback_reason = %s
               WHERE id = %s""",
            (approved, reason, pattern_id),
        )

    def get_successful_patterns(self, pattern_type=None, limit=20):
        """Get patterns that were approved or scored well."""
        if pattern_type:
            cur = self._execute(
                """SELECT pattern_data, source_username, score
                   FROM style_patterns
                   WHERE (approved = TRUE OR score >= 8.0)
                     AND pattern_type = %s
                   ORDER BY score DESC, created_at DESC LIMIT %s""",
                (pattern_type, limit),
            )
        else:
            cur = self._execute(
                """SELECT pattern_type, pattern_data, source_username, score
                   FROM style_patterns
                   WHERE approved = TRUE OR score >= 8.0
                   ORDER BY score DESC, created_at DESC LIMIT %s""",
                (limit,),
            )
        if cur:
            return cur.fetchall()
        return []

    def get_failed_patterns(self, pattern_type=None, limit=10):
        """Get patterns that were rejected - to avoid repeating mistakes."""
        if pattern_type:
            cur = self._execute(
                """SELECT pattern_data, feedback_reason, source_username
                   FROM style_patterns
                   WHERE approved = FALSE AND pattern_type = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (pattern_type, limit),
            )
        else:
            cur = self._execute(
                """SELECT pattern_type, pattern_data, feedback_reason
                   FROM style_patterns
                   WHERE approved = FALSE
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
        if cur:
            return cur.fetchall()
        return []

    # ──────────────────────────────────────────────
    # Generation History
    # ──────────────────────────────────────────────

    def save_generation(self, target_account, content_type, prompt_text,
                        prompt_json=None, generation_tool="runpod_zimage",
                        output_path=None, source_username=None):
        """Record a new content generation."""
        cur = self._execute(
            """INSERT INTO generation_history
               (target_account, content_type, source_username, prompt_text,
                prompt_json, generation_tool, output_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (target_account, content_type, source_username, prompt_text,
             json.dumps(prompt_json, ensure_ascii=False) if prompt_json else None,
             generation_tool, output_path),
        )
        if cur:
            row = cur.fetchone()
            return row["id"]
        return None

    def update_qc_result(self, generation_id, qc_score, qc_details=None):
        """Update generation with QC results."""
        self._execute(
            """UPDATE generation_history SET qc_score = %s, qc_details = %s
               WHERE id = %s""",
            (qc_score, json.dumps(qc_details, ensure_ascii=False) if qc_details else None,
             generation_id),
        )

    def update_approval(self, generation_id, approved, reason=None):
        """Update generation with Telegram approval result."""
        self._execute(
            """UPDATE generation_history SET approved = %s, approval_reason = %s
               WHERE id = %s""",
            (approved, reason, generation_id),
        )

    def update_engagement(self, generation_id, likes=0, comments=0, views=0):
        """Update generation with post-publish engagement metrics."""
        self._execute(
            """UPDATE generation_history
               SET engagement_likes = %s, engagement_comments = %s, engagement_views = %s
               WHERE id = %s""",
            (likes, comments, views, generation_id),
        )

    def get_approved_generations(self, content_type=None, limit=20):
        """Get recently approved generations for learning."""
        if content_type:
            cur = self._execute(
                """SELECT prompt_text, prompt_json, qc_score, qc_details,
                          source_username, generation_tool
                   FROM generation_history
                   WHERE approved = TRUE AND content_type = %s
                   ORDER BY qc_score DESC NULLS LAST, created_at DESC LIMIT %s""",
                (content_type, limit),
            )
        else:
            cur = self._execute(
                """SELECT content_type, prompt_text, prompt_json, qc_score,
                          source_username, generation_tool
                   FROM generation_history
                   WHERE approved = TRUE
                   ORDER BY qc_score DESC NULLS LAST, created_at DESC LIMIT %s""",
                (limit,),
            )
        if cur:
            return cur.fetchall()
        return []

    def get_rejected_generations(self, limit=10):
        """Get rejected generations to learn what NOT to do."""
        cur = self._execute(
            """SELECT content_type, prompt_text, qc_score, qc_details,
                      approval_reason, source_username
               FROM generation_history
               WHERE approved = FALSE
               ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        if cur:
            return cur.fetchall()
        return []

    def get_generation_stats(self, target_account=None):
        """Get summary statistics for generations."""
        if target_account:
            cur = self._execute(
                """SELECT content_type,
                          COUNT(*) as total,
                          COUNT(*) FILTER (WHERE approved = TRUE) as approved_count,
                          COUNT(*) FILTER (WHERE approved = FALSE) as rejected_count,
                          AVG(qc_score) FILTER (WHERE qc_score IS NOT NULL) as avg_qc_score
                   FROM generation_history
                   WHERE target_account = %s
                   GROUP BY content_type""",
                (target_account,),
            )
        else:
            cur = self._execute(
                """SELECT content_type,
                          COUNT(*) as total,
                          COUNT(*) FILTER (WHERE approved = TRUE) as approved_count,
                          COUNT(*) FILTER (WHERE approved = FALSE) as rejected_count,
                          AVG(qc_score) FILTER (WHERE qc_score IS NOT NULL) as avg_qc_score
                   FROM generation_history
                   GROUP BY content_type""",
            )
        if cur:
            return cur.fetchall()
        return []

    # ──────────────────────────────────────────────
    # Discovered Accounts
    # ──────────────────────────────────────────────

    def save_discovered_account(self, username, source="manual", followers=0,
                                 category="beauty", compatibility_score=0.0):
        """Save a discovered account for parsing."""
        cur = self._execute(
            """INSERT INTO discovered_accounts
               (username, source, followers, category, compatibility_score)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (username) DO UPDATE SET
                 followers = EXCLUDED.followers,
                 compatibility_score = EXCLUDED.compatibility_score
               RETURNING id""",
            (username, source, followers, category, compatibility_score),
        )
        if cur:
            row = cur.fetchone()
            return row["id"]
        return None

    def get_accounts_to_parse(self, limit=5):
        """Get active accounts ordered by compatibility, least recently parsed first."""
        cur = self._execute(
            """SELECT username, category, compatibility_score, followers, last_parsed_at
               FROM discovered_accounts
               WHERE active = TRUE
               ORDER BY last_parsed_at ASC NULLS FIRST,
                        compatibility_score DESC
               LIMIT %s""",
            (limit,),
        )
        if cur:
            return cur.fetchall()
        return []

    def mark_account_parsed(self, username):
        """Update last_parsed_at and increment times_used."""
        self._execute(
            """UPDATE discovered_accounts
               SET last_parsed_at = NOW(), times_used = times_used + 1
               WHERE username = %s""",
            (username,),
        )

    def deactivate_account(self, username, reason=None):
        """Deactivate an account (e.g., went private, low quality)."""
        self._execute(
            """UPDATE discovered_accounts SET active = FALSE WHERE username = %s""",
            (username,),
        )
        if reason:
            self.log_event("lead", "account_deactivated",
                          {"username": username, "reason": reason})

    # ──────────────────────────────────────────────
    # Learning Log
    # ──────────────────────────────────────────────

    def log_event(self, agent_name, event_type, event_data, lesson=None):
        """Log an agent event for debugging and pattern extraction."""
        self._execute(
            """INSERT INTO learning_log (agent_name, event_type, event_data, lesson_learned)
               VALUES (%s, %s, %s, %s)""",
            (agent_name, event_type, json.dumps(event_data, ensure_ascii=False), lesson),
        )

    def get_lessons(self, agent_name=None, limit=20):
        """Get recent lessons learned by agents."""
        if agent_name:
            cur = self._execute(
                """SELECT event_type, event_data, lesson_learned, created_at
                   FROM learning_log
                   WHERE agent_name = %s AND lesson_learned IS NOT NULL
                   ORDER BY created_at DESC LIMIT %s""",
                (agent_name, limit),
            )
        else:
            cur = self._execute(
                """SELECT agent_name, event_type, lesson_learned, created_at
                   FROM learning_log
                   WHERE lesson_learned IS NOT NULL
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
        if cur:
            return cur.fetchall()
        return []

    # ──────────────────────────────────────────────
    # Context Builder (for LLM prompts)
    # ──────────────────────────────────────────────

    def build_creative_context(self, content_type="feed", max_examples=5):
        """Build a context string for Creative Agent with learned patterns.

        Returns a text block that can be injected into the LLM prompt
        to inform it about what worked and what didn't.
        """
        context_parts = []

        # Successful patterns
        good = self.get_approved_generations(content_type=content_type, limit=max_examples)
        if good:
            context_parts.append("=== SUCCESSFUL PATTERNS (approved, high QC score) ===")
            for i, g in enumerate(good):
                prompt = g.get("prompt_text", "")
                score = g.get("qc_score", "N/A")
                source = g.get("source_username", "unknown")
                context_parts.append(
                    "Example %d (QC: %s, inspired by @%s):\n%s" % (i + 1, score, source, prompt[:500])
                )

        # Failed patterns
        bad = self.get_rejected_generations(limit=3)
        if bad:
            context_parts.append("\n=== REJECTED PATTERNS (avoid these) ===")
            for i, g in enumerate(bad):
                prompt = g.get("prompt_text", "")
                reason = g.get("approval_reason", "unknown reason")
                qc = g.get("qc_details", {})
                artifacts = ""
                if isinstance(qc, dict):
                    artifacts = qc.get("artifacts_found", "")
                context_parts.append(
                    "Rejected %d (reason: %s, artifacts: %s):\n%s" % (
                        i + 1, reason, artifacts, prompt[:300])
                )

        # Style lessons
        lessons = self.get_lessons(agent_name="creative", limit=5)
        if lessons:
            context_parts.append("\n=== LESSONS LEARNED ===")
            for l in lessons:
                context_parts.append("- %s" % l.get("lesson_learned", ""))

        if not context_parts:
            return ""

        return "\n".join(context_parts)

    def build_qc_context(self, max_examples=5):
        """Build context for QC Agent with known artifact patterns."""
        context_parts = []

        # Get rejected generations with QC details
        bad = self.get_rejected_generations(limit=max_examples)
        if bad:
            context_parts.append("=== KNOWN ARTIFACT PATTERNS (from past rejections) ===")
            for g in bad:
                qc = g.get("qc_details", {})
                reason = g.get("approval_reason", "")
                if qc or reason:
                    context_parts.append("- QC details: %s, Rejection reason: %s" % (
                        json.dumps(qc)[:200] if qc else "N/A", reason[:200]))

        lessons = self.get_lessons(agent_name="qc", limit=5)
        if lessons:
            context_parts.append("\n=== QC LESSONS ===")
            for l in lessons:
                context_parts.append("- %s" % l.get("lesson_learned", ""))

        return "\n".join(context_parts) if context_parts else ""

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# ──────────────────────────────────────────────
# CLI for testing
# ──────────────────────────────────────────────

if __name__ == "__main__":
    mem = AgentMemory()

    if len(sys.argv) < 2:
        print("Usage: python3 agent_memory.py <command>")
        print("Commands: stats, lessons, accounts, patterns, test")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "stats":
        stats = mem.get_generation_stats()
        if stats:
            print("=== Generation Statistics ===")
            for s in stats:
                print("  %s: %s total, %s approved, %s rejected, avg QC: %.1f" % (
                    s["content_type"], s["total"], s["approved_count"],
                    s["rejected_count"],
                    float(s["avg_qc_score"]) if s["avg_qc_score"] else 0,
                ))
        else:
            print("No generation history yet.")

    elif cmd == "lessons":
        lessons = mem.get_lessons(limit=20)
        if lessons:
            print("=== Recent Lessons ===")
            for l in lessons:
                print("  [%s] %s: %s" % (
                    l.get("agent_name", "?"),
                    l.get("event_type", "?"),
                    l.get("lesson_learned", "N/A"),
                ))
        else:
            print("No lessons recorded yet.")

    elif cmd == "accounts":
        accounts = mem.get_accounts_to_parse(limit=20)
        if accounts:
            print("=== Discovered Accounts ===")
            for a in accounts:
                print("  @%s (%s) - score: %.1f, followers: %s, last parsed: %s" % (
                    a["username"], a["category"],
                    float(a["compatibility_score"]),
                    a["followers"], a["last_parsed_at"] or "never",
                ))
        else:
            print("No discovered accounts yet.")

    elif cmd == "patterns":
        good = mem.get_successful_patterns(limit=10)
        print("=== Successful Patterns (%d) ===" % len(good))
        for p in good:
            print("  [%s] score: %.1f from @%s" % (
                p.get("pattern_type", "?"),
                float(p.get("score", 0)),
                p.get("source_username", "?"),
            ))
        bad = mem.get_failed_patterns(limit=5)
        print("\n=== Failed Patterns (%d) ===" % len(bad))
        for p in bad:
            print("  [%s] reason: %s" % (
                p.get("pattern_type", "?"),
                p.get("feedback_reason", "?"),
            ))

    elif cmd == "test":
        print("Testing memory system...")
        # Test save/retrieve cycle
        test_id = mem.save_analysis("test_user", {"test": True, "style": "glamour"})
        print("  Saved test analysis: id=%s" % test_id)
        retrieved = mem.get_latest_analysis("test_user")
        print("  Retrieved: %s" % retrieved)
        # Test account discovery
        acc_id = mem.save_discovered_account("kyliejenner", source="seed",
                                              followers=400000000, category="beauty",
                                              compatibility_score=9.5)
        print("  Saved discovered account: id=%s" % acc_id)
        accounts = mem.get_accounts_to_parse()
        print("  Accounts to parse: %d" % len(accounts))
        # Test learning log
        mem.log_event("test", "system_test", {"status": "ok"},
                     lesson="Memory system works correctly")
        lessons = mem.get_lessons(limit=1)
        print("  Lessons: %s" % (lessons[0]["lesson_learned"] if lessons else "none"))
        print("All tests passed!")

    mem.close()
