# Coffee shop monitoring demo

## Run
    docker-compose up -d --build
    ./load.sh            # keeps sending orders (separate terminal)

| What        | URL                         |
|-------------|-----------------------------|
| App         | http://<vm-ip>:5000/order   |
| Raw metrics | http://<vm-ip>:5000/metrics |
| Prometheus  | http://<vm-ip>:9090         |
| Alertmanager| http://<vm-ip>:9093         |
| Grafana     | http://<vm-ip>:3000 (admin/admin) |

Stop any older monitoring stack first (`docker-compose down` in its folder)
or ports 9090/9100/3000 will clash.

## Metrics
| Metric                      | Type      | Meaning                    |
|-----------------------------|-----------|----------------------------|
| orders_total{status}        | Counter   | orders done, ok / failed   |
| orders_in_progress          | Gauge     | orders being made now      |
| coffee_beans_stock_grams    | Gauge     | beans left (20g per order) |
| order_duration_seconds      | Histogram | how long each order took   |

## Experiments (each triggers an alert)
    curl localhost:5000/break?mode=errors   # -> HighOrderFailureRate
    curl localhost:5000/break?mode=slow     # -> SlowOrders
    curl localhost:5000/fix
    # keep load.sh running ~50 orders -> LowCoffeeBeans, then:
    curl localhost:5000/restock
    docker-compose stop app                 # -> TargetDown

Watch: Prometheus > Alerts (Inactive -> Pending -> Firing), then Slack.

## Useful PromQL
    rate(orders_total[1m])                                   # orders per second
    sum(rate(orders_total{status="failed"}[1m])) / sum(rate(orders_total[1m]))   # failure ratio
    histogram_quantile(0.95, sum by (le) (rate(order_duration_seconds_bucket[1m])))   # p95
    coffee_beans_stock_grams
    orders_in_progress

## Slack
Put your webhook URL in alertmanager.yml, then:
    docker-compose restart alertmanager


----------
app.py

  1. The app breaks in two ways

Randomly, about 5% of orders fail on their own, like a real machine having a bad moment.
On command, when you visit /break?mode=errors or /break?mode=slow, so you can trigger problems whenever you want.

2. The app records, Prometheus pulls

customer orders -> app does the work and WRITES the result in its metrics (a notebook)
                                      |
                Prometheus comes every 15s and READS the notebook (/metrics)

The app doesn’t know Prometheus exists. It just keeps its notebook up to date and answers when someone asks for it. Prometheus is the one that visits on a schedule, copies the numbers, and stores them with a timestamp. 
That stored history is what lets you draw graphs and set alerts like “failures above 30% for 30 seconds”.


On command, when you visit /break?mode=errors or /break?mode=slow, so you can
who to do this?

Option 1: Browser (easiest)

Open this on your laptop:

http://192.168.25.131:5000/break?mode=errors

You should see: Shop is now in 'errors' mode

To fix it again:

http://192.168.25.131:5000/fix
Option 2: Terminal (curl)

On the VM, or any machine that can reach it:

bash
curl "http://localhost:5000/break?mode=errors"
curl "http://localhost:5000/break?mode=slow"
curl "http://localhost:5000/fix"
curl "http://localhost:5000/restock"

Keep the quotes around the URL. The ? is a special character in some shells and can cause errors without them.

Full experiment, step by step

You need three things open:

Terminal 1 on the VM: ./load.sh (sends orders continuously)
Browser tab A: Prometheus at http://192.168.25.131:9090/alerts
Terminal 2 (or a browser tab) for the break commands

Then:

bash
curl "http://localhost:5000/break?mode=errors"

In Prometheus, run this query in the Graph tab and watch the line:

promql
sum(rate(orders_total{status="failed"}[1m]))

Within about 30-60 seconds the HighOrderFailureRate alert should go from Inactive to Pending to Firing. When you’ve seen it, run:

bash
curl "http://localhost:5000/fix"

and watch the alert resolve.

/break only changes the mode. It does nothing by itself. You need load.sh running (or you refreshing /order repeatedly), because the metrics only change when orders come in.

