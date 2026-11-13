# WISE Endpoint Intelligence

WISE collects Microsoft Intune evidence, computes endpoint health metrics, and
publishes a SharePoint dashboard. A scheduled Python Azure Function validates and
archives reports before publication. An optional Mistral integration generates
guidance from aggregate facts; deterministic metrics remain authoritative.

## Components

- [Azure Function and deployment guide](Data_Collection_DB/EndpointHealthReport_Python/CLIENT-DEPLOYMENT.md): collection, analytics, archive, publication, and SharePoint provisioning.
- [SPFx dashboard source](EndpointIQ-SPFx/endpoint-iq): build the SharePoint app with Node 22.14 or later within major version 22.
- Offline Python and frontend tests are included alongside the source.

## Build and deploy

Follow the deployment guide for Entra permissions, Azure resources, private Blob
storage, SharePoint provisioning, optional model setup, and acceptance checks.
Explicitly configure all tenant-specific values; no live tenant defaults or secrets
are included. The report can contain identifiable device and user information;
restrict access to the archive and SharePoint site.

```powershell
Set-Location Data_Collection_DB/EndpointHealthReport_Python
py -3.11 -m unittest test_wise_endpoint_intelligence
Set-Location ../../EndpointIQ-SPFx/endpoint-iq
npm ci
npm run build
```

The Function is deployed separately from the SharePoint app. Do not commit local
settings, credentials, tenant exports, virtual environments, or generated packages.