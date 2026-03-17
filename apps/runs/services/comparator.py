"""
Comparative analysis service.
Takes all individual reports from a run and produces cross-competitor comparison.
"""
import os
import json
import logging
from decimal import Decimal
from anthropic import Anthropic

from apps.runs.models import RunComparison, RunReport, RunOverallScore

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

COMPARISON_PROMPT = """You are a Senior CX/UX Research Lead producing a final comparative analysis.

## Context

**Our Product:** {product_name} ({product_url})
**Competitors analyzed:** {competitor_list}

## Individual Audit Summaries

{audit_summaries}

## Your Task

Produce a comprehensive comparative analysis. Your audience is the VP of Product and Design Lead.

Return JSON:

```json
{{
  "executive_summary": "<15-20 bullet points as a single string with newlines. Key findings: what our product does better, what competitors do better, top opportunities.>",

  "feature_matrix": [
    {{
      "category": "<Feature category>",
      "features": [
        {{
          "name": "<Feature name>",
          "our_product": "yes|no|partial",
          "competitors": {{
            "<Comp Name>": "yes|no|partial"
          }}
        }}
      ]
    }}
  ],

  "flow_comparison": [
    {{
      "flow_name": "<Core user flow>",
      "description": "<What this flow achieves>",
      "products": {{
        "<Product Name>": {{
          "steps": <number>,
          "friction_level": "low|medium|high",
          "notable_features": "<What stands out>",
          "pain_points": "<Issues found>"
        }}
      }}
    }}
  ],

  "ux_scorecard": {{
    "dimensions": [
      "Navigation & IA",
      "Visual Design & Consistency",
      "Interaction Design",
      "Content Quality",
      "Onboarding Experience",
      "Error Handling",
      "Performance",
      "Mobile Experience",
      "Accessibility",
      "Trust Signals"
    ],
    "scores": {{
      "<Product Name>": {{
        "Navigation & IA": <1-5>,
        "Visual Design & Consistency": <1-5>,
        ...all 10 dimensions...
      }}
    }}
  }},

  "recommendations": [
    {{
      "finding": "<What was discovered>",
      "evidence": "<Specific examples from competitor analysis>",
      "impact": "<How this affects users and business>",
      "priority": "critical|high|medium|low",
      "recommendation": "<Specific action to take>",
      "reference_competitor": "<Which competitor does this well>"
    }}
  ],

  "competitive_position": "<3-5 sentences on our product's market position relative to competitors>"
}}
```

Rules:
- Feature matrix: 15-25 features across 4-6 categories
- Flow comparison: 3-5 core flows relevant to the niche
- Recommendations: 8-12 items, ordered by priority
- Be brutally honest — if competitors are better, say so
- Return ONLY valid JSON

**IMPORTANT: Write ALL text content (executive_summary, descriptions, recommendations, findings, competitive_position) in {language}. JSON keys and enum values (yes/no/partial, high/medium/low) must remain in English.**"""


def generate_comparison(run):
    """Generate comparative analysis from all individual reports."""
    job = run.job
    product_name = job.name.split(' vs ')[0] if ' vs ' in job.name else job.name
    product_url = job.product_url

    # Gather all reports grouped by competitor
    competitors = list(job.competitors.all())
    competitor_list = ", ".join([f"{c.name} ({c.url})" for c in competitors])

    # Build audit summaries
    summaries = []
    for comp in competitors:
        reports = RunReport.objects.filter(run=run, competitor=comp)
        overall = RunOverallScore.objects.filter(run=run, competitor=comp).first()

        comp_summary = f"\n### {comp.name} ({comp.url})\n"
        if overall:
            comp_summary += f"Overall Score: {overall.overall_score}/100\n"

        for report in reports:
            if report.score > 0:
                comp_summary += f"\n**{report.category}** (Score: {report.score}/100)\n"
                comp_summary += f"{report.summary}\n"
                if report.details:
                    for d in report.details[:3]:
                        if isinstance(d, dict):
                            comp_summary += f"- {d.get('observation', '')}\n"

        summaries.append(comp_summary)

    audit_summaries = "\n---\n".join(summaries)

    # Determine user language
    user_locale = 'English'
    if run.triggered_by:
        user_locale_code = getattr(run.triggered_by, 'locale', 'en')
        if user_locale_code == 'ru':
            user_locale = 'Russian'

    prompt = COMPARISON_PROMPT.format(
        product_name=product_name,
        product_url=product_url,
        competitor_list=competitor_list,
        audit_summaries=audit_summaries[:15000],
        language=user_locale,
    )

    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=4000,
            messages=[{'role': 'user', 'content': prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)

        # Update run cost
        cost = (response.usage.input_tokens * 0.003 + response.usage.output_tokens * 0.015) / 1000
        run.cost_api_usd += Decimal(str(cost))
        run.save(update_fields=['cost_api_usd'])

        comparison = RunComparison.objects.create(
            run=run,
            executive_summary=data.get('executive_summary', ''),
            feature_matrix=data.get('feature_matrix', []),
            flow_comparison=data.get('flow_comparison', []),
            ux_scorecard=data.get('ux_scorecard', {}),
            recommendations=data.get('recommendations', []),
            competitive_position=data.get('competitive_position', ''),
        )

        return comparison

    except Exception as e:
        logger.error(f"Comparison generation failed: {e}")
        return RunComparison.objects.create(
            run=run,
            executive_summary=f"Comparison failed: {str(e)[:500]}",
        )