----------
alert.yml
The shape every rule has
yaml
- alert: Name                 # what it's called
  expr: <PromQL condition>    # when this is TRUE, something is wrong
  for: 30s                    # it must stay true this long before firing
  labels:  { severity: ... }  # tags Alertmanager can route on
  annotations: { ... }        # the message text; {{ $value }} is the number that triggered it
  
  1. HighOrderFailureRate (counter, so use rate())
promql
sum(rate(orders_total{status="failed"}[1m]))
  /
sum(rate(orders_total[1m])) > 0.30
Top line: failed orders per second over the last minute.
Bottom line: all orders per second (ok and failed together).
Dividing gives a ratio between 0 and 1, and > 0.30 means more than 30% are failing.

We use a ratio, not a count, because 5 failures a minute is a disaster when you serve 10 orders and normal when you serve 10,000.

sum(...) is there because rate() returns two separate series (ok and failed), and sum merges them into one number you can divide.

The app fails about 5% of the time naturally, so the alert stays quiet until you run /break?mode=errors, which pushes the ratio to nearly 100%.

2. SlowOrders (histogram, so use histogram_quantile)
promql
histogram_quantile(0.95,
  sum by (le) (rate(order_duration_seconds_bucket[1m]))) > 1.5

This reads from the inside out:

order_duration_seconds_bucket are the buckets of the histogram (“how many orders took ≤ 0.5s, ≤ 1s, ≤ 2.5s...”).
rate(...[1m]) gives how fast each bucket has been filling over the last minute.
sum by (le) merges the buckets but keeps the le label, because histogram_quantile needs it.
histogram_quantile(0.95, ...) answers: “95% of orders finished faster than what time?” That’s the p95.

Why p95 and not the average? An average hides the unlucky customers. If 19 orders take 0.3s and 1 takes 20s, the average looks fine (about 1.3s) while one customer waited 20 seconds. p95 is closer to the experience of your slowest customers.

Normally orders take 0.1-0.6s, so p95 is around 0.6s. In slow mode (2-4s) it jumps to about 4s and the alert fires.

3. LowCoffeeBeans (gauge, so compare directly)
promql
coffee_beans_stock_grams < 200

No rate() here. A gauge already holds the current value, so you compare it as it is. rate() is only for counters that grow forever.

Each order uses 20g, so starting at 1000g you’ll cross 200 after about 40 orders. Then for: 10s passes and the alert fires. Run /restock and it resolves.

4. HighCPU (node_exporter)
promql
100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100) > 80

We covered this one before, so here’s the short version. It takes the idle CPU time, turns it into a rate, averages across the cores of each machine, and subtracts from 100 to get “how busy is the CPU”. The alert fires above 80% for a full minute. This one watches the VM itself, not your app.

The rule of thumb
Metric type	What you do with it	Example
Counter (only goes up)	rate()	failed orders per second
Gauge (up and down)	use directly	beans left
Histogram (durations)	histogram_quantile()	p95 order time

If you remember this table, you can write most alerts yourself.

Why for: matters

Without for, a single bad second would page you. With for: 30s, the alert goes Inactive → Pending (condition true but not long enough) and only becomes Firing if it stays true the whole time. In /alerts you can watch this happen.

Try writing one yourself: an alert that fires when more than 5 orders are in progress at once. Which of the three patterns would you use?

----------

What node_exporter is

Node Exporter is a specialized monitoring agent for Prometheus that collects and exposes hardware- and OS-level metrics (such as CPU, memory, disk, and network statistics) from Linux and Unix-like systems. 
 Written in Go, it acts as a lightweight translator that reads data from the Linux kernel’s /proc and /sys filesystems and exposes them via an HTTP endpoint on port 9100 by default.


Your coffee app reports numbers about the coffee shop (orders, beans). But nobody is reporting numbers about the building the shop sits in: the VM’s CPU, memory, disk and network.

node_exporter does that. It’s a small program that reads the machine’s own stats and publishes them at /metrics, just like your Flask app does. In monitoring, a “node” means a machine.

