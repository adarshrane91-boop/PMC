"""Drafts a ready-to-act brief for one ward's clustered reports: department,
proposed action, rough cost estimate, timeline, budget-feasibility flag.

Uses llm_provider (Gemini or Claude, whichever key is set) and never raises
to the caller - returns None (not a fabricated brief) when no provider is
available or the call fails. Callers must show "brief unavailable", not
silently invent numbers.

The model is told explicitly that cost/timeline are ballpark estimates for
a human to check, not a quote - see the prompt below. Real cost/timeline
figures should come from the department's own rate card, not an LLM guess.
"""
import json
import logging
from dataclasses import dataclass
from typing import List, Optional

import llm_provider

logger = logging.getLogger(__name__)


@dataclass
class WardBrief:
    department: str
    action: str
    cost_estimate: str
    timeline: str
    budget_feasibility: str


def generate_brief(ward_name: str, reports: List[dict]) -> Optional[WardBrief]:
    if not llm_provider.available() or not reports:
        return None
    posts_block = "\n".join(f'- ({r["category"]}, {r["source"]}) {r["text"]}' for r in reports[:20])
    prompt = f"""You are drafting an internal civic-issue brief for a PMC (Pune Municipal Corporation) ward officer, based on citizen posts clustered to ward "{ward_name}".

Citizen posts this week:
{posts_block}

Write a short, practical brief a ward officer could act on. Be conservative and mark estimates as estimates - you do not have the department's real rate card or crew schedule.

Reply with ONLY a JSON object, no markdown fences:
{{"department": "<which PMC department(s) should handle this>",
  "action": "<one concrete recommended action, one sentence>",
  "cost_estimate": "<rough cost in INR lakhs, labelled as an estimate>",
  "timeline": "<rough number of days, labelled as an estimate>",
  "budget_feasibility": "<one short phrase, e.g. 'likely within routine ward allocation' or 'likely needs supplementary approval'>"}}"""
    raw = llm_provider.complete(prompt, max_tokens=600)
    if raw is None:
        return None
    try:
        d = json.loads(llm_provider.strip_json_fences(raw))
        return WardBrief(
            department=d.get("department", "Unknown"),
            action=d.get("action", ""),
            cost_estimate=d.get("cost_estimate", ""),
            timeline=d.get("timeline", ""),
            budget_feasibility=d.get("budget_feasibility", ""),
        )
    except Exception:
        logger.exception("Could not parse LLM brief response: %r", raw)
        return None