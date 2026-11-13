# WISE Azure Function

This project collects Intune data, calculates deterministic endpoint indicators,
optionally generates an aggregate-only Mistral narrative, verifies a private Blob
archive, and publishes the dashboard and summary to SharePoint.

Start with [CLIENT-DEPLOYMENT.md](CLIENT-DEPLOYMENT.md) for client prerequisites,
permissions, provisioning, configuration, deployment, and operational checks.
No tenant, application, SharePoint site, or archive account is configured by default.
The optional model requires `MISTRAL_ENDPOINT`, `MISTRAL_DEPLOYMENT`, and
`MISTRAL_API_KEY`; missing model settings fall back to deterministic insights.

Run offline regression tests with Python 3.11:

```powershell
py -3.11 -m unittest test_wise_endpoint_intelligence
```

`Provision-WISESharePointSite.ps1` is a separate administrative operation after
building the SPFx package. Local settings, secrets, environment directories,
reports, and generated packages must never be committed. The timer's optional
configuration-detail collection can be marked partial if Graph throttles it;
check collection coverage before interpreting the report.