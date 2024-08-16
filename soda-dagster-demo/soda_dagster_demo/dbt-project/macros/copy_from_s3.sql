{% macro create_table_if_not_exists(target_table, columns) %}
CREATE TABLE IF NOT EXISTS {{ target_table }} (
    {{ columns | join(", ") }}
);
{% endmacro %}

{% macro copy_from_s3(target_table, s3_paths, iam_role, region='eu-north-1') %}
COPY {{ target_table }}
FROM '{{ s3_path }}'
IAM_ROLE '{{ iam_role }}'
FORMAT AS CSV
IGNOREHEADER 1
REGION '{{ region }}';
{% endmacro %}
