from pathlib import Path

from dagster_dbt import DbtProject

dagsteretl_project = DbtProject(
    project_dir=Path(__file__).joinpath("..", "..", "..").resolve(),
    packaged_project_dir=Path(__file__).joinpath("..", "..", "dbt-project").resolve(),
)
dagsteretl_project.prepare_if_dev()
