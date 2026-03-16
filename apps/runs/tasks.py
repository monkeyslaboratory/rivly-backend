"""
Celery task: execute a full analysis run.
Pipeline: preflight → screenshots → AI analysis → scoring → notify.
Sends real-time progress via Django Channels WebSocket.
"""
import logging
from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.utils import timezone

logger = logging.getLogger(__name__)

channel_layer = get_channel_layer()


def send_ws_event(run_id: str, event_type: str, data: dict):
    """Send event to all WebSocket connections watching this run."""
    try:
        async_to_sync(channel_layer.group_send)(
            f"run_{run_id}",
            {
                "type": event_type.replace(".", "_"),
                **data,
                "timestamp": timezone.now().isoformat(),
            }
        )
    except Exception as e:
        logger.warning(f"WS send failed: {e}")


def update_run_progress(run, status: str, progress: int, phase: str, message: str = ""):
    """Update Run in DB + push WS event."""
    run.status = status
    run.progress = progress
    run.current_phase = phase
    run.save(update_fields=["status", "progress", "current_phase"])

    send_ws_event(str(run.id), "run.progress_updated", {
        "status": status,
        "progress": progress,
        "current_phase": phase,
        "message": message,
    })


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def execute_run(self, run_id: str):
    """
    Main Celery task. Runs the full analysis pipeline.
    """
    from apps.runs.models import Run
    from apps.runs.services.preflight import preflight_check
    from apps.runs.services.screenshot import screenshot_competitor
    from apps.runs.services.analyzer import analyze_competitor_page
    from apps.runs.services.scorer import calculate_overall_scores

    run = Run.objects.select_related("job").prefetch_related("job__competitors").get(id=run_id)
    competitors = list(run.job.competitors.all())

    try:
        run.started_at = timezone.now()
        update_run_progress(run, "preflight", 0, "preflight", "Checking competitor accessibility...")

        # ═══ PHASE 1: PREFLIGHT ═══
        preflight_results = preflight_check(run, competitors)

        accessible = []
        failed = []
        for result in preflight_results:
            if result.success:
                accessible.append(result.competitor)
            else:
                failed.append(result)
                send_ws_event(str(run.id), "run.competitor_error", {
                    "competitor_id": str(result.competitor.id),
                    "competitor_name": result.competitor.name,
                    "error_type": result.error_type,
                    "error_message": result.error_message,
                    "recoverable": result.recoverable,
                })

        if not accessible:
            update_run_progress(run, "failed", 0, "preflight", "No competitors accessible")
            send_ws_event(str(run.id), "run.failed", {
                "error_message": "All competitors are inaccessible.",
            })
            return

        total_steps = len(accessible) * 2  # screenshot + analysis
        completed_steps = 0

        # ═══ PHASE 2: SCREENSHOTS ═══
        update_run_progress(run, "screenshots", 5, "screenshots", "Starting screenshots...")

        all_screenshots = {}
        for competitor in accessible:
            send_ws_event(str(run.id), "run.competitor_started", {
                "competitor_id": str(competitor.id),
                "competitor_name": competitor.name,
                "competitor_url": competitor.url,
                "phase": "screenshotting",
            })

            screenshots = screenshot_competitor(run, competitor)
            all_screenshots[competitor.id] = screenshots
            completed_steps += 1

            for shot in screenshots:
                if shot.status == "success":
                    send_ws_event(str(run.id), "run.screenshot_taken", {
                        "competitor_id": str(competitor.id),
                        "page_name": shot.page_name,
                        "device_type": shot.device_type,
                        "thumbnail_url": "",
                    })

            success_count = sum(1 for s in screenshots if s.status == 'success')
            progress = int(5 + (completed_steps / total_steps) * 40)
            update_run_progress(run, "screenshots", progress, "screenshots",
                                f"Captured {success_count} pages from {competitor.name}")

        # ═══ PHASE 3: AI ANALYSIS ═══
        update_run_progress(run, "analyzing", 50, "analyzing", "Starting AI analysis...")

        for competitor in accessible:
            send_ws_event(str(run.id), "run.competitor_started", {
                "competitor_id": str(competitor.id),
                "competitor_name": competitor.name,
                "competitor_url": competitor.url,
                "phase": "analyzing",
            })

            comp_screenshots = all_screenshots.get(competitor.id, [])
            successful_shots = [s for s in comp_screenshots if s.status == "success"]

            if not successful_shots:
                send_ws_event(str(run.id), "run.competitor_error", {
                    "competitor_id": str(competitor.id),
                    "competitor_name": competitor.name,
                    "error_type": "no_screenshots",
                    "error_message": "No successful screenshots to analyze",
                    "recoverable": False,
                })
                completed_steps += 1
                continue

            # Analyze EACH page separately (not by area — by page)
            for shot in successful_shots:
                try:
                    analyze_competitor_page(run, competitor, shot, shot.page_name)
                except Exception as e:
                    logger.error(f"Analysis failed for {competitor.name}/{shot.page_name}: {e}")

            completed_steps += 1

            overall_score_val = 0
            reports = run.reports.filter(competitor=competitor)
            if reports.exists():
                scores = [r.score for r in reports if r.score > 0]
                overall_score_val = round(sum(scores) / len(scores)) if scores else 0

            send_ws_event(str(run.id), "run.competitor_completed", {
                "competitor_id": str(competitor.id),
                "competitor_name": competitor.name,
                "overall_score": overall_score_val,
                "screenshot_count": len(successful_shots),
                "top_insight": reports.first().summary[:100] if reports.exists() else "",
            })

            progress = int(50 + (completed_steps / total_steps) * 40)
            update_run_progress(run, "analyzing", progress, "analyzing",
                                f"Analyzed {competitor.name}")

        # ═══ PHASE 4: SCORING ═══
        update_run_progress(run, "scoring", 92, "scoring", "Calculating scores...")
        calculate_overall_scores(run)

        # ═══ PHASE 5: COMPARATIVE ANALYSIS ═══
        update_run_progress(run, "comparing", 95, "comparing", "Generating comparative analysis...")

        from apps.runs.services.comparator import generate_comparison
        try:
            generate_comparison(run)
        except Exception as e:
            logger.error(f"Comparison failed: {e}")

        # ═══ COMPLETE ═══
        run.completed_at = timezone.now()
        run.duration_seconds = int((run.completed_at - run.started_at).total_seconds())

        final_status = "completed" if not failed else "partial"
        update_run_progress(run, final_status, 100, "completed", "Analysis complete!")

        send_ws_event(str(run.id), "run.completed", {
            "status": final_status,
            "duration_seconds": run.duration_seconds,
            "total_competitors": len(competitors),
            "successful_competitors": len(accessible),
            "failed_competitors": len(failed),
            "report_url": f"/api/v1/runs/{run.id}/report/",
        })

    except Exception as e:
        logger.exception(f"Run {run_id} failed: {e}")
        run.error_log = str(e)[:2000]
        run.completed_at = timezone.now()
        if run.started_at:
            run.duration_seconds = int((run.completed_at - run.started_at).total_seconds())
        run.save(update_fields=["error_log", "completed_at", "duration_seconds"])

        update_run_progress(run, "failed", 0, "failed", str(e)[:200])
        send_ws_event(str(run.id), "run.failed", {
            "error_message": str(e)[:300],
        })

        raise self.retry(exc=e)
