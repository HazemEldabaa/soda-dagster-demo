import os
import sys
import requests
import time
from dotenv import load_dotenv
from dagster import get_dagster_logger

def get_urls(input_table_names, input_datasources):
    """
    This function fetches datasets from Soda Cloud and returns only the datasets
    that match the input table names and datasources.
    
    :param input_table_names: List of table names you want to extract.
    :param input_datasources: List of datasource names to filter datasets.
    :return: List of dictionaries with matching table names, datasources, and cloud URLs.
    """
    load_dotenv()

    # Soda Cloud Instance
    soda_cloud_url = "https://demo.soda.io"  # Your Soda Cloud URL
    soda_apikey = os.getenv("soda_api_key_id")  # User API key ID from Soda Cloud
    soda_apikey_secret = os.getenv("soda_api_key_secret")  # User API key secret from Soda Cloud

    # Initialize list to store matching dataset names and cloud URLs
    datasets_info = []

    # Fetch datasets and iterate over all pages to get cloudUrl for each dataset
    response_datasets = requests.get(
        soda_cloud_url + "/api/v1/datasets?page=0",
        auth=(soda_apikey, soda_apikey_secret),
    )

    if response_datasets.status_code in [401, 403]:
        print("Unauthorized or Forbidden access. Please check your API keys and/or permissions in Soda.")
        sys.exit()

    if response_datasets.status_code == 200:
        dataset_pages = response_datasets.json().get("totalPages", 1)

        i = 0
        while i < dataset_pages:
            dq_datasets = requests.get(
                soda_cloud_url + "/api/v1/datasets?page=" + str(i),
                auth=(soda_apikey, soda_apikey_secret),
            )

            if dq_datasets.status_code == 200:
                print(f"Fetching all datasets on page: {i}")
                get_dagster_logger().info(f"Fetching all datasets on page: {i}")
                page_data = dq_datasets.json().get("content", [])
                
                # Collect dataset names and cloud URLs only if they match the input table names and datasources
                for dataset in page_data:
                    dataset_name = dataset.get("name")
                    datasource_name = dataset.get("datasource", {}).get("name")
                    cloud_url = dataset.get("cloudUrl")

                    if dataset_name in input_table_names and datasource_name in input_datasources:
                        datasets_info.append({
                            "table_name": dataset_name,
                            "datasource": datasource_name,
                            "cloud_url": cloud_url
                        })
                
                i += 1
            elif dq_datasets.status_code == 429:
                print(f"API Rate Limit reached when fetching datasets on page: {i}. Pausing for 30 seconds.")
                time.sleep(30)
            else:
                print(f"Error fetching datasets on page {i}. Status code: {dq_datasets.status_code}")
                sys.exit()
    else:
        print(f"Error fetching initial datasets. Status code: {response_datasets.status_code}")
        sys.exit()

    # Return the list of matching table names, datasources, and their corresponding cloud URLs
    return datasets_info

