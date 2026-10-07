#!/usr/bin/env python3
"""Copy the founder skills that matter into the CEO's skills repo.

founder-skills is a third-party repo with 14 skills. Copying all 14 into the
CEO's prompt path would be wasted context: the CEO reasons from a playbook and
injecting a marketing database every tick is expensive and mostly irrelevant.

This copies a deliberate subset, so the choice of what is worth carrying is a
recorded decision rather than a lucky accident. It also normalises the format,
because the upstream SKILL.md files are written for an interactive session
(wait for user input, read references) and the CEO has neither: it is one-shot
and file-restricted.

Run from backend-deploy:
    python copy_founder_skills.py            # dry run
    python copy_founder_skills.py --yes
"""
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "refs" / "founder-skills" / "skills"
DST = ROOT / "admin" / "agency" / "ceo_skills_repo"

# skill name -> which sub-agent it is most useful to, and why it was kept
WANTED = {
    "outreach-specialist": "writes the cold outreach that actually books calls",
    "strategic-planning": "decides what the agency does next, which is the CEO's own job",
    "pricing-strategist": "retainer pricing against appointments, not leads",
    "go-to-market-plan": "how a local-service business is actually entered",
    "lead-magnet-generator": "content asset for clinics that stall on a retainer",
    "competitor-intel": "positions against local agencies and Upwork freelancers",
    "viral-hook-creator": "the first line of an outreach message",
    "landing-page": "SKIPPED on purpose, see note below",
}

# Dropped on purpose:
#   brand-copywriter, linkedin-writer, x-writer  - social content, not the offer
#   cro-optimization, marketing-ideas, prd-generator, sop-creator,
#   product-hunt-launch-plan                      - product work, not service delivery

INTERACTIVE = re.compile(
    r"^\s*(Respond with:|Then wait for the user|proceed immediately with Task Execution"
    r"|Check \$ARGUMENTS first).*$",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    """Make a SKILL.md usable by a one-shot autonomous agent.

    The upstream files assume an interactive session that can ask a question and
    read files on disk. The CEO can do neither, so those instructions are
    replaced with the ones that actually apply to it.
    """
    out = []
    for line in text.splitlines():
        if INTERACTIVE.match(line):
            continue
        out.append(line)
    body = "\n".join(out)

    header = (
        "<!-- founder-skills, adapted for the TAGS Agency CEO. One-shot, "
        "autonomous, no user available to answer a question. Read "
        "FOUNDER_CONTEXT.md for the business. -->\n\n"
    )
    return header + body.strip() + "\n"


def main():
    if not SRC.exists():
        print(f"  source not found: {SRC}")
        print("  clone it first:  git clone --depth 1 "
              "https://github.com/ognjengt/founder-skills.git refs/founder-skills")
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0

    for name, why in WANTED.items():
        src_dir = SRC / name
        if name == "landing-page" or not src_dir.exists():
            print(f"  skip {name:<24} ({'not upstream' if name == 'landing-page' else 'missing'})")
            skipped += 1
            continue

        src_md = src_dir / "SKILL.md"
        if not src_md.exists():
            print(f"  skip {name:<24} (no SKILL.md)")
            skipped += 1
            continue

        dst_dir = DST / name
        if "--yes" not in sys.argv:
            size = sum(f.stat().st_size for f in src_dir.rglob("*") if f.is_file())
            print(f"  would copy {name:<24} {size//1024:>4} KB   {why}")
            copied += 1
            continue

        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)

        # Rewrite the top-level SKILL.md for autonomous use.
        (dst_dir / "SKILL.md").write_text(clean(src_md.read_text(encoding="utf-8")),
                                          encoding="utf-8")

        # References are trimmed to a size the CEO can actually hold. An
        # unbounded reference file is silently ignored, which looks identical to
        # the skill not existing.
        refs = dst_dir / "references"
        if refs.exists():
            for f in sorted(refs.glob("*.md")):
                f.write_text(clean(f.read_text(encoding="utf-8"))[:12000], encoding="utf-8")

        n = len(list(dst_dir.rglob("*.md")))
        print(f"  copied  {name:<24} {n} md files   {why}")
        copied += 1

    if "--yes" not in sys.argv:
        print(f"\n  dry run. {copied} would copy, {skipped} skipped. pass --yes")
    else:
        total = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file())
        print(f"\n  {copied} skills into {DST.name}/  ({total//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
