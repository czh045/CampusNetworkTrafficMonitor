# Campus Network Traffic Monitor: Project Notes

## Scope

This Flask application is the engineering implementation for a campus-network
traffic monitoring and visualization project. It supports authenticated access,
device inventory management, real-time and historical traffic dashboards,
threshold alerts, optional DingTalk notifications, and short-horizon traffic
prediction.

## Architecture

```text
Browser -> Flask routes/templates -> MySQL
                                 -> Predictor (scikit-learn)
SNMP collector ------------------> MySQL realtime_traffic
Alert worker --------------------> MySQL alerts -> DingTalk (optional)
```

## Data Integrity Notes

- `real_collector.py` collects counter deltas by SNMP when enabled devices have
  an SNMP community configured.
- `utils/traffic_collector.py` is a demo-data collector for UI demonstrations;
  it is intentionally not presented as production telemetry.
- Prediction reports whether it is using a persisted model or a clearly marked
  fallback mode when historical data is insufficient.
- Raw production captures, local models, database passwords, and webhook
  credentials are excluded from Git.

## Portfolio Talking Points

1. The monitoring dashboard aggregates the latest metric for each device rather
   than showing a raw event stream.
2. The analysis API uses indexed time windows in MySQL to support dashboard
   queries and device ranking.
3. Alert generation deduplicates active alerts by device, type, and a time
   window so a noisy device does not generate unlimited duplicates.
4. The application separates normal users from administrators. Registration
   always creates a normal user; administrator accounts are created through the
   bootstrap script or controlled database administration.
