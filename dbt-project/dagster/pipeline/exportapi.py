import requests
import sys
import pandas as pd
import time
from dagster import get_dagster_logger, asset, AssetExecutionContext
from . import assets
from datetime import datetime
import psycopg2
from psycopg2 import sql
import os
from dotenv import load_dotenv


# This script will fetch all checks/datasets in Soda and save them in Redshift.
@asset(deps=[assets.dbt_prod], compute_kind="python")
def export_report():

    load_dotenv()

    # Soda Cloud Instance
    soda_cloud_url = "https://demo.soda.io"  # Your Soda Cloud URL - cloud.us.soda.io (US Customers) cloud.soda.io (EU Customers)
    soda_apikey = os.getenv("soda_api_key_id")  # User API key ID from Soda Cloud
    soda_apikey_secret = os.getenv(
        "soda_api_key_secret"
    )  # User API key secret from Soda Cloud

    # The table NAMES you want to save the results to. Use UPPERCASE to avoid using "" in Redshift
    datasets_table = "DATASETS_REPORT"
    checks_table = "CHECKS_REPORT"

    # Redshift connection details
    host = os.getenv("redshift_host")
    port = "5439"
    user = os.getenv("redshift_user")
    password = os.getenv("redshift_password")
    dbname = "dev"
    schema = "demo"

    # Connect to Redshift
    conn = psycopg2.connect(
        dbname=dbname, user=user, password=password, host=host, port=port
    )

    print("Starting SodaAPI to Redshift Tables.....")
    get_dagster_logger().info("Starting SodaAPI to Redshift Tables.....")
    datasets = []
    checks = []

    response_datasets = requests.get(
        soda_cloud_url + "/api/v1/datasets?page=0",
        auth=(soda_apikey, soda_apikey_secret),
    )

    if response_datasets.status_code == 401 or response_datasets.status_code == 403:
        print(
            "Unauthorized or Forbidden access. Please check your API keys and/or permissions in Soda."
        )
        sys.exit()

    # Fetch all Datasets
    if response_datasets.status_code == 200:
        dataset_pages = response_datasets.json().get("totalPages")

        i = 0
        while i < dataset_pages:
            dq_datasets = requests.get(
                soda_cloud_url + "/api/v1/datasets?page=" + str(i),
                auth=(soda_apikey, soda_apikey_secret),
            )

            if dq_datasets.status_code == 200:
                print("Fetching all datasets on page: " + str(i))
                get_dagster_logger().info("Fetching all datasets on page: " + str(i))
                list = dq_datasets.json().get("content")
                datasets.extend(list)
                i += 1
            elif dq_datasets.status_code == 429:
                print(
                    "API Rate Limit reached when fetching datasets on page: "
                    + str(i)
                    + ". Pausing for 30 seconds."
                )
                time.sleep(30)
            else:
                print(
                    "Error fetching datasets on page " + str(i) + ". Status code:",
                    dq_datasets.status_code,
                )
    else:
        print(
            "Error fetching initial datasets. Status code:",
            response_datasets.status_code,
        )
        sys.exit()

    df_datasets = pd.DataFrame(datasets)

    # Clean up df_datasets
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_datasets.insert(0, "record_created", current_time)
    df_datasets["datasource_type"] = df_datasets["datasource"].apply(
        lambda x: x["type"] if x else None
    )
    
    # Drop the "owners" column before inserting
    if "owners" in df_datasets.columns:
        df_datasets.drop(columns=["owners"], inplace=True)
    
    df_datasets.drop(columns=["datasource"], inplace=True)

    # Fetch all Checks
    response_checks = requests.get(
        soda_cloud_url + "/api/v1/checks?size=100",
        auth=(soda_apikey, soda_apikey_secret),
    )

    if response_checks.status_code == 200:
        check_pages = response_checks.json().get("totalPages")

        i = 0
        while i < check_pages:
            dq_checks = requests.get(
                soda_cloud_url + "/api/v1/checks?size=100&page=" + str(i),
                auth=(soda_apikey, soda_apikey_secret),
            )

            if dq_checks.status_code == 200:
                print("Fetching all checks on page " + str(i))
                get_dagster_logger().info("Fetching all checks on page " + str(i))
                check_list = dq_checks.json().get("content")
                checks.extend(check_list)
                i += 1
            elif dq_checks.status_code == 429:
                print(
                    "API Rate Limit reached when fetching checks on page: "
                    + str(i)
                    + ". Pausing for 30 seconds."
                )
                time.sleep(30)
            else:
                print(
                    "Error fetching checks on page " + str(i) + ". Status code:",
                    dq_checks.status_code,
                )
    else:
        print(
            "Error fetching initial checks. Status code:", response_checks.status_code
        )
        sys.exit()

    df_checks = pd.DataFrame(checks)

    # Clean up df_checks
    df_checks.insert(0, "record_created", current_time)
    df_checks["dataset_id"] = df_checks["datasets"].apply(
        lambda x: x[0]["id"] if x else None
    )
    df_checks["dataset_name"] = df_checks["datasets"].apply(
        lambda x: x[0]["name"] if x else None
    )
    df_checks["dataset_url"] = df_checks["datasets"].apply(
        lambda x: x[0]["cloudUrl"] if x else None
    )
    df_checks["lastCheckResultValue"] = df_checks["lastCheckResultValue"].apply(
        lambda x: (
            x.get("value")
            if isinstance(x, dict) and "value" in x
            else (
                x.get("valueLabel") if isinstance(x, dict) and "valueLabel" in x else x
            )
        )
    )
    df_checks["attributes"] = df_checks["attributes"].fillna({})
    df_checks["check_owner"] = df_checks["owner"].apply(
        lambda x: (
            x.get("firstName", "") + " " + x.get("lastName", "")
            if isinstance(x, dict)
            else ""
        )
    )
    df_checks["owner_email"] = df_checks["owner"].apply(
        lambda x: x.get("email", "") if isinstance(x, dict) else ""
    )
    df_checks["lastCheckResultValue"] = df_checks["lastCheckResultValue"].astype(str)

    # Rename columns
    df_checks.rename(columns={"id": "check_id"}, inplace=True)
    df_checks.rename(columns={"name": "check_name"}, inplace=True)
    df_checks.rename(columns={"evaluationStatus": "check_status"}, inplace=True)
    df_checks.rename(columns={"definition": "check_definition"}, inplace=True)
    df_checks.rename(columns={"cloudUrl": "check_url"}, inplace=True)

    # Add attribute names as separate columns with attribute values
    for index, row in df_checks.iterrows():
        attributes_dict = row["attributes"]
        for key in attributes_dict:
            column_name = key.upper()
            column_value = attributes_dict[key]
            df_checks.at[index, column_name] = column_value

    # Drop original columns
    df_checks.drop(columns=["attributes", "datasets", "owner"], inplace=True)

    # Convert to str
    df_checks = df_checks.astype(str)

    df_checks.rename(columns={"column": "check_column"}, inplace=True)
    df_checks.rename(columns={"group": "check_group"}, inplace=True)

    # Save in Redshift
    def execute_query(conn, query):
        with conn.cursor() as cur:
            cur.execute(query)
            conn.commit()

    def table_exists(conn, table_name):
        query = "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)"
        with conn.cursor() as cur:
            cur.execute(query, (table_name.lower(),))
            return cur.fetchone()[0]

    def create_table(conn, df, table_name):
        columns_with_types = ", ".join([f"{col} VARCHAR(1000)" for col in df.columns])
        query = f"CREATE TABLE {table_name} ({columns_with_types})"
        execute_query(conn, query)

    def insert_into_table(conn, df, table_name):
        columns = ", ".join(df.columns)
        values = ", ".join(["%s"] * len(df.columns))
        insert_query = f"INSERT INTO {table_name} ({columns}) VALUES ({values})"
        
        with conn.cursor() as cur:
            for row in df.itertuples(index=False, name=None):
                # Truncate any string value exceeding 500 characters
                truncated_row = tuple(
                    (str(item)[:500] if isinstance(item, str) and len(item) > 500 else item) 
                    for item in row
                )
                cur.execute(insert_query, truncated_row)
            conn.commit()



    # Check for existing table and add new columns if needed
    def update_table_structure(conn, df, table_name):
        existing_columns_query = (
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
        )
        with conn.cursor() as cur:
            cur.execute(existing_columns_query, (table_name.lower(),))
            existing_columns = [row[0] for row in cur.fetchall()]
            new_columns = [
                col for col in df.columns if col.lower() not in existing_columns
            ]
            for col in new_columns:
                alter_table_query = sql.SQL(
                    "ALTER TABLE {} ADD COLUMN {} VARCHAR(1000)"
                ).format(sql.Identifier(table_name), sql.Identifier(col))
                execute_query(conn, alter_table_query)
                print(f"Added new column {col} to {table_name}")

    # Create or update datasets table
    print(df_datasets)
    print(df_checks)
    if not table_exists(conn, datasets_table):
        create_table(conn, df_datasets, datasets_table)
    else:
        update_table_structure(conn, df_datasets, datasets_table)
    insert_into_table(conn, df_datasets, datasets_table)

    # Create or update checks table
    if not table_exists(conn, checks_table):
        create_table(conn, df_checks, checks_table)
    else:
        update_table_structure(conn, df_checks, checks_table)
    insert_into_table(conn, df_checks, checks_table)

    print(
        f"The following tables in Redshift were updated successfully: {datasets_table}, {checks_table}"
    )
    get_dagster_logger().info(
        f"The following tables in Redshift were updated successfully: {datasets_table}, {checks_table}"
    )
    # Close connection
    conn.close()

if __name__=='__main__':
    export_report()