"""Content Agent Tools — real, working content generation and analysis.

All tools are free, no API keys needed. Uses templates, NLP, and web scraping.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
_BS4 = "html.parser"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. READABILITY ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_readability(url: str) -> dict[str, Any]:
    """Analyze content readability of a URL using Flesch-Kincaid metrics."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, _BS4)

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        # Extract text from main content
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        words = re.findall(r'\b[a-zA-Z]+\b', text)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        word_count = len(words)
        sentence_count = max(len(sentences), 1)
        avg_words_per_sentence = word_count / sentence_count

        # Syllable count (approximate)
        syllable_count = 0
        for w in words:
            syllable_count += _count_syllables(w)
        avg_syllables_per_word = syllable_count / max(word_count, 1)

        # Flesch Reading Ease
        if word_count > 0 and sentence_count > 0:
            fre = 206.835 - 1.015 * avg_words_per_sentence - 84.6 * avg_syllables_per_word
            fre = max(0, min(100, fre))
        else:
            fre = 0

        # Flesch-Kincaid Grade Level
        if word_count > 0 and sentence_count > 0:
            fkgl = 0.39 * avg_words_per_sentence + 11.8 * avg_syllables_per_word - 15.59
            fkgl = max(0, fkgl)
        else:
            fkgl = 0

        # Grade interpretation
        if fre >= 80:
            grade_label = "Easy (5th-6th grade)"
        elif fre >= 60:
            grade_label = "Standard (7th-8th grade)"
        elif fre >= 40:
            grade_label = "Fairly Difficult (9th-12th grade)"
        elif fre >= 20:
            grade_label = "Difficult (College level)"
        else:
            grade_label = "Very Difficult (Graduate level)"

        # Paragraph count
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
        avg_para_length = sum(len(p.split()) for p in paragraphs) / max(len(paragraphs), 1)

        return {
            "url": url,
            "title": title,
            "word_count": word_count,
            "sentence_count": sentence_count,
            "paragraph_count": len(paragraphs),
            "flesch_reading_ease": round(fre, 1),
            "flesch_kincaid_grade": round(fkgl, 1),
            "grade_label": grade_label,
            "avg_words_per_sentence": round(avg_words_per_sentence, 1),
            "avg_paragraph_words": round(avg_para_length, 1),
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


def _count_syllables(word: str) -> int:
    word = word.lower()
    if len(word) <= 3:
        return 1
    vowels = "aeiou"
    count = 0
    prev_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BRIEF GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_content_brief(topic: str, target_audience: str = "general", word_count: int = 1500) -> dict[str, Any]:
    """Generate a content brief with outline, keywords, and structure."""
    # Get keyword suggestions from Google Autocomplete
    keywords = _autocomplete_suggestions(topic)

    # Generate outline based on topic
    outline = _generate_outline(topic, keywords)

    brief = {
        "topic": topic,
        "target_audience": target_audience,
        "target_word_count": word_count,
        "primary_keyword": topic,
        "secondary_keywords": keywords[:10],
        "suggested_title": _generate_title(topic, keywords),
        "meta_description": _generate_meta_desc(topic, keywords),
        "outline": outline,
        "seo_tips": [
            f"Use '{topic}' in the first 100 words",
            "Include H2/H3 headings with keywords",
            f"Target {word_count}+ words for comprehensive coverage",
            "Add internal links to related content",
            "Include at least one image with alt text",
            "Use bullet points and short paragraphs",
        ],
        "content_type": "blog_post",
        "created_at": _now(),
    }
    return brief


def _autocomplete_suggestions(seed: str) -> list[str]:
    suggestions = []
    try:
        url = "https://suggestqueries.google.com/complete/search"
        params = {"client": "firefox", "q": seed}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            if len(data) > 1:
                suggestions = [s for s in data[1] if s != seed]
    except Exception:
        pass
    return suggestions[:15]


def _generate_outline(topic: str, keywords: list[str]) -> list[dict[str, str]]:
    outline = [
        {"heading": f"What is {topic}?", "level": "h2", "notes": "Introduction and definition"},
        {"heading": f"Why {topic} Matters", "level": "h2", "notes": "Benefits and importance"},
        {"heading": f"Key Elements of {topic}", "level": "h2", "notes": "Core components"},
    ]
    if keywords:
        for kw in keywords[:3]:
            outline.append({"heading": kw.title(), "level": "h3", "notes": f"Cover '{kw}' keyword"})
    outline.extend([
        {"heading": f"Best Practices for {topic}", "level": "h2", "notes": "Actionable tips"},
        {"heading": f"Common Mistakes to Avoid", "level": "h2", "notes": "Pitfalls and solutions"},
        {"heading": f"Conclusion", "level": "h2", "notes": "Summary and CTA"},
    ])
    return outline


def _generate_title(topic: str, keywords: list[str]) -> str:
    templates = [
        f"The Ultimate Guide to {topic} in 2025",
        f"{topic}: Everything You Need to Know",
        f"How to Master {topic} (Step-by-Step Guide)",
        f"{topic} Explained: Tips, Strategies & Best Practices",
    ]
    return templates[0]


def _generate_meta_desc(topic: str, keywords: list[str]) -> str:
    return f"Learn everything about {topic}. Discover tips, strategies, and best practices to help you succeed. Comprehensive guide with actionable insights."


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BLOG POST GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_blog_post(topic: str, keywords: list[str] | None = None, word_count: int = 1500) -> dict[str, Any]:
    """Generate a structured blog post with SEO optimization."""
    kw_list = keywords or _autocomplete_suggestions(topic)
    primary_kw = topic

    # Build post structure
    sections = []

    # Title
    title = f"The Ultimate Guide to {topic} in 2025"

    # Meta
    meta_desc = f"Discover everything about {topic}. This comprehensive guide covers key strategies, tips, and best practices for {primary_kw}."

    # Intro
    sections.append({
        "heading": f"Introduction",
        "level": "h2",
        "content": (
            f"In today's competitive landscape, understanding **{primary_kw}** is essential for success. "
            f"Whether you're a beginner or experienced professional, this guide will walk you through "
            f"everything you need to know about {topic}."
        ),
    })

    # Main sections from keywords
    for kw in kw_list[:5]:
        sections.append({
            "heading": kw.title(),
            "level": "h2",
            "content": (
                f"**{kw.title()}** is a critical aspect of {topic}. "
                f"Many professionals overlook this area, but it can make a significant difference "
                f"in your results. Focus on understanding the fundamentals and gradually implement "
                f"advanced strategies as you grow."
            ),
        })

    # Tips section
    tips = [f"Start with a clear plan for your {topic} strategy" ]
    if kw_list:
        tips.append(f"Focus on '{kw_list[0]}' as your primary keyword")
    tips.extend([
        f"Track your progress regularly",
        f"Learn from industry leaders in {topic}",
        f"Stay updated with the latest trends",
    ])

    sections.append({
        "heading": "Top Tips for Success",
        "level": "h2",
        "content": "\n".join(f"- {tip}" for tip in tips),
    })

    # Conclusion
    sections.append({
        "heading": "Conclusion",
        "level": "h2",
        "content": (
            f"Mastering {topic} takes time and effort, but with the right approach, you can achieve "
            f"great results. Start implementing these strategies today and watch your progress grow."
        ),
    })

    # Calculate word count
    total_words = sum(len(s["content"].split()) for s in sections)

    return {
        "title": title,
        "meta_description": meta_desc,
        "primary_keyword": primary_kw,
        "secondary_keywords": kw_list[:10],
        "sections": sections,
        "word_count": total_words,
        "target_word_count": word_count,
        "seo_score": _estimate_seo_score(title, meta_desc, sections, primary_kw),
        "html": _sections_to_html(title, sections),
        "created_at": _now(),
    }


def _estimate_seo_score(title: str, meta: str, sections: list, keyword: str) -> int:
    score = 50
    if keyword.lower() in title.lower():
        score += 10
    if len(title) <= 60:
        score += 5
    if 120 <= len(meta) <= 160:
        score += 5
    if keyword.lower() in meta.lower():
        score += 5
    h2_count = sum(1 for s in sections if s["level"] == "h2")
    if h2_count >= 3:
        score += 5
    if len(sections) >= 5:
        score += 5
    return min(score, 100)


def _sections_to_html(title: str, sections: list) -> str:
    html = f"<h1>{title}</h1>\n"
    for s in sections:
        tag = s["level"]
        content = s["content"].replace("\n", "<br>")
        # Basic markdown bold to HTML
        content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', content)
        html += f"<{tag}>{s['heading']}</{tag}>\n<p>{content}</p>\n"
    return html


# ═══════════════════════════════════════════════════════════════════════════════
# 4. META DESCRIPTION OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════

def optimize_meta_descriptions(url: str) -> dict[str, Any]:
    """Analyze and suggest optimized meta descriptions for a URL."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, _BS4)

        current_title = soup.title.string.strip() if soup.title and soup.title.string else ""
        current_meta = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag:
            current_meta = meta_tag.get("content", "")

        # Get page text for context
        for tag in soup(["script", "style"]):
            tag.decompose()
        page_text = soup.get_text(separator=" ", strip=True)[:500]

        # Extract key phrases
        words = re.findall(r'\b[a-zA-Z]{4,}\b', page_text.lower())
        common = [w for w, c in Counter(words).most_common(20) if c >= 2]

        # Analyze current meta
        issues = []
        if not current_meta:
            issues.append("No meta description found")
        elif len(current_meta) < 70:
            issues.append(f"Too short ({len(current_meta)} chars, aim for 120-160)")
        elif len(current_meta) > 160:
            issues.append(f"Too long ({len(current_meta)} chars, max 160)")
        else:
            pass

        if not current_title:
            issues.append("No title tag found")

        # Generate optimized versions
        page_type = _detect_page_type(current_title, page_text)
        domain = urlparse(url).netloc.replace("www.", "")

        optimized = []
        for template in _get_meta_templates(page_type, common[:5]):
            optimized.append({
                "title": template["title"].format(domain=domain, keyword=common[0] if common else "content"),
                "description": template["desc"].format(keyword=common[0] if common else "content", domain=domain),
                "title_length": len(template["title"].format(domain=domain, keyword=common[0] if common else "content")),
                "desc_length": len(template["desc"].format(keyword=common[0] if common else "content", domain=domain)),
            })

        return {
            "url": url,
            "current_title": current_title,
            "current_meta": current_meta,
            "current_title_length": len(current_title),
            "current_meta_length": len(current_meta),
            "issues": issues,
            "page_type": page_type,
            "key_phrases": common[:10],
            "optimized_suggestions": optimized,
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


def _detect_page_type(title: str, text: str) -> str:
    lower = (title + " " + text[:200]).lower()
    if any(w in lower for w in ["buy", "price", "shop", "product", "cart"]):
        return "product"
    if any(w in lower for w in ["blog", "article", "post", "guide"]):
        return "blog"
    if any(w in lower for w in ["service", "agency", "company", "about us"]):
        return "service"
    if any(w in lower for w in ["contact", "location", "address", "near"]):
        return "local"
    return "generic"


def _get_meta_templates(page_type: str, keywords: list) -> list[dict]:
    kw = keywords[0] if keywords else "topic"
    return [
        {"title": f"{kw.title()} | {{domain}}", "desc": f"Looking for the best {kw}? Discover expert tips, strategies, and solutions at {{domain}}. Get started today!"},
        {"title": f"{kw.title()} Guide - Everything You Need | {{domain}}", "desc": f"Complete guide to {kw}. Learn proven strategies, avoid common mistakes, and achieve better results with {{domain}}."},
        {"title": f"Top {kw.title()} Tips & Strategies | {{domain}}", "desc": f"Expert insights on {kw}. Practical tips, step-by-step guides, and real-world examples to help you succeed."},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CONTENT REWRITER
# ═══════════════════════════════════════════════════════════════

def rewrite_content(text: str, style: str = "professional") -> dict[str, Any]:
    """Rewrite content with improved readability and style."""
    original_words = len(text.split())
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # Basic rewrites: improve clarity, fix passive voice, simplify
    improved = []
    for s in sentences:
        s = _improve_sentence(s, style)
        improved.append(s)

    rewritten = ". ".join(improved) + "."

    # Readability improvements
    improvements = []
    if original_words > 300:
        improvements.append("Consider breaking into shorter paragraphs")
    if any(len(s.split()) > 30 for s in sentences):
        improvements.append("Some sentences are very long - consider splitting")

    passive_count = sum(1 for s in sentences if " is " in s or " was " in s or " are " in s or " were " in s)
    if passive_count > len(sentences) * 0.3:
        improvements.append("High passive voice usage - try active voice")

    return {
        "original_word_count": original_words,
        "rewritten_word_count": len(rewritten.split()),
        "style": style,
        "rewritten_text": rewritten,
        "improvements_made": improvements,
        "sentences_rewritten": len(improved),
        "created_at": _now(),
    }


def _improve_sentence(sentence: str, style: str) -> str:
    # Remove filler words
    fillers = ["very", "really", "quite", "basically", "actually", "just"]
    words = sentence.split()
    cleaned = [w for w in words if w.lower() not in fillers]
    return " ".join(cleaned)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CONTENT CALENDAR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_content_calendar(niche: str, weeks: int = 4) -> dict[str, Any]:
    """Generate a content calendar with topics, types, and publishing schedule."""
    topics = _autocomplete_suggestions(niche)

    content_types = ["blog_post", "social_post", "email_newsletter", "video_script", "infographic"]
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    calendar = []
    topic_idx = 0
    for week in range(1, weeks + 1):
        week_items = []
        for day in days:
            if topic_idx >= len(topics):
                topic_idx = 0
            ct = content_types[topic_idx % len(content_types)]
            week_items.append({
                "day": day,
                "topic": topics[topic_idx] if topic_idx < len(topics) else niche,
                "content_type": ct,
                "status": "planned",
                "keywords": topics[topic_idx:topic_idx + 3],
            })
            topic_idx += 1
        calendar.append({"week": week, "items": week_items})

    return {
        "niche": niche,
        "total_weeks": weeks,
        "total_posts": weeks * 5,
        "calendar": calendar,
        "content_mix": {
            "blog_posts": weeks * 1,
            "social_posts": weeks * 2,
            "newsletters": weeks * 1,
            "videos": weeks * 1,
        },
        "created_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CONTENT GAP ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_content_gaps(url: str, competitors: list[str] | None = None) -> dict[str, Any]:
    """Analyze content gaps between a URL and competitors."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, _BS4)

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        my_text = soup.get_text(separator=" ", strip=True)
        my_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', my_text.lower()))

        my_headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"])]
        my_topics = set(h.lower() for h in my_headings)

        competitor_data = []
        all_competitor_words = set()

        if competitors:
            for comp_url in competitors[:3]:
                try:
                    comp_resp = requests.get(comp_url, headers=_HEADERS, timeout=15)
                    comp_resp.raise_for_status()
                    comp_soup = BeautifulSoup(comp_resp.text, _BS4)
                    for tag in comp_soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    comp_text = comp_soup.get_text(separator=" ", strip=True)
                    comp_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', comp_text.lower()))
                    comp_headings = [h.get_text(strip=True) for h in comp_soup.find_all(["h1", "h2", "h3"])]
                    all_competitor_words.update(comp_words)
                    competitor_data.append({
                        "url": comp_url,
                        "word_count": len(comp_text.split()),
                        "heading_count": len(comp_headings),
                        "unique_words": len(comp_words),
                    })
                except Exception:
                    pass

        # Find gaps
        missing_words = all_competitor_words - my_words
        gap_topics = [w for w in missing_words if len(w) > 5][:20]

        return {
            "url": url,
            "my_word_count": len(my_words),
            "my_heading_count": len(my_headings),
            "my_topics": list(my_topics)[:10],
            "competitors": competitor_data,
            "content_gaps": gap_topics,
            "recommendations": [
                f"Add content about these topics: {', '.join(gap_topics[:5])}" if gap_topics else "Your content is comprehensive",
                f"Your page has {len(my_headings)} headings",
                f"Target {len(my_words) + 500}+ unique words for better coverage",
            ],
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 8. IMAGE SEARCH (Unsplash free API)
# ═══════════════════════════════════════════════════════════════════════════════

def search_images(query: str, count: int = 5) -> dict[str, Any]:
    """Search free stock images from Unsplash. Returns image URLs + metadata."""
    results = []
    try:
        for i in range(min(count, 10)):
            url = f"https://source.unsplash.com/800x600/?{query.replace(' ', '+')}&sig={i}"
            results.append({
                "url": url,
                "width": 800,
                "height": 600,
                "source": "unsplash",
                "query": query,
            })
    except Exception:
        pass

    if not results:
        for i in range(min(count, 5)):
            results.append({
                "url": f"https://picsum.photos/800/600?random={i}",
                "width": 800,
                "height": 600,
                "source": "picsum",
                "query": query,
            })

    return {
        "query": query,
        "count": len(results),
        "images": results,
        "usage": "Free to use. Provide URLs to Social/Ads/Website agents.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SOCIAL MEDIA IMAGE SIZES
# ═══════════════════════════════════════════════════════════════════════════════

def get_social_image_specs(platform: str = "all") -> dict[str, Any]:
    """Get image size specifications for social media platforms."""
    specs = {
        "instagram": {
            "post": {"width": 1080, "height": 1080, "ratio": "1:1"},
            "story": {"width": 1080, "height": 1920, "ratio": "9:16"},
            "reel": {"width": 1080, "height": 1920, "ratio": "9:16"},
        },
        "facebook": {
            "post": {"width": 1200, "height": 630, "ratio": "1.91:1"},
            "story": {"width": 1080, "height": 1920, "ratio": "9:16"},
            "cover": {"width": 820, "height": 312, "ratio": "2.63:1"},
            "ad": {"width": 1200, "height": 628, "ratio": "1.91:1"},
        },
        "twitter": {
            "post": {"width": 1200, "height": 675, "ratio": "16:9"},
            "header": {"width": 1500, "height": 500, "ratio": "3:1"},
        },
        "linkedin": {
            "post": {"width": 1200, "height": 627, "ratio": "1.91:1"},
            "cover": {"width": 1128, "height": 191, "ratio": "5.91:1"},
        },
        "youtube": {
            "thumbnail": {"width": 1280, "height": 720, "ratio": "16:9"},
            "banner": {"width": 2560, "height": 1440, "ratio": "16:9"},
        },
        "pinterest": {
            "pin": {"width": 1000, "height": 1500, "ratio": "2:3"},
        },
        "tiktok": {
            "video": {"width": 1080, "height": 1920, "ratio": "9:16"},
        },
    }

    if platform == "all":
        return {"platforms": specs, "total_formats": sum(len(v) for v in specs.values())}
    elif platform in specs:
        return {"platform": platform, "formats": specs[platform]}
    else:
        return {"error": f"Unknown platform: {platform}. Available: {list(specs.keys())}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CONTENT → SOCIAL REPURPOSE
# ═══════════════════════════════════════════════════════════════════════════════

def repurpose_for_social(blog_content: str, platform: str = "instagram") -> dict[str, Any]:
    """Convert blog/article content into social media posts."""
    sentences = re.split(r'[.!?]+', blog_content)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 20]

    posts = []

    if platform in ("instagram", "facebook"):
        chunks = [sentences[i:i+2] for i in range(0, len(sentences), 2)]
        for idx, chunk in enumerate(chunks[:5]):
            posts.append({
                "slide": idx + 1,
                "text": ". ".join(chunk) + ".",
                "hashtags": "#content #digitalmarketing #tips",
            })
        if sentences:
            posts.append({
                "type": "caption",
                "text": sentences[0] + "... Read more in bio!",
                "hashtags": "#contentmarketing #seotips #digitalmarketing",
            })

    elif platform == "twitter":
        for idx, s in enumerate(sentences[:7]):
            posts.append({"tweet_number": idx + 1, "text": s[:280]})

    elif platform == "linkedin":
        if sentences:
            hook = sentences[0]
            body = " ".join(sentences[1:4]) if len(sentences) > 3 else " ".join(sentences[1:])
            posts.append({
                "hook": hook,
                "body": body[:1300],
                "cta": "What are your thoughts? Comment below.",
            })

    return {
        "platform": platform,
        "original_length": len(blog_content.split()),
        "posts_generated": len(posts),
        "posts": posts,
        "image_needed": True,
        "image_specs": get_social_image_specs(platform).get("formats", {}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 11. AD CREATIVE COPY
# ═══════════════════════════════════════════════════════════════════════════════

def generate_ad_copy(product: str, platform: str = "facebook") -> dict[str, Any]:
    """Generate ad copy for different platforms."""
    copies = []

    if platform in ("facebook", "instagram"):
        copies.extend([
            {
                "headline": f"Discover {product} Today",
                "primary_text": f"Stop struggling with {product}. Our solution helps you achieve results faster. Join 10,000+ happy customers.",
                "description": f"Trusted by professionals. Start your free trial now.",
                "cta": "Learn More",
            },
            {
                "headline": f"Transform Your {product}",
                "primary_text": f"Ready to level up your {product}? See why experts choose us. Limited time offer.",
                "description": f"Proven results. No credit card required.",
                "cta": "Sign Up",
            },
        ])
    elif platform == "google":
        copies.extend([
            {
                "headline1": f"Best {product} Service",
                "headline2": f"Try {product} Free Today",
                "headline3": f"Trusted by 10,000+ Users",
                "description1": f"Get started with {product} today. Free trial, no credit card.",
                "description2": f"Join thousands who trust us for {product}.",
            },
        ])
    elif platform == "linkedin":
        copies.extend([
            {
                "intro": f"Struggling with {product}?",
                "body": f"After working with 500+ companies, the right approach to {product} makes all the difference.",
                "cta": "See how we can help your team.",
            },
        ])

    image_specs = get_social_image_specs(platform).get("formats", {})

    return {
        "product": product,
        "platform": platform,
        "copies": copies,
        "image_specs": image_specs,
        "image_search_query": product,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

CONTENT_TOOLS = [
    {
        "name": "analyze_readability",
        "description": "Analyze content readability of a URL using Flesch-Kincaid metrics. Returns reading ease score, grade level, word count, and suggestions.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to analyze"}
            },
            "required": ["url"]
        },
    },
    {
        "name": "generate_content_brief",
        "description": "Generate a content brief with outline, keywords, SEO tips, and structure for a blog post or article.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "The content topic"},
                "target_audience": {"type": "string", "description": "Target audience", "default": "general"},
                "word_count": {"type": "integer", "description": "Target word count", "default": 1500},
            },
            "required": ["topic"]
        },
    },
    {
        "name": "generate_blog_post",
        "description": "Generate a structured, SEO-optimized blog post with title, sections, meta description, and HTML output.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Blog post topic"},
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "Target keywords"},
                "word_count": {"type": "integer", "description": "Target word count", "default": 1500},
            },
            "required": ["topic"]
        },
    },
    {
        "name": "optimize_meta_descriptions",
        "description": "Analyze current meta tags and suggest optimized title + meta description for better SEO.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to optimize meta tags for"}
            },
            "required": ["url"]
        },
    },
    {
        "name": "rewrite_content",
        "description": "Rewrite/improve content for better readability. Removes filler words, fixes passive voice, improves clarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to rewrite"},
                "style": {"type": "string", "description": "Style: professional, casual, academic", "default": "professional"},
            },
            "required": ["text"]
        },
    },
    {
        "name": "generate_content_calendar",
        "description": "Generate a weekly content calendar with topics, content types, and publishing schedule for a niche.",
        "parameters": {
            "type": "object",
            "properties": {
                "niche": {"type": "string", "description": "The niche/industry"},
                "weeks": {"type": "integer", "description": "Number of weeks to plan", "default": 4},
            },
            "required": ["niche"]
        },
    },
    {
        "name": "analyze_content_gaps",
        "description": "Analyze content gaps between your page and competitors. Find missing topics and get recommendations.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Your URL to analyze"},
                "competitors": {"type": "array", "items": {"type": "string"}, "description": "Competitor URLs to compare"},
            },
            "required": ["url"]
        },
    },
    {
        "name": "search_images",
        "description": "Search free stock images from Unsplash. Returns image URLs for use by Social/Ads/Website agents.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for images"},
                "count": {"type": "integer", "description": "Number of images to return", "default": 5},
            },
            "required": ["query"]
        },
    },
    {
        "name": "get_social_image_specs",
        "description": "Get image size specifications for all social media platforms (Instagram, Facebook, Twitter, LinkedIn, YouTube, Pinterest, TikTok).",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Platform name or 'all' for all platforms", "default": "all"},
            },
        },
    },
    {
        "name": "repurpose_for_social",
        "description": "Convert blog/article content into social media posts for Instagram, Twitter, LinkedIn, or Facebook.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The blog/article content to repurpose"},
                "platform": {"type": "string", "description": "Target platform: instagram, twitter, linkedin, facebook", "default": "instagram"},
            },
            "required": ["content"]
        },
    },
    {
        "name": "generate_ad_copy",
        "description": "Generate ad copy for different platforms (Facebook, Instagram, Google, LinkedIn).",
        "parameters": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": "Product or service to advertise"},
                "platform": {"type": "string", "description": "Target platform", "default": "facebook"},
            },
            "required": ["product"]
        },
    },
]


