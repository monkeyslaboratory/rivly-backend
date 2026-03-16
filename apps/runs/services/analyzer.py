"""
Claude AI analysis service.
Sends screenshots + DOM text to Claude API for UX/CX analysis.
"""
import os
import base64
import logging
import json
from pathlib import Path
from anthropic import Anthropic

from apps.runs.models import RunReport

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path(os.environ.get('SCREENSHOTS_DIR', 'media/screenshots'))

client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

ANALYSIS_PROMPT = """You are an expert UX/CX analyst. Analyze this competitor's website page.

**Competitor:** {competitor_name} ({competitor_url})
**Page:** {page_name}
**Device:** {device_type}
**Analysis area:** {category}

**Page text content:**
{dom_text}

Provide your analysis as JSON with this exact structure:
{{
  "score": <integer 0-100>,
  "score_breakdown": {{
    "clarity": <0-100>,
    "visual_hierarchy": <0-100>,
    "cta_effectiveness": <0-100>,
    "content_quality": <0-100>,
    "user_flow": <0-100>
  }},
  "summary": "<2-3 sentence summary of UX quality>",
  "details": [
    {{
      "observation": "<what you observed>",
      "impact": "<impact on user experience>",
      "evidence": "<specific evidence from the page>",
      "severity": "high|medium|low"
    }}
  ],
  "recommendations": [
    {{
      "action": "<specific actionable recommendation>",
      "impact": "high|medium|low",
      "effort": "high|medium|low"
    }}
  ]
}}

Be specific, data-driven, and actionable. Score fairly — 50 is average, 70+ is good, 85+ is excellent.
Return ONLY valid JSON, no markdown or extra text."""


def analyze_competitor_page(run, competitor, screenshot, category):
    """
    Send a screenshot + DOM text to Claude for analysis.
    Returns a RunReport object.
    """
    messages_content = []

    # Try to include screenshot image
    image_path = None
    # Find local file by matching s3_key pattern
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
        dom_text=screenshot.dom_text[:10000] if screenshot.dom_text else '(no text extracted)',
    )

    messages_content.append({
        'type': 'text',
        'text': prompt_text,
    })

    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=2000,
            messages=[{'role': 'user', 'content': messages_content}],
        )

        # Parse response
        response_text = response.content[0].text.strip()

        # Handle potential markdown code blocks
        if response_text.startswith('```'):
            response_text = response_text.split('\n', 1)[1]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        analysis = json.loads(response_text)

        # Calculate API cost (approximate)
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

        # Update run cost
        from decimal import Decimal
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
