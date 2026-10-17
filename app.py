"""
Coffee shop demo app: shows the 3 core Prometheus metric types.

  Counter   -> only goes up           (orders_total)
  Gauge     -> goes up AND down       (coffee_beans_stock_grams, orders_in_progress)
  Histogram -> how long things took   (order_duration_seconds)
  
  
"""
import random
import time

from flask import Flask, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

app = Flask(__name__)

# ---------- METRICS ----------
orders_total = Counter("orders_total", "Orders handled, by result", ["status"])
orders_in_progress = Gauge("orders_in_progress", "Orders being prepared right now")
beans_stock = Gauge("coffee_beans_stock_grams", "Coffee beans left in the shop")
order_duration = Histogram("order_duration_seconds", "Time to prepare one order")

# Create the label series at 0 so they exist from the first scrape
orders_total.labels(status="ok")
orders_total.labels(status="failed")

# ---------- APP STATE ----------
state = {"mode": "normal", "beans": 1000}   # mode: normal | errors | slow
beans_stock.set(state["beans"])


@app.route("/order")
def order():
    # track_inprogress(): gauge +1 on entry, -1 on exit
    # time(): records how long this block took into the histogram
    with orders_in_progress.track_inprogress(), order_duration.time():
        if state["mode"] == "slow":
            time.sleep(random.uniform(2, 4))        # slow machine
        else:
            time.sleep(random.uniform(0.1, 0.6))    # normal speed

        if state["mode"] == "errors" or random.random() < 0.05:
            orders_total.labels(status="failed").inc()
            return "Machine broke, order failed", 500

        orders_total.labels(status="ok").inc()
        state["beans"] = max(0, state["beans"] - 20)
        beans_stock.set(state["beans"])
        return "Coffee ready!"


# ---------- SWITCHES FOR EXPERIMENTS ----------
@app.route("/break")
def break_it():
    mode = request.args.get("mode", "errors")     # /break?mode=errors  or  /break?mode=slow
    state["mode"] = mode
    return f"Shop is now in '{mode}' mode"


@app.route("/fix")
def fix():
    state["mode"] = "normal"
    return "Shop is back to normal"


@app.route("/restock")
def restock():
    state["beans"] = 1000
    beans_stock.set(1000)
    return "Beans restocked to 1000g"


# ---------- WHAT PROMETHEUS SCRAPES ----------
@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