coffee app      ->  numbers about the SHOP   (orders, beans, order time)
node_exporter   ->  numbers about the MACHINE (CPU, RAM, disk, network)

Both are scraped the same way, which is why they sit side by side in prometheus.yml.



----------
Your whole stack in one picture
node_exporter ─┐
Flask /metrics ─┼──(Prometheus pulls every 15s)──> Prometheus ──> Grafana (charts)
               ┘                                      │
                                          evaluates alert.yml rules
                                                      │
                                              (condition true?)
                                                      ▼
                                               Alertmanager ──> Slack

Everything you’ve learned fits in this picture:

Exporters only expose systems numbers. They don’t store or decide anything.
app metrics: expose app metrics ( count,gauge,histogram)

Exporters and app metrics: right. The difference is who produces the numbers. An exporter like node_exporter translates a system that can’t speak Prometheus. Your app exposes its own numbers through the client library. 
One small note: counters, gauges and histograms aren’t only for apps. node_exporter’s metrics use the same types (node_cpu_seconds_total is a counter, for example).

Prometheus has 3 jobs: 
Scrape the /metrics pages on a schedule
Store the numbers with timestamps
Evaluate the rules in alert_rule.yml against what it stored (and pass firing alerts to Alertmanager)


Alertmanager doesn’t know what’s wrong. It only receives “this alert is firing” and decides where to send it.

Grafana stores no metrics. It asks Prometheus questions and draws the answers.



Here is the whole thing in one line each:

Component	One-line job
App / exporter	Show the numbers
Prometheus	Collect, store, and judge them
Alertmanager	Deliver the bad news
Grafana	Draw the picture

----------

volumes:
  prometheus_data:
  grafana_data:


There are two different kinds of volume in that file, and they look similar.

Kind 1: bind mounts (a file from your VM)
yaml
volumes:
  - ./prometheus.yml:/etc/prometheus/prometheus.yml
  - ./alert.yml:/etc/prometheus/alert.yml

The format is always what's on the VM : where it appears inside the container.

./prometheus.yml is the file in your project folder (the ./ means “this folder”).
/etc/prometheus/prometheus.yml is where Prometheus expects its config inside the container.

The container image has no idea about your config, so you hand it your file this way. It’s like giving the container a shortcut to your file. If you edit prometheus.yml on the VM, the container sees the change immediately (after a reload or restart).

Kind 2: named volumes (storage managed by Docker)
yaml
volumes:
  - prometheus_data:/prometheus

Here the left side has no ./ or /, just a name. That tells Docker: “this isn’t a folder of mine, it’s a storage area that you manage, called prometheus_data.”

/prometheus is where Prometheus keeps its stored data (the wal and chunks_head folders you saw earlier).
Why the bottom volumes: section exists
yaml
volumes:
  prometheus_data:
  grafana_data:

This is where you declare the named volumes. The rule is:

Left side looks like	Kind	Needs declaring at the bottom?
./file.yml or /path	bind mount	No
prometheus_data (a plain name)	named volume	Yes

Without the bottom section, Compose gives an error because prometheus_data isn’t defined anywhere. The empty value after the colon just means “create it with default settings.”

Why use named volumes at all

A container’s own filesystem is temporary. When you run docker-compose down and up again, the container is recreated from the image, and everything it wrote is lost. That’s what happened to your Grafana dashboard earlier.

A named volume lives outside the container, so it survives:

without volume:  down -> container deleted -> data deleted
with volume:     down -> container deleted -> data stays in the volume -> up -> data is back
What’s stored	Volume	What you keep
/prometheus	prometheus_data	metric history
/var/lib/grafana	grafana_data	dashboards, users, settings








App UP time
up{job="app"}

P90 Latency
histogram_quantile(0.9, sum(rate(app_request_latency_seconds_bucket[5m])) by (le))

error rate
sum(rate(app_errors_total[1m])) by (type)

Active Alerts
ALERTS{alertstate="firing"}


Memory & CPU
100 - (avg by (instane)(irate(node_cpu_seconds_total){mode="idle"}[1m])) * 100)

(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100





Multi service dashboard

App Errors
increase(app_errors_total{job="web"}[1m])