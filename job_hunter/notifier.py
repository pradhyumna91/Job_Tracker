"""Desktop and terminal notifications for new job listings."""

import subprocess
import platform
import logging

logger = logging.getLogger("job_hunter.notifier")


def send_desktop_notification(title: str, message: str):
    """Send a macOS desktop notification."""
    system = platform.system()
    try:
        if system == "Darwin":
            # macOS native notification
            script = f'display notification "{message}" with title "{title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=True, timeout=5)
        elif system == "Linux":
            subprocess.run(["notify-send", title, message], check=True, timeout=5)
        else:
            logger.info(f"Desktop notification not supported on {system}")
    except Exception as e:
        logger.warning(f"Could not send desktop notification: {e}")


def notify_new_jobs(new_jobs: list[dict]):
    """Send notifications for newly discovered jobs."""
    if not new_jobs:
        return

    count = len(new_jobs)
    title = f"Job Hunter: {count} New Listing{'s' if count > 1 else ''}!"

    # Desktop notification with summary
    top_3 = new_jobs[:3]
    lines = []
    for job in top_3:
        sponsor_badge = " [H1B]" if job.get("is_h1b_sponsor") else ""
        lines.append(f"- {job['title']} @ {job['company']}{sponsor_badge}")
    if count > 3:
        lines.append(f"  ...and {count - 3} more")

    message = "\n".join(lines)
    send_desktop_notification(title, message)
    logger.info(f"Notified about {count} new jobs")
