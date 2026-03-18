"""Topics bank for real estate content."""
from __future__ import annotations

import logging
import random
from datetime import datetime

from db import execute, execute_insert

log = logging.getLogger(__name__)

# Default topics organized by category
DEFAULT_TOPICS = {
    "purchase": [
        "Как выбрать земельный участок: 5 главных критериев",
        "Что проверить перед покупкой загородного дома",
        "Покупка дома в деревне: на что обратить внимание",
        "Как не переплатить за земельный участок",
        "5 ошибок при покупке недвижимости",
        "Подбор участка под строительство дома",
        "Как оценить перспективы земельного участка",
        "Покупка участка у реки: плюсы и подводные камни",
        "Загородная недвижимость как инвестиция",
        "На что обращать внимание при осмотре дома",
    ],
    "sale": [
        "Как подготовить дом к продаже: пошаговый план",
        "Как определить рыночную цену недвижимости",
        "Сезонность продажи загородной недвижимости",
        "Как ускорить продажу земельного участка",
        "Фотографии для объявления: как подать объект выгодно",
        "Типичные ошибки при продаже дома",
    ],
    "land": [
        "ИЖС, ЛПХ, СНТ — в чём разница и что выбрать",
        "Межевание участка: зачем нужно и как проводится",
        "Как проверить границы земельного участка",
        "Перевод земли из одной категории в другую",
        "Объединение и раздел земельных участков",
        "Кадастровый учёт: что это и зачем нужен",
        "Ограничения и обременения на земельный участок",
        "Как узнать историю земельного участка",
    ],
    "documents": [
        "Какие документы нужны для сделки с недвижимостью",
        "Как оформить право собственности на дом",
        "Оформление дачи: пошаговая инструкция",
        "Договор купли-продажи: на что обратить внимание",
        "Как проверить юридическую чистоту объекта",
        "Регистрация права собственности через МФЦ",
        "Доверенность на продажу недвижимости",
    ],
    "legal": [
        "Наследование недвижимости: основные правила",
        "Как разделить дом между собственниками",
        "Приватизация земельного участка",
        "Налог на продажу недвижимости: когда и сколько",
        "Материнский капитал при покупке жилья",
        "Что делать если дом не оформлен",
        "Сделка с недвижимостью через нотариуса",
    ],
    "advice": [
        "Когда лучше покупать загородную недвижимость",
        "Как выбрать район для покупки дома",
        "Плюсы жизни за городом в Нижегородской области",
        "Дом у реки: романтика или головная боль",
        "Городецкий район: почему сюда переезжают",
        "Чкаловский район: природа и перспективы",
        "Что нужно знать о загородной жизни",
        "Как переехать за город: практические советы",
    ],
    "cases": [
        "Как мы помогли клиенту найти участок мечты",
        "Сложный случай: оформление дома без документов",
        "Как мы решили спор о границах участка",
        "История покупки: от осмотра до ключей за 2 недели",
    ],
}


def init_topics_bank():
    """Initialize topics bank with default topics."""
    for category, topics in DEFAULT_TOPICS.items():
        for topic in topics:
            try:
                execute_insert(
                    "INSERT OR IGNORE INTO topics_bank (topic, category, source) VALUES (?, ?, 'default')",
                    (topic, category),
                )
            except Exception:
                pass
    log.info("Topics bank initialized with %d categories", len(DEFAULT_TOPICS))


def add_parsed_topic(topic: str, category: str, source: str = "parsed"):
    """Add a topic from competitor parsing."""
    try:
        execute_insert(
            "INSERT OR IGNORE INTO topics_bank (topic, category, source) VALUES (?, ?, ?)",
            (topic, category, source),
        )
    except Exception:
        pass


def get_next_topic(post_type: str = "post") -> dict:
    """Get the best topic to use next (least recently used).
    
    Returns {'topic': str, 'category': str}.
    """
    # Get least used topics
    rows = execute(
        """SELECT topic, category, used_count, last_used_at 
           FROM topics_bank 
           ORDER BY used_count ASC, last_used_at ASC NULLS FIRST
           LIMIT 20""",
    )
    
    if not rows:
        # Fallback: pick random default topic
        all_topics = []
        for topics in DEFAULT_TOPICS.values():
            all_topics.extend(topics)
        topic = random.choice(all_topics) if all_topics else "Советы по покупке недвижимости"
        return {"topic": topic, "category": "advice"}
    
    # Pick from top 5 least used (add some randomness)
    candidates = [dict(r) for r in rows[:5]]
    pick = random.choice(candidates)
    
    # Mark as used
    execute_insert(
        "UPDATE topics_bank SET used_count = used_count + 1, last_used_at = ? WHERE topic = ?",
        (datetime.now().isoformat(), pick["topic"]),
    )
    
    return {"topic": pick["topic"], "category": pick["category"]}


def get_topic_stats() -> dict:
    """Get topics bank statistics."""
    total = execute("SELECT COUNT(*) as cnt FROM topics_bank")
    used = execute("SELECT COUNT(*) as cnt FROM topics_bank WHERE used_count > 0")
    by_category = execute(
        "SELECT category, COUNT(*) as cnt FROM topics_bank GROUP BY category ORDER BY cnt DESC"
    )
    
    return {
        "total": total[0]["cnt"] if total else 0,
        "used": used[0]["cnt"] if used else 0,
        "by_category": {r["category"]: r["cnt"] for r in by_category},
    }
