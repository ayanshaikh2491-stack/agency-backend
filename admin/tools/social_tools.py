"""Social Agent — Advanced Tools Suite.

Platform Intelligence, Trending, Competitor Tracking, Content Calendar, etc.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Organic engine imports (lazy, so social_tools stays importable standalone) ──
def _organic_hub():
    from admin.tools.organic.hub import post as hub_post
    return hub_post


def _organic_registry():
    from admin.tools.organic.registry import list_channels
    return list_channels()


def _organic_config():
    from admin.tools.organic.config import get_channel_config, list_channel_configs, save_channel_config
    return get_channel_config, list_channel_configs, save_channel_config


# Platform Intelligence

PLATFORM_INTELLIGENCE = {
    "instagram": {
        "algorithm_factors": [
            "Relationship (how often user interacts with you)",
            "Interest (how relevant content is to user)",
            "Timeliness (how recent the post is)",
            "Session Behavior (what user engages with most)",
        ],
        "best_practices": [
            "Reels get 2x more reach than static posts",
            "Carousel posts get 2x more saves",
            "First 3 seconds of Reel determine 50% watch time",
            "Use 3-5 hashtags in caption, 20-30 in first comment",
            "Post when audience is most active (check Insights)",
        ],
        "content_limits": {
            "caption_max": 2200,
            "hashtag_max": 30,
            "reel_max_seconds": 90,
            "story_max": 100,
            "carousel_max": 10,
        },
        "best_times": {
            "monday": ["9am", "12pm", "7pm"],
            "tuesday": ["9am", "2pm", "7pm"],
            "wednesday": ["9am", "12pm", "7pm"],
            "thursday": ["9am", "2pm", "7pm"],
            "friday": ["9am", "12pm", "5pm"],
            "saturday": ["10am", "2pm", "8pm"],
            "sunday": ["10am", "2pm", "8pm"],
        },
    },
    "linkedin": {
        "algorithm_factors": [
            "Connection strength",
            "Content relevance",
            "Engagement velocity (first hour matters most)",
            "Content format preference",
        ],
        "best_practices": [
            "Articles get 3x more engagement than posts",
            "Personal stories outperform corporate content",
            "Use line breaks for readability",
            "Tag relevant people/companies",
            "Comment on others' posts to boost your visibility",
        ],
        "content_limits": {
            "post_max": 3000,
            "article_max": 11000,
            "hashtag_max": 5,
        },
        "best_times": {
            "tuesday": ["8am", "10am", "12pm"],
            "wednesday": ["8am", "10am", "12pm"],
            "thursday": ["8am", "10am", "12pm"],
        },
    },
    "twitter": {
        "algorithm_factors": [
            "Recency",
            "Engagement (likes, retweets, replies)",
            "Media presence (images, videos boost reach)",
            "Thread completion rate",
        ],
        "best_practices": [
            "Threads get 10x more impressions than single tweets",
            "First tweet of thread determines if people read rest",
            "Use polls for engagement (2x replies)",
            "Quote tweet with opinion > simple retweet",
            "Post 3-5 times daily for maximum reach",
        ],
        "content_limits": {
            "tweet_max": 280,
            "thread_max": 25,
            "hashtag_max": 2,
        },
        "best_times": {
            "daily": ["12pm", "3pm", "5pm"],
        },
    },
    "tiktok": {
        "algorithm_factors": [
            "Watch time (most important factor)",
            "Completion rate",
            "Shares and saves",
            "Trending sounds/topics",
        ],
        "best_practices": [
            "Hook in first 1-2 seconds",
            "Use trending sounds (check For You page)",
            "Keep videos 15-30 seconds for best completion",
            "Post 1-3 times daily",
            "Engage with comments (reply with video)",
        ],
        "content_limits": {
            "video_max_seconds": 180,
            "caption_max": 300,
            "hashtag_max": 5,
        },
        "best_times": {
            "daily": ["7am", "12pm", "7pm"],
        },
    },
    "youtube": {
        "algorithm_factors": [
            "Watch time (total minutes watched)",
            "Click-through rate (thumbnail + title)",
            "Viewer retention",
            "Upload frequency",
        ],
        "best_practices": [
            "Thumbnails determine 70% of clicks",
            "First 30 seconds determine if viewer stays",
            "Videos 8-15 minutes get most watch time",
            "Post Shorts for discovery, long-form for retention",
            "Use chapters for better navigation",
        ],
        "content_limits": {
            "short_max_seconds": 60,
            "title_max": 100,
            "description_max": 5000,
        },
        "best_times": {
            "friday": ["2pm", "3pm"],
            "saturday": ["9am", "10am"],
            "sunday": ["9am", "10am"],
        },
    },
    "facebook": {
        "algorithm_factors": [
            "Meaningful interactions (comments > likes)",
            "Content type preference",
            "Relationship with poster",
            "Content quality signals",
        ],
        "best_practices": [
            "Long captions (40-80 words) get more engagement",
            "Video gets 135% more organic reach than photos",
            "Go Live for 4x more engagement",
            "Ask questions to drive comments",
            "Share in relevant Groups for extra reach",
        ],
        "content_limits": {
            "caption_max": 63206,
            "video_max_seconds": 240,
        },
        "best_times": {
            "wednesday": ["11am", "1pm"],
            "thursday": ["12pm", "2pm"],
            "friday": ["10am", "11am"],
        },
    },
}


def get_platform_intelligence(platform: str) -> dict[str, Any]:
    """Platform-specific intelligence return karo."""
    return PLATFORM_INTELLIGENCE.get(platform.lower(), {})


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT CALENDAR
# ═══════════════════════════════════════════════════════════════════════════════

CONTENT_PILLARS = {
    "educational": {
        "description": "Tips, how-tos, tutorials, industry insights",
        "engagement_type": "saves",
        "best_platforms": ["instagram", "linkedin", "youtube"],
        "frequency": "2-3 times per week",
    },
    "entertaining": {
        "description": "Memes, trends, humor, relatable content",
        "engagement_type": "shares",
        "best_platforms": ["instagram", "tiktok", "twitter"],
        "frequency": "3-4 times per week",
    },
    "inspiring": {
        "description": "Success stories, motivational, behind-the-scenes",
        "engagement_type": "comments",
        "best_platforms": ["instagram", "linkedin", "facebook"],
        "frequency": "1-2 times per week",
    },
    "promotional": {
        "description": "Product features, offers, testimonials",
        "engagement_type": "clicks",
        "best_platforms": ["instagram", "facebook", "linkedin"],
        "frequency": "1-2 times per week",
    },
    "community": {
        "description": "User-generated content, reposts, community highlights",
        "engagement_type": "trust",
        "best_platforms": ["instagram", "facebook", "twitter"],
        "frequency": "1-2 times per week",
    },
}


def generate_content_calendar(
    platform: str,
    weeks: int = 4,
    pillars: list[str] | None = None,
) -> dict[str, Any]:
    """Monthly content calendar banao."""
    if pillars is None:
        pillars = ["educational", "entertaining", "inspiring", "promotional", "community"]

    calendar = {
        "platform": platform,
        "weeks": weeks,
        "pillars_used": pillars,
        "schedule": [],
    }

    intel = get_platform_intelligence(platform)
    best_times = intel.get("best_times", {})

    start_date = datetime.now()
    for week in range(weeks):
        week_start = start_date + timedelta(weeks=week)
        week_schedule = {
            "week": week + 1,
            "start_date": week_start.strftime("%Y-%m-%d"),
            "posts": [],
        }

        # Assign pillars to days
        days_to_post = 5 if platform in ("instagram", "linkedin") else 7
        for day in range(days_to_post):
            post_date = week_start + timedelta(days=day)
            pillar = pillars[day % len(pillars)]

            week_schedule["posts"].append({
                "date": post_date.strftime("%Y-%m-%d"),
                "day": post_date.strftime("%A"),
                "pillar": pillar,
                "best_time": best_times.get(post_date.strftime("%A").lower(), ["12pm"])[0] if best_times.get(post_date.strftime("%A").lower()) else "12pm",
                "content_type": CONTENT_PILLARS[pillar]["description"].split(",")[0],
                "status": "planned",
            })

        calendar["schedule"].append(week_schedule)

    return calendar


# ═══════════════════════════════════════════════════════════════════════════════
# HASHTAG RESEARCH
# ═══════════════════════════════════════════════════════════════════════════════

def research_hashtags(
    topic: str,
    platform: str = "instagram",
    count: int = 20,
) -> dict[str, Any]:
    """Hashtag research karo (real context via Agent-Reach)."""
    from admin.tools.social_reach import reach_hashtags
    return reach_hashtags(topic=topic, platform=platform, count=count)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPETITOR ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_competitor(
    competitor_handles: list[str],
    platform: str = "instagram",
) -> dict[str, Any]:
    """Competitor content analysis (real data via Agent-Reach when possible)."""
    from admin.tools.social_reach import reach_competitor
    competitor = competitor_handles[0] if competitor_handles else ""
    real = reach_competitor(competitor=competitor, platform=platform)
    real["analysis_framework"] = {
        "content_types": "What types of posts do they create?",
        "posting_frequency": "How often do they post?",
        "engagement_rate": "What's their average engagement?",
        "top_posts": "What performed best for them?",
        "visual_style": "What's their visual identity?",
        "caption_style": "How do they write captions?",
        "hashtag_strategy": "What hashtags do they use?",
        "weaknesses": "Where are they falling short?",
    }
    return real


# ═══════════════════════════════════════════════════════════════════════════════
# COMMUNITY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

COMMUNITY_RESPONSE_STRATEGIES = {
    "positive_comment": {
        "tone": "grateful, encouraging",
        "action": "Thank them, ask follow-up question",
        "template": "Thank you so much! {follow_up_question}",
    },
    "question": {
        "tone": "helpful, informative",
        "action": "Answer clearly, offer additional help",
        "template": "Great question! {answer} Let me know if you need more info!",
    },
    "complaint": {
        "tone": "empathetic, solution-focused",
        "action": "Acknowledge, apologize, offer solution",
        "template": "We're sorry to hear that. {solution} DM us and we'll make it right!",
    },
    "spam": {
        "tone": "neutral",
        "action": "Hide or delete, don't engage",
        "template": "[Hide/Delete]",
    },
    "ugc_request": {
        "tone": "excited, appreciative",
        "action": "Ask permission, credit them",
        "template": "This is amazing! Would you mind if we share this? We'll credit you!",
    },
}


def get_response_strategy(comment_type: str) -> dict[str, Any]:
    """Comment type ke liye response strategy return karo."""
    return COMMUNITY_RESPONSE_STRATEGIES.get(comment_type, {
        "tone": "friendly, professional",
        "action": "Respond appropriately",
        "template": "Thank you for your comment!",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# SOCIAL LISTENING
# ═══════════════════════════════════════════════════════════════════════════════

def get_social_listening_queries(brand_name: str, industry: str) -> dict[str, Any]:
    """Social listening queries banao."""
    return {
        "brand_mentions": [
            f'"{brand_name}"',
            f'"{brand_name}" review',
            f'"{brand_name}" experience',
            f'"{brand_name}" problem',
            f'"{brand_name}" recommend',
        ],
        "competitor_mentions": [
            f"[competitor] vs {brand_name}",
            f"[competitor] alternative",
        ],
        "industry_queries": [
            f"{industry} tips",
            f"{industry} news",
            f"{industry} trends",
            f"best {industry} tools",
        ],
        "sentiment_keywords": {
            "positive": ["love", "amazing", "great", "best", "recommend", "awesome"],
            "negative": ["hate", "terrible", "worst", "avoid", "scam", "broken"],
            "neutral": ["okay", "fine", "decent", "average", "standard"],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CRISIS MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

CRISIS_PROTOCOLS = {
    "level_1_negative_feedback": {
        "severity": "low",
        "description": "Individual negative comments",
        "response_time": "24 hours",
        "action": "Respond professionally, offer solution",
        "escalation": "No",
    },
    "level_2_viral_complaint": {
        "severity": "medium",
        "description": "Negative post going viral",
        "response_time": "2 hours",
        "action": "Immediate response, public apology if needed",
        "escalation": "Notify workspace CEO",
    },
    "level_3_pr_crisis": {
        "severity": "high",
        "description": "Major PR crisis, media coverage",
        "response_time": "30 minutes",
        "action": "Pause all scheduled content, draft official statement",
        "escalation": "Notify Agency CEO + Workspace CEO",
    },
}


def get_crisis_response(severity: str) -> dict[str, Any]:
    """Crisis severity ke liye response protocol return karo."""
    return CRISIS_PROTOCOLS.get(severity, CRISIS_PROTOCOLS["level_1_negative_feedback"])


# ═══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_engagement_rate(
    likes: int,
    comments: int,
    shares: int,
    followers: int,
) -> float:
    """Engagement rate calculate karo."""
    if followers == 0:
        return 0.0
    total_engagement = likes + comments + shares
    return round((total_engagement / followers) * 100, 2)


def get_platform_benchmarks(platform: str) -> dict[str, Any]:
    """Platform-specific engagement benchmarks."""
    benchmarks = {
        "instagram": {
            "good_engagement_rate": 3.0,
            "excellent_engagement_rate": 6.0,
            "average_likes_per_post": "1-3% of followers",
            "average_comments_per_post": "0.1-0.5% of followers",
        },
        "linkedin": {
            "good_engagement_rate": 2.0,
            "excellent_engagement_rate": 5.0,
            "average_likes_per_post": "1-2% of connections",
            "average_comments_per_post": "0.1-0.3% of connections",
        },
        "twitter": {
            "good_engagement_rate": 1.0,
            "excellent_engagement_rate": 3.0,
            "average_likes_per_post": "0.5-1% of followers",
            "average_retweets_per_post": "0.1-0.3% of followers",
        },
        "tiktok": {
            "good_engagement_rate": 5.0,
            "excellent_engagement_rate": 10.0,
            "average_views": "10-30% of followers",
            "average_likes_per_video": "3-9% of views",
        },
    }
    return benchmarks.get(platform, {})


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS (used by social.py agent + api/routes/social.py)
# ═══════════════════════════════════════════════════════════════════════════════


def content_calendar(platform: str = "instagram", weeks: int = 4, topic: str = "") -> dict[str, Any]:
    """Content calendar generate karo."""
    return generate_content_calendar(platform, weeks)


def hashtag_research(topic: str = "", platform: str = "instagram", count: int = 20) -> dict[str, Any]:
    """Hashtag research karo."""
    return research_hashtags(topic, platform, count)


def posting_schedule(platform: str = "instagram") -> dict[str, Any]:
    """Best posting times return karo."""
    intel = get_platform_intelligence(platform)
    return {
        "platform": platform,
        "best_times": intel.get("best_times", {}),
        "best_practices": intel.get("best_practices", []),
    }


def competitor_analysis(competitors: list[str] | None = None, platform: str = "instagram") -> dict[str, Any]:
    """Competitor analysis karo."""
    return analyze_competitor(competitors or [], platform)


def trend_research(topic: str = "", platform: str = "instagram") -> dict[str, Any]:
    """Trending topics research karo (real data via Agent-Reach)."""
    from admin.tools.social_reach import reach_trending
    return reach_trending(topic=topic, platform=platform)


def engagement_strategy(platform: str = "instagram") -> dict[str, Any]:
    """Community engagement strategy banao."""
    return {
        "platform": platform,
        "strategies": list(COMMUNITY_RESPONSE_STRATEGIES.keys()),
        "response_guidelines": COMMUNITY_RESPONSE_STRATEGIES,
    }


def platform_strategy(platform: str = "instagram") -> dict[str, Any]:
    """Platform-specific strategy return karo."""
    return get_platform_intelligence(platform)


def content_gap_analysis(competitors: list[str] | None = None, platform: str = "instagram") -> dict[str, Any]:
    """Content gap analysis karo."""
    return {
        "competitors": competitors or [],
        "platform": platform,
        "analysis_type": "gap_analysis",
        "note": "LLM will identify content gaps vs competitors",
    }


def audience_analysis(platform: str = "instagram", industry: str = "") -> dict[str, Any]:
    """Target audience analysis (real platform intel + web signal)."""
    from admin.tools.social_reach import reach_audience
    return reach_audience(platform=platform, industry=industry)


def growth_tactics(platform: str = "instagram", goal: str = "followers") -> dict[str, Any]:
    """Growth tactics banao."""
    return {
        "platform": platform,
        "goal": goal,
        "tactics": [
            "Consistent posting schedule",
            "Engage with community",
            "Cross-platform promotion",
            "Collaborate with creators",
            "Use trending formats",
        ],
    }


def generate_caption(topic: str = "", platform: str = "instagram", tone: str = "professional") -> dict[str, Any]:
    """Post caption generate karo."""
    return {
        "topic": topic,
        "platform": platform,
        "tone": tone,
        "caption_limit": get_platform_intelligence(platform).get("content_limits", {}).get("post_max", 2200),
    }


def repurpose_content(content: str = "", target_platforms: list[str] | None = None) -> dict[str, Any]:
    """Content ko multiple platforms ke liye adapt karo."""
    return {
        "source_content": content[:200],
        "target_platforms": target_platforms or ["instagram", "linkedin", "twitter"],
        "note": "LLM will adapt content for each platform",
    }


def dm_outreach(brand: str = "", audience: str = "") -> dict[str, Any]:
    """DM outreach templates banao."""
    return {
        "brand": brand,
        "audience": audience,
        "templates": {
            "collaboration": "Hi! Love your content...",
            "ugc_request": "Great post! Would you mind if...",
            "feedback": "Hey! Quick question about...",
        },
    }


def influencer_research(niche: str = "", platform: str = "instagram") -> dict[str, Any]:
    """Influencer research karo."""
    return {
        "niche": niche,
        "platform": platform,
        "criteria": ["engagement_rate", "audience_fit", "content_quality", "authenticity"],
    }


def analytics_report(platform: str = "instagram", period: str = "weekly") -> dict[str, Any]:
    """Analytics report banao."""
    return {
        "platform": platform,
        "period": period,
        "metrics": [
            "engagement_rate", "reach", "impressions", "followers_gained",
            "top_posts", "best_times", "audience_growth",
        ],
        "benchmarks": get_platform_benchmarks(platform),
    }


def create_post(topic: str = "", platform: str = "instagram") -> dict[str, Any]:
    """Complete post create karo (caption + hashtags + media plan)."""
    return {
        "topic": topic,
        "platform": platform,
        "components": ["caption", "hashtags", "media_plan", "posting_time"],
        "platform_intel": get_platform_intelligence(platform),
    }


def schedule_post(post_data: dict[str, Any] | None = None, datetime_str: str = "") -> dict[str, Any]:
    """Schedule a post. If post_data contains 'channel', queue through the real
    organic scheduler (dispatched by the backend 60s loop at run_at)."""
    post_data = post_data or {}
    if post_data.get("channel"):
        from admin.tools.organic.scheduler import schedule_post as organic_schedule
        return organic_schedule(
            workspace_id=post_data.get("workspace_id", "default"),
            channel=post_data["channel"],
            payload=post_data.get("payload") or post_data.get("post_data") or {},
            run_at=datetime_str or post_data.get("run_at", ""),
        )
    return {
        "status": "scheduled",
        "scheduled_for": datetime_str,
        "post_data": post_data,
        "note": "Channel-specific scheduling routes through the organic engine.",
    }


def organic_post(channel: str, workspace_id: str, payload: dict) -> dict[str, Any]:
    """Post to an organic channel (reddit, telegram, twitter, linkedin, pinterest, gbp, facebook)."""
    hub_post = _organic_hub()
    result = hub_post(channel, workspace_id, payload)
    try:
        from admin.tools.organic.history import record_post
        history_id = record_post(workspace_id, channel, result, payload)
        if isinstance(result, dict):
            result["history_id"] = history_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("history record failed for %s/%s: %s", workspace_id, channel, exc)
    return result


def organic_channels(workspace_id: str = "default") -> dict[str, Any]:
    """List available organic channels + their configs for a workspace."""
    channels = _organic_registry()
    _, list_configs, _ = _organic_config()
    return {"channels": channels, "configs": list_configs(workspace_id)}


def organic_save_config(channel: str, workspace_id: str, config: dict) -> dict[str, Any]:
    """Save per-workspace config for an organic channel (subreddits, chat_id, profile_dir...)."""
    _, _, save_config = _organic_config()
    return save_config(workspace_id, channel, config)


def post_now(post_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Post immediately. If post_data contains 'channel', route through organic engine."""
    post_data = post_data or {}
    if post_data.get("channel"):
        return organic_post(
            post_data["channel"],
            post_data.get("workspace_id", "default"),
            {k: v for k, v in post_data.items() if k not in ("channel", "workspace_id")},
        )
    return {
        "status": "published",
        "post_data": post_data,
        "published_at": datetime.now().isoformat(),
    }


