"""Hosted WISE daily refresh; no interactive or local-report execution path."""

import logging

import azure.functions as func

from wise_endpoint_intelligence import run_hosted_pipeline


app = func.FunctionApp()
logger = logging.getLogger("wise.timer")


@app.function_name(name="WiseDailyRefresh")
@app.timer_trigger(schedule="%WISE_REPORT_SCHEDULE%", arg_name="timer", run_on_startup=False, use_monitor=True)
def daily_refresh(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logger.warning("WISE daily refresh timer is past due; processing the delayed invocation")
    logger.info("WISE timer started: memory-only collection -> Blob verification -> SharePoint publication")
    run_hosted_pipeline()
    logger.info("WISE timer completed successfully")