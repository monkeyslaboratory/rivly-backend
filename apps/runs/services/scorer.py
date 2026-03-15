"""
Scoring service.
Aggregates individual RunReport scores into RunOverallScore per competitor.
"""
import logging
from django.db.models import Avg

from apps.runs.models import RunReport, RunOverallScore

logger = logging.getLogger(__name__)


def calculate_overall_scores(run):
    """
    Calculate overall UX score per competitor for this run.
    Averages all category scores and picks top insights.
    Returns list of RunOverallScore objects.
    """
    overall_scores = []

    # Group reports by competitor
    competitors = set()
    for report in run.reports.all():
        competitors.add(report.competitor_id)

    for competitor_id in competitors:
        reports = run.reports.filter(competitor_id=competitor_id)
        competitor = reports.first().competitor

        if not reports.exists():
            continue

        # Category scores dict
        category_scores = {}
        for report in reports:
            category_scores[report.category] = report.score

        # Overall = weighted average of all categories
        scores = [r.score for r in reports if r.score > 0]
        overall_score = round(sum(scores) / len(scores)) if scores else 0

        # Top insights from high-severity details
        top_insights = []
        for report in reports.order_by('-score'):
            if report.details:
                for detail in report.details[:2]:
                    if isinstance(detail, dict):
                        insight = detail.get('observation', '')
                        if insight and len(top_insights) < 5:
                            top_insights.append(insight)

        # Find previous overall score
        previous = RunOverallScore.objects.filter(
            competitor=competitor,
        ).exclude(run=run).order_by('-created_at').first()

        obj = RunOverallScore.objects.create(
            run=run,
            competitor=competitor,
            overall_score=overall_score,
            category_scores=category_scores,
            top_insights=top_insights,
            previous_overall_score=previous.overall_score if previous else None,
            score_delta=(overall_score - previous.overall_score) if previous else None,
        )
        overall_scores.append(obj)

    return overall_scores
