"""
scheduler.py

Starts a background APScheduler that runs follow-up checks every hour.
Import this module in server.py to activate the scheduler.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from followup_service import check_followups

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
scheduler.add_job(check_followups, 'interval', hours=1, id='followup_check', replace_existing=True)
scheduler.start()

logger.info("✅ Background scheduler started. Follow-up check runs every 1 hour.")
