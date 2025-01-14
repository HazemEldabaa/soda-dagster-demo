from dagster import (
    AssetCheckResult,
    AssetKey,
    AssetSelection,
    DagsterEventType,
    Definitions,
    EventRecordsFilter,
    OpExecutionContext,
    RunRequest,
    ScheduleDefinition,
    SensorDefinition,
    SensorEvaluationContext,
    SkipReason,
    define_asset_job,
    job,
    load_assets_from_modules,
    sensor,
)
from dagster_aws.s3 import S3Resource
from dagster_dbt import DbtCliResource, get_asset_key_for_model

from . import assets, exportapi
from .assets import (
    dataset_names,
    dbt_staging,
    load_from_s3,
    process_data,
    reporting,
    s3_files,
    soda_UI_check,
)
from .exportapi import export_report
from .project import dagsteretl_project
from .schedules import schedules

all_assets = load_assets_from_modules([assets])
# api = load_assets_from_modules([exportapi])
staging_pipeline = define_asset_job(
    "staging_pipeline",
    selection=AssetSelection.assets(
        s3_files, load_from_s3, dbt_staging, soda_UI_check, reporting, process_data
    ).required_multi_asset_neighbors(),
)
# prod_pipeline = define_asset_job('prod_pipeline', selection=AssetSelection.assets(get_asset_key_for_model([dbt_prod],'prod_sales_summary'),get_asset_key_for_model([dbt_prod],'prod_product_popularity'), export_report).required_multi_asset_neighbors())


from datetime import datetime

from dagster import (
    AssetKey,
    DagsterEventType,
    EventRecordsFilter,
    RunRequest,
    SkipReason,
)


def soda_check_sensor(context: SensorEvaluationContext):
    # Create an event filter with the required parameters
    event_filter = EventRecordsFilter(
        asset_key=AssetKey(["staging", "t_sales_summary"]),  # Adjust to your asset key
        event_type=DagsterEventType.ASSET_MATERIALIZATION,  # Ensure event type is provided
    )

    # Fetch the event records using the correct filter
    events = context.instance.get_event_records(
        event_records_filter=event_filter,
        limit=1,
    )

    if not events:
        return SkipReason("No recent events found for soda_UI_check")

    # Access the first event log record
    last_event_record = events[0]
    last_event = last_event_record.event_log_entry.dagster_event

    # Since it's an ASSET_MATERIALIZATION event, consider it successful if it exists
    if last_event and last_event.event_type_value == "ASSET_MATERIALIZATION":
        return RunRequest(
            run_key=f"soda_ui_check_{datetime.now().isoformat()}",  # Use current timestamp
            job_name="prod_pipeline",
        )
    else:
        return SkipReason(f"soda_UI_check did not pass at {datetime.now().isoformat()}")


# soda_ui_check_sensor = SensorDefinition(
#     name="soda_ui_check_sensor",
#     job=prod_pipeline,  # Replace with your actual pipeline
#     evaluation_fn=soda_check_sensor,
#     minimum_interval_seconds=21600
# )
daily_schedule = ScheduleDefinition(
    name="Bikes_Pipeline",
    cron_schedule="0 9 * * *",
    job=staging_pipeline,  # Or use job=my_job for jobs
    run_config={},  # Provide run configuration if needed
    execution_timezone="UTC",
)

defs = Definitions(
    assets=[*all_assets],
    asset_checks=[soda_UI_check],
    jobs=[staging_pipeline],
    schedules=[daily_schedule],
    resources={
        "dbt": DbtCliResource(project_dir=dagsteretl_project),
        "s3": S3Resource(
            region_name="your-region",
            aws_access_key_id="your-aws-key",
            aws_secret_access_key="your-aws-secret",
        ),
    },
)