def social_accounts() -> dict[str, Any]:
    """Connected social accounts list karo."""
    return {
        "accounts": [
            {"platform": "instagram", "status": "connected"},
            {"platform": "linkedin", "status": "connected"},
            {"platform": "twitter", "status": "connected"},
            {"platform": "tiktok", "status": "pending"},
            {"platform": "facebook", "status": "connected"},
        ],
    }


def content_queue() -> dict[str, Any]:
    """Scheduled content queue dekho."""
    return {
        "queue": [],
        "note": "Production mein SocialClaw se real queue aayegi",
    }


def post_analytics(post_id: str = "") -> dict[str, Any]:
    """Individual post analytics track karo."""
    return {
        "post_id": post_id,
        "metrics": ["likes", "comments", "shares", "saves", "reach", "impressions"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SOCIAL_TOOLS — LangGraph tool definitions
# ═══════════════════════════════════════════════════════════════════════════════

SOCIAL_TOOLS = [
    {"type": "function", "function": {"name": "content_calendar", "description": "Generate content calendar", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "weeks": {"type": "integer"}, "topic": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "hashtag_research", "description": "Find relevant hashtags", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "platform": {"type": "string"}, "count": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "posting_schedule", "description": "Best times to post", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "competitor_analysis", "description": "Analyze competitor social presence", "parameters": {"type": "object", "properties": {"competitors": {"type": "array", "items": {"type": "string"}}, "platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "trend_research", "description": "Find trending topics", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "engagement_strategy", "description": "Plan community management", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "platform_strategy", "description": "Platform-specific strategy", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "content_gap_analysis", "description": "What competitors post that you don't", "parameters": {"type": "object", "properties": {"competitors": {"type": "array", "items": {"type": "string"}}, "platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "audience_analysis", "description": "Target audience insights", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "industry": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "growth_tactics", "description": "Follower acquisition plan", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "goal": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "generate_caption", "description": "Generate post captions", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "platform": {"type": "string"}, "tone": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "repurpose_content", "description": "Adapt content for multiple platforms", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "target_platforms": {"type": "array", "items": {"type": "string"}}}}}},
    {"type": "function", "function": {"name": "dm_outreach", "description": "DM templates + outreach strategy", "parameters": {"type": "object", "properties": {"brand": {"type": "string"}, "audience": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "influencer_research", "description": "Find organic influencers", "parameters": {"type": "object", "properties": {"niche": {"type": "string"}, "platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "analytics_report", "description": "Organic performance report", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "period": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "create_post", "description": "Create complete post", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "platform": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "schedule_post", "description": "Schedule post via SocialClaw", "parameters": {"type": "object", "properties": {"post_data": {"type": "object"}, "datetime_str": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "post_now", "description": "Publish immediately via SocialClaw", "parameters": {"type": "object", "properties": {"post_data": {"type": "object"}}}}},
    {"type": "function", "function": {"name": "organic_post", "description": "Post to organic channels (reddit, telegram, twitter, linkedin, pinterest, gbp, facebook)", "parameters": {"type": "object", "properties": {"channel": {"type": "string"}, "workspace_id": {"type": "string"}, "payload": {"type": "object"}}}}},
    {"type": "function", "function": {"name": "organic_channels", "description": "List organic channels and their configs", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "organic_save_config", "description": "Save channel config (subreddits, chat_id, profile_dir)", "parameters": {"type": "object", "properties": {"channel": {"type": "string"}, "workspace_id": {"type": "string"}, "config": {"type": "object"}}}}},
    {"type": "function", "function": {"name": "social_accounts", "description": "Manage connected accounts", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "content_queue", "description": "View scheduled posts", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "post_analytics", "description": "Track post performance", "parameters": {"type": "object", "properties": {"post_id": {"type": "string"}}}}},
]


# Tool registry for execute_social_tool
_TOOL_REGISTRY: dict[str, Any] = {
    "content_calendar": content_calendar,
    "hashtag_research": hashtag_research,
    "posting_schedule": posting_schedule,
    "competitor_analysis": competitor_analysis,
    "trend_research": trend_research,
    "engagement_strategy": engagement_strategy,
    "platform_strategy": platform_strategy,
    "content_gap_analysis": content_gap_analysis,
    "audience_analysis": audience_analysis,
    "growth_tactics": growth_tactics,
    "generate_caption": generate_caption,
    "repurpose_content": repurpose_content,
    "dm_outreach": dm_outreach,
    "influencer_research": influencer_research,
    "analytics_report": analytics_report,
    "create_post": create_post,
    "schedule_post": schedule_post,
    "post_now": post_now,
    "organic_post": organic_post,
    "organic_channels": organic_channels,
    "organic_save_config": organic_save_config,
    "social_accounts": social_accounts,
    "content_queue": content_queue,
    "post_analytics": post_analytics,
}


def execute_social_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a social tool by name with given arguments."""
    func = _TOOL_REGISTRY.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        # The worker bridge injects delegation metadata (workspace_id, __brief)
        # that not every tool accepts. Only forward kwargs the tool declares.
        try:
            sig = inspect.signature(func)
            filtered = {k: v for k, v in args.items() if k in sig.parameters}
        except (ValueError, TypeError):
            filtered = args
        return func(**filtered)
    except Exception as e:
        logger.exception("execute_social_tool failed: %s", name)
        return {"error": str(e)}
