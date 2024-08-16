from dagster import Definitions
from dagster_dbt import DbtCliResource
from .assets import dbt_staging, dbt_prod
from . import exportapi
from .project import dagsteretl_project
from .schedules import schedules
from dagster import (
    Definitions,
    load_assets_from_modules,
    define_asset_job,
    AssetSelection,
    ScheduleDefinition,
)
from . import assets
from dagster_aws.s3 import S3Resource


all_assets = load_assets_from_modules([assets])
api = load_assets_from_modules([exportapi])
dagster_pipeline = define_asset_job("dagster_pipeline", selection=AssetSelection.all())

daily_schedule = ScheduleDefinition(
    name="Bikes_Pipeline",
    cron_schedule="0 9 * * *",
    job=dagster_pipeline,  # Or use job=my_job for jobs
    run_config={},  # Provide run configuration if needed
    execution_timezone="UTC",
)
defs = Definitions(
    assets=[*all_assets, *api],
    jobs=[dagster_pipeline],
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
