"""APScheduler 진입점. python -m app.pipelines.scheduler"""

from apscheduler.schedulers.blocking import BlockingScheduler

sched = BlockingScheduler(timezone="Asia/Seoul")
# sched.add_job(daily_batch.run, "cron", day_of_week="mon-fri", hour=15, minute=40)
# sched.add_job(collect_news.run, "interval", minutes=30)

if __name__ == "__main__":
    sched.start()
