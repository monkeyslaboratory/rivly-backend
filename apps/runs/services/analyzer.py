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

ANALYSIS_PROMPT = """You are a **Senior UX/CX Research Lead** conducting a comprehensive competitive intelligence audit. You have 15+ years analyzing SaaS products for companies like Stripe, Linear, and Figma.

---

## Context

**Competitor:** {competitor_name} ({competitor_url})
**Page:** {page_name} ({device_type} viewport)

**Page content (extracted text):**
```
{dom_text}
```

---

## Your Mission

Conduct a **comprehensive competitive audit** of this page. You are NOT just evaluating UX — you are mapping the competitor's entire business strategy as visible through their product/marketing surface.

### Analyze ALL of the following (based on what's visible on this page):

**1. Core Value Proposition**
- What problem do they solve? For whom?
- How clearly is it communicated? How many seconds to understand?
- What's their unique angle vs. generic positioning?

**2. Conversion Architecture**
- Primary CTA: what is it, where is it, how compelling?
- Secondary CTAs: what alternative paths exist?
- Friction points: how many steps/fields to convert?
- Free trial vs. demo vs. freemium — what model?

**3. Pricing & Monetization (if visible)**
- Pricing tiers, anchor pricing, annual discounts
- Free tier limitations
- Enterprise/custom pricing signals
- Compared to market — cheap, mid, premium?

**4. Trust & Social Proof**
- Customer logos, testimonials, case studies
- Numbers (users, companies, revenue)
- Awards, certifications, security badges
- Press mentions, integrations displayed

**5. Content & Engagement Strategy**
- Blog, resources, guides, webinars
- Lead magnets, email capture
- Community, forum, social links
- Documentation quality signals

**6. Special Programs & Growth Levers**
- Loyalty programs, referral programs
- Promotions, seasonal offers, discounts
- Partner/affiliate programs
- Startup/education programs

**7. Product Capabilities (as communicated)**
- Feature grid, product tour
- Integrations ecosystem
- API/developer offering
- Mobile app availability

**8. UX Quality Assessment**
- Visual hierarchy, typography, whitespace
- Navigation clarity, information architecture
- Performance perception (above-the-fold content)
- Mobile readiness, accessibility signals
- Dark/light mode, personalization

### Scoring (0-100 per dimension):
- **90-100**: Best-in-class, sets industry standard
- **75-89**: Strong execution, competitive advantage
- **60-74**: Adequate, room for improvement
- **40-59**: Below average, losing to competitors
- **0-39**: Poor, needs major overhaul

Return your analysis as JSON:

```json
{{
  "score": <overall weighted score 0-100>,
  "score_breakdown": {{
    "value_proposition": <0-100>,
    "conversion_architecture": <0-100>,
    "trust_and_proof": <0-100>,
    "content_strategy": <0-100>,
    "ux_quality": <0-100>,
    "pricing_transparency": <0-100>,
    "growth_mechanics": <0-100>,
    "product_communication": <0-100>
  }},
  "summary": "<Executive summary: 4-5 sentences. What does this competitor do well? Where do they fall short? What's their strategic positioning? Write as a consulting report opening paragraph.>",
  "key_findings": {{
    "primary_cta": "<Exact CTA text and action>",
    "pricing_model": "<Free/Freemium/Trial/Demo/Contact Sales>",
    "target_audience": "<Who are they targeting based on messaging?>",
    "unique_differentiator": "<What makes them different from competitors?>",
    "loyalty_programs": "<Any referral/loyalty/partner programs found?>",
    "promotions": "<Any active promotions, discounts, or special offers?>",
    "integrations_count": "<Number of integrations mentioned, or 'Not visible'>",
    "social_proof_strength": "<Weak/Medium/Strong — with specifics>"
  }},
  "details": [
    {{
      "observation": "<Specific, evidence-based finding — cite actual UI elements, copy, or patterns>",
      "category": "<value_proposition|conversion|trust|content|ux|pricing|growth|product>",
      "impact": "<How this affects user behavior, conversion, or competitive positioning>",
      "evidence": "<Direct quote or precise description of what you saw>",
      "severity": "critical|high|medium|low"
    }}
  ],
  "recommendations": [
    {{
      "action": "<Specific recommendation to outcompete this player>",
      "rationale": "<Why this matters strategically>",
      "impact": "high|medium|low",
      "effort": "high|medium|low",
      "priority": <1-based priority>
    }}
  ],
  "competitive_position": "<2-3 sentences: How does this competitor position itself in the market? What's their moat? Where are they vulnerable?>"
}}
```

**Rules:**
- Provide 8-12 detailed observations across ALL categories
- Provide 5-8 strategic recommendations
- Every observation MUST cite specific evidence from the page
- Fill ALL key_findings fields (use "Not visible on this page" if not found)
- Be calibrated — don't inflate scores
- Think strategically, not just tactically
- Return ONLY valid JSON"""


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
