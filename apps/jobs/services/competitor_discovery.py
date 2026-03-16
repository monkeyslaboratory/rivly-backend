"""Discover competitors using Claude AI."""
import os
import json
import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))

DISCOVERY_PROMPT = """Given this product, suggest 5 direct competitors.

Product: {name}
URL: {url}
Description: {description}
Industry: {industry}

Return ONLY valid JSON array:
[
  {{"name": "Competitor Name", "url": "https://competitor.com", "description": "What they do", "relevance_score": 85}}
]

Rules:
- Only real, existing companies with working URLs
- relevance_score 0-100 (how directly they compete)
- Sort by relevance_score descending
- Return exactly 5 competitors
- URLs must be real homepage URLs"""


def discover_competitors(product_meta: dict) -> list:
    """Use Claude to discover competitors for a product."""
    try:
        prompt = DISCOVERY_PROMPT.format(
            name=product_meta.get('name', ''),
            url=product_meta.get('url', ''),
            description=product_meta.get('description', ''),
            industry=product_meta.get('industry', ''),
        )

        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1000,
            messages=[{'role': 'user', 'content': prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        competitors = json.loads(text)
        return competitors

    except Exception as e:
        logger.error(f"Competitor discovery failed: {e}")
        return []
