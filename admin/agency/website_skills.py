"""Website Agent Skills — Website's OWN brain, loaded from its repo-local folder.

Website is the design/frontend/deploy agent. Its skills live in
admin/agency/website_skills_repo/ (copied from the domain catalog). Repo-local so
it deploys to AWS with the agent.

Covers design direction, frontend frameworks (React/Next), UI systems, copy for
sites, domain ideas, and web testing.
"""

from __future__ import annotations

import logging

from .agent_skill_loader import (
    detect_agent_skills,
    build_agent_skill_context,
    list_agent_skills,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "website"

WEBSITE_SKILL_REGISTRY: list[dict] = [
    {
        "name": "frontend-design",
        "keywords": [
            "design", "layout", "visual", "typography", "aesthetic",
            "look and feel", "beautiful", "modern design", "clean design",
            "landing page design", "website design", "ui design",
        ],
        "description": "Distinctive, intentional visual design — aesthetic direction, typography, non-templated choices",
    },
    {
        "name": "frontend-design-direction",
        "keywords": [
            "design direction", "brand feel", "design language",
            "product ui", "dashboard design", "app design",
        ],
        "description": "Set a product-specific frontend design direction for production UI work",
    },
    {
        "name": "design-taste-frontend",
        "keywords": [
            "redesign", "portfolio", "anti-slop", "templated", "unique design",
            "stand out", "modern website", "fresh look",
        ],
        "description": "Anti-slop frontend design for landing pages, portfolios, and redesigns",
    },
    {
        "name": "hallmark",
        "keywords": [
            "slop", "greenfield", "design audit", "design extraction",
            "make it premium", "high-end", "signature style",
        ],
        "description": "Anti-AI-slop design system for greenfield pages, audits, redesigns, and style extraction",
    },
    {
        "name": "impeccable",
        "keywords": [
            "polish", "refine", "audit ui", "ux review", "improve ui",
            "harden", "polish interface", "visual hierarchy", "micro-interaction",
        ],
        "description": "Design/redesign/audit/polish frontend interfaces — UX, hierarchy, motion, accessibility",
    },
    {
        "name": "design-system",
        "keywords": [
            "design system", "design tokens", "component system",
            "visual consistency", "design review", "styling audit",
        ],
        "description": "Generate or audit design systems and check visual consistency",
    },
    {
        "name": "ui-ux-pro-max",
        "keywords": [
            "color palette", "font pairing", "ux guidelines", "chart types",
            "gsap motion", "animation preset", "ui style", "ui intelligence",
        ],
        "description": "UI/UX design intelligence — styles, color palettes, font pairings, UX guidelines, motion presets",
    },
    {
        "name": "theme-factory",
        "keywords": [
            "theme", "theming", "color scheme", "brand colors",
            "style artifact", "slide theme", "report theme",
        ],
        "description": "Style artifacts with a theme — 10 pre-set themes with colors/fonts or generate on-the-fly",
    },
    {
        "name": "nextjs-developer",
        "keywords": [
            "nextjs", "next.js", "app router", "server components",
            "rsc", "vercel deploy", "ssr", "middleware", "route handler",
        ],
        "description": "Build Next.js 14+ apps — App Router, server components, server actions, Vercel deploy",
    },
    {
        "name": "react-expert",
        "keywords": [
            "react", "react.js", "reactjs", "hooks", "usestate",
            "component", "jsx", "tsx", "state management", "suspense",
        ],
        "description": "Build React 18+ apps — components, custom hooks, state, performance",
    },
    {
        "name": "senior-frontend",
        "keywords": [
            "tailwind", "typescript", "frontend performance", "bundle size",
            "accessibility", "responsive", "scaffold", "optimize frontend",
        ],
        "description": "Senior frontend — React/Next/TypeScript/Tailwind, performance, a11y, scaffolding",
    },
    {
        "name": "react-best-practices",
        "keywords": [
            "react best practices", "react patterns", "tsx best practices",
            "component structure", "react performance",
        ],
        "description": "Reading/writing React components with best practices",
    },
    {
        "name": "ui-design-system",
        "keywords": [
            "shadcn", "radix", "tailwindcss", "component library",
            "accessible components", "dark mode", "design tokens",
        ],
        "description": "React UI component systems — TailwindCSS + Radix + shadcn/ui",
    },
    {
        "name": "web-design-guidelines",
        "keywords": [
            "web guidelines", "wcag", "aria", "contrast", "responsive check",
            "web interface guidelines", "check accessibility",
        ],
        "description": "Review UI against web interface guidelines — accessibility, responsive, UX best practices",
    },
    {
        "name": "web-design-reviewer",
        "keywords": [
            "review website", "check the ui", "fix the layout",
            "find design problems", "visual inspection", "layout breakage",
        ],
        "description": "Visually inspect sites to find and fix design issues at the source code level",
    },
    {
        "name": "landing-page-copywriter",
        "keywords": [
            "headline", "hero copy", "cta", "value proposition",
            "landing copy", "sales page", "conversion copy",
            "page sections", "website copy",
        ],
        "description": "High-converting landing page copy — headlines, value props, CTAs, section copy",
    },
    {
        "name": "domain-name-brainstormer",
        "keywords": [
            "domain", "domain name", "tld", ".com", ".io", ".dev", ".ai",
            "domain ideas", "url", "website address",
        ],
        "description": "Generate creative domain name ideas and check availability across TLDs",
    },
    {
        "name": "webapp-testing",
        "keywords": [
            "test website", "verify page", "browser test", "playwright",
            "local app test", "capture screenshot", "debug ui",
        ],
        "description": "Interact with and test web apps via Playwright — verify frontend, capture screenshots, debug",
    },
]


def detect_skills(message: str, max_skills: int = 2) -> list[dict]:
    """Detect relevant Website skills from a message (loaded from website_skills_repo/)."""
    return detect_agent_skills(AGENT_NAME, message, WEBSITE_SKILL_REGISTRY, max_skills=max_skills)


def build_skill_context(skills: list[dict]) -> str:
    """Build the Website skill context block."""
    return build_agent_skill_context(skills)


def list_website_skills() -> list[dict]:
    """List Website's own skills (without loading content)."""
    return list_agent_skills(AGENT_NAME, WEBSITE_SKILL_REGISTRY)
