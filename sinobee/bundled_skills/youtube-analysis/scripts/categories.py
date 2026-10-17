"""The 16 video categories. Keys are what cover.json and the report use; references/categories.md explains each."""

# key: (中文, English, fact-check level)
#   full    - every factual claim that carries the argument is checked
#   claims  - the key factual claims (numbers, names, dates, product specs, medical/financial statements) are checked
#   none    - no fact-check section; craft and content analysis instead
CATEGORIES = {
    "news": ("新聞時事", "News", "full"),
    "politics": ("政治評論", "Politics & Opinion", "full"),
    "science": ("知識科普", "Science & Explainers", "full"),
    "tutorial": ("教學實作", "Tutorial & How-to", "claims"),
    "review": ("開箱評測", "Product Review", "claims"),
    "finance": ("財經投資", "Finance & Investing", "full"),
    "health": ("醫療健康", "Health & Medicine", "full"),
    "tech": ("科技趨勢", "Tech & AI", "full"),
    "interview": ("訪談對談", "Interview & Podcast", "claims"),
    "talk": ("演講講座", "Talk & Lecture", "claims"),
    "documentary": ("紀錄片", "Documentary", "full"),
    "vlog": ("生活 Vlog", "Vlog & Lifestyle", "none"),
    "travel-food": ("旅遊美食", "Travel & Food", "claims"),
    "gaming": ("遊戲電競", "Gaming & Esports", "none"),
    "music": ("音樂表演", "Music & Performance", "none"),
    "entertainment": ("娛樂戲劇", "Entertainment & Drama", "none"),
}

# the heading each category's analysis section must carry in the report (after "## 分類分析：")
VERDICTS = ["正確", "大致正確", "部分正確", "缺乏脈絡", "誤導", "錯誤", "無法查證", "觀點"]


def label(key: str) -> str:
    zh, en, _ = CATEGORIES[key]
    return f"{zh} · {en}"
