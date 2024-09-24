{{ config(
    materialized='table'
) }}

WITH order_data AS (
    SELECT
        o.store_id,
        o.order_id,
        sum(oi.list_price * oi.quantity) AS order_total,
        count(DISTINCT o.order_id) AS total_orders,
        count(DISTINCT o.customer_id) AS total_customers
    FROM dev.demo.orders o
    LEFT JOIN {{source('dev','order_items')}} oi ON o.order_id = oi.order_id
    GROUP BY o.store_id, o.order_id
)
SELECT
    store_id,
    sum(order_total) AS total_sales,
    avg(order_total) AS average_order_value,
    sum(total_orders) AS total_orders,
    sum(total_customers) AS total_customers
FROM order_data
GROUP BY store_id
