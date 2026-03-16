"""
Claude AI analysis service.
Sends screenshots + DOM text to Claude API for deep UX/CX analysis.
"""
import os
import base64
import logging
import json
from decimal import Decimal
from pathlib import Path
from anthropic import Anthropic

from apps.runs.models import RunReport

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path(os.environ.get('SCREENSHOTS_DIR', 'media/screenshots'))

client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

ANALYSIS_PROMPT = """You are a **Senior UX/CX Research Lead** with 15+ years of experience conducting competitive audits for top SaaS companies. You've worked with teams at Stripe, Linear, Notion, and Figma. Your analyses are known for being razor-sharp, backed by evidence, and immediately actionable.

You are conducting a professional competitive UX audit.

---

**Target:** {competitor_name}
**URL:** {competitor_url}
**Page analyzed:** {page_name}
**Device viewport:** {device_type}
**Focus area:** {category}

**Extracted page content:**
```
{dom_text}
```

---

## Your Task

Analyze this page as if you're presenting findings to a VP of Product. Be specific — reference actual UI elements, copy, layout patterns, and interaction design you observe. Don't be generic.

### Scoring Criteria (each 0-100):

- **clarity** — How instantly understandable is the value proposition? Can a new visitor grasp what this product does within 5 seconds?
- **visual_hierarchy** — Is there a clear visual flow? Do primary elements dominate? Is there effective use of whitespace, typography scale, and color contrast?
- **cta_effectiveness** — Are CTAs compelling, well-positioned, and friction-free? Is the primary action obvious?
- **content_quality** — Is the copy concise, benefit-driven, and free of jargon? Does it address user pain points?
- **user_flow** — Is navigation intuitive? Can users accomplish key tasks without confusion? Are there dead ends?
- **trust_signals** — Social proof, testimonials, security badges, brand credibility indicators
- **mobile_readiness** — Touch targets, responsive patterns, content priority on smaller viewports
- **accessibility** — Color contrast, semantic structure, keyboard navigability, alt text quality

### Scoring Guidelines:
- **90-100** — Best-in-class, award-worthy execution
- **75-89** — Strong, professional, minor improvements possible
- **60-74** — Solid but notable gaps exist
- **40-59** — Below average, significant issues
- **0-39** — Poor execution, major redesign needed

Return your analysis as a JSON object with this exact structure:

```json
{{
  "score": <overall weighted score 0-100>,
  "score_breakdown": {{
    "clarity": <0-100>,
    "visual_hierarchy": <0-100>,
    "cta_effectiveness": <0-100>,
    "content_quality": <0-100>,
    "user_flow": <0-100>,
    "trust_signals": <0-100>,
    "mobile_readiness": <0-100>,
    "accessibility": <0-100>
  }},
  "summary": "<Executive summary: 3-4 sentences capturing the most critical UX strengths and weaknesses. Write as if this is the opening paragraph of a consulting report.>",
  "details": [
    {{
      "observation": "<Specific, evidence-based observation — reference actual UI elements>",
      "impact": "<How this affects real user behavior and business metrics>",
      "evidence": "<Quote specific copy, describe specific layout patterns, reference exact UI elements>",
      "severity": "critical|high|medium|low"
    }}
  ],
  "recommendations": [
    {{
      "action": "<Specific, implementable recommendation — not vague advice>",
      "rationale": "<Why this matters, with reference to UX best practices or competitor benchmarks>",
      "impact": "high|medium|low",
      "effort": "high|medium|low",
      "priority": <1-based priority ranking>
    }}
  ],
  "competitive_position": "<1-2 sentences on how this page compares to industry best practices and what the competitor does uniquely well or poorly>"
}}
```

**Critical rules:**
- Provide 6-10 detailed observations, ordered by severity
- Provide 4-6 actionable recommendations, ordered by priority
- Every observation MUST reference specific evidence from the page
- Be honest and calibrated — don't inflate scores
- Return ONLY valid JSON, no markdown fences or commentary"""


def analyze_competitor_page(run, competitor, screenshot, category):
    """
    Send a screenshot + DOM text to Claude for analysis.
    Returns a RunReport object.
    """
    messages_content = []

    # Try to include screenshot image
    image_path = None
    for f in SCREENSHOTS_DIR.glob('*.png'):
        if f.stem in screenshot.s3_key:
            image_path = f
            break

    if image_path and image_path.exists():
        with open(image_path, 'rb') as img_file:
            image_data = base64.standard_b64encode(img_file.read()).decode('utf-8')
        messages_content.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': 'image/png',
                'data': image_data,
            }
        })

    prompt_text = ANALYSIS_PROMPT.format(
        competitor_name=competitor.name,
        competitor_url=competitor.url,
        page_name=screenshot.page_name,
        device_type=screenshot.device_type,
        category=category,
        dom_text=screenshot.dom_text[:12000] if screenshot.dom_text else '(no text extracted)',
    )

    messages_content.append({
        'type': 'text',
        'text': prompt_text,
    })

    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=4000,
            messages=[{'role': 'user', 'content': messages_content}],
        )

        response_text = response.content[0].text.strip()

        # Handle potential markdown code blocks
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        analysis = json.loads(response_text)

        # Calculate API cost
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_usd = (input_tokens * 0.003 + output_tokens * 0.015) / 1000

        # Find previous report for diff
        previous_report = RunReport.objects.filter(
            competitor=competitor,
            category=category,
        ).exclude(run=run).order_by('-created_at').first()

        report = RunReport.objects.create(
            run=run,
            competitor=competitor,
            category=category,
            score=analysis.get('score', 0),
            score_breakdown=analysis.get('score_breakdown', {}),
            summary=analysis.get('summary', ''),
            details=analysis.get('details', []),
            recommendations=analysis.get('recommendations', []),
            previous_report=previous_report,
            previous_score=previous_report.score if previous_report else None,
            score_delta=(analysis.get('score', 0) - previous_report.score) if previous_report else None,
        )

        run.cost_api_usd += Decimal(str(cost_usd))
        run.save(update_fields=['cost_api_usd'])

        return report

    except json.JSONDecodeError as e:
        logger.error(f'Failed to parse Claude response: {e}')
        return RunReport.objects.create(
            run=run,
            competitor=competitor,
            category=category,
            score=0,
            summary=f'Analysis failed: could not parse AI response',
            details=[{'observation': 'Parse error', 'impact': str(e), 'evidence': '', 'severity': 'high'}],
        )
    except Exception as e:
        logger.error(f'Claude API error: {e}')
        return RunReport.objects.create(
            run=run,
            competitor=competitor,
            category=category,
            score=0,
            summary=f'Analysis failed: {str(e)[:200]}',
            details=[],
        )