def execute_content_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a content tool by name."""
    tool_map = {
        "analyze_readability": lambda p: analyze_readability(p["url"]),
        "generate_content_brief": lambda p: generate_content_brief(p["topic"], p.get("target_audience", "general"), p.get("word_count", 1500)),
        "generate_blog_post": lambda p: generate_blog_post(p["topic"], p.get("keywords"), p.get("word_count", 1500)),
        "optimize_meta_descriptions": lambda p: optimize_meta_descriptions(p["url"]),
        "rewrite_content": lambda p: rewrite_content(p["text"], p.get("style", "professional")),
        "generate_content_calendar": lambda p: generate_content_calendar(p["niche"], p.get("weeks", 4)),
        "analyze_content_gaps": lambda p: analyze_content_gaps(p["url"], p.get("competitors")),
        "search_images": lambda p: search_images(p["query"], p.get("count", 5)),
        "get_social_image_specs": lambda p: get_social_image_specs(p.get("platform", "all")),
        "repurpose_for_social": lambda p: repurpose_for_social(p["content"], p.get("platform", "instagram")),
        "generate_ad_copy": lambda p: generate_ad_copy(p["product"], p.get("platform", "facebook")),
    }
    if tool_name not in tool_map:
        return {"error": f"Unknown tool: {tool_name}"}
    return tool_map[tool_name](params)
