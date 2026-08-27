# Contributing to HYDRA-UMC-PRODUCTION-REPORTS 🦾

We welcome contributions to the automated reporting engine of the HYDRA-UMC platform.

## Technology Stack
- **Runtimes**: Node.js 20+, Python 3.12.
- **Reporting**: Puppeteer (PDF), EJS/Jinja2 (Templates), Chart.js.
- **Metrics**: OEE (Overall Equipment Effectiveness) logic.
- **Data Source**: HYDRA-UMC-DATALAKE (InfluxDB/TimescaleDB).

## Guidelines
1. **Metric Standardization**: Ensure that all OEE and KPI calculations follow the standard industrial definitions (Availability * Performance * Quality).
2. **Template Modularity**: Create reusable reporting components for different robot types (PnP, Laser, assembly).
3. **Data Security**: Reports must not expose sensitive credentials. Ensure only aggregate data is exported to managers.
4. **Automation**: Validate that scheduled reporting tasks handle database timeouts and empty datasets gracefully.
