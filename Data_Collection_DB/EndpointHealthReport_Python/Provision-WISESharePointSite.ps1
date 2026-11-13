[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$SiteUrl = '<YOUR_SITE_URL>',
    [string]$TenantId = '<YOUR_TENANT_ID>',
    [string]$PnPClientId = '<YOUR_PNP_CLIENT_ID>',
    [switch]$ProvisionTenantAppCatalog,
    [string]$TenantAppCatalogUrl,
    [string]$TenantAppCatalogOwner,
    [Nullable[int]]$TenantAppCatalogTimeZoneId,
    [string]$PackagePath = (Join-Path $PSScriptRoot '../../EndpointIQ-SPFx/endpoint-iq/sharepoint/solution/endpoint-iq.sppkg'),
    [switch]$PublishPage,
    [switch]$SetHomePage
)

$ErrorActionPreference = 'Stop'

$solutionId = [guid]'43387551-b243-44a9-9f6c-61856c69b0ae'
$componentId = [guid]'b1d9022e-a2e5-4af8-92c6-0ec92c6b6c0e'
$listTitle = 'WISE Endpoint Intelligence Summary'
$listMarker = 'WISE Endpoint Intelligence source-backed statistics v2'
$rowTitle = 'WISE Endpoint Intelligence Report Statistics'
$folderSiteRelativeUrl = 'SiteAssets/WISEEndpointIntelligence'
$dashboardName = 'dashboard.json'
$pageName = 'WISE-Endpoint-Intelligence.aspx'
$pageTitle = 'WISE Endpoint Intelligence'

if (-not [guid]::TryParse($TenantId, [ref]([guid]::Empty))) {
    throw 'TenantId must be a tenant UUID.'
}
$TenantId = ([guid]$TenantId).ToString()
$parsedSite = $null
if (-not [uri]::TryCreate($SiteUrl, [UriKind]::Absolute, [ref]$parsedSite) -or
    $parsedSite.Scheme -ne 'https' -or
    $parsedSite.Host -notmatch '^[a-z0-9-]+\.sharepoint\.com$' -or
    $parsedSite.AbsolutePath -notmatch '^/(sites|teams)/[^/]+/?$') {
    throw 'SiteUrl must be an HTTPS tenant.sharepoint.com/sites/name or /teams/name URL.'
}
$SiteUrl = $SiteUrl.TrimEnd('/')

if ($SetHomePage -and -not $PublishPage) {
    throw 'SetHomePage requires PublishPage; a draft page cannot be the site homepage.'
}
if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
    throw "The production SPFx package was not found: $PackagePath. Build endpoint-iq first."
}
if ($PSVersionTable.PSVersion -lt [version]'7.4') {
    throw 'PowerShell 7.4 or newer is required by current PnP.PowerShell releases. Run this script from PowerShell 7 (pwsh), not Windows PowerShell 5.1.'
}
if ($WhatIfPreference) {
    if (-not (Get-Module -ListAvailable -Name PnP.PowerShell)) {
        Write-Output 'WhatIf: install PnP.PowerShell from PSGallery for the current user.'
    }
    Write-Output "WhatIf: connect to the SharePoint admin site for catalog discovery and to $SiteUrl for site provisioning using interactive PnP authentication."
    if ($ProvisionTenantAppCatalog) {
        Write-Output "WhatIf: if no tenant app catalog exists, create/register '$TenantAppCatalogUrl' with owner '$TenantAppCatalogOwner' and timezone ID '$TenantAppCatalogTimeZoneId'. No existing catalog will be replaced."
    } else {
        Write-Output 'WhatIf: require an existing tenant app catalog; add -ProvisionTenantAppCatalog and its URL, owner, and time-zone ID parameters to create one if absent.'
    }
    Write-Output "WhatIf: publish solution $solutionId from $PackagePath to the tenant app catalog and install it on the site."
    Write-Output 'WhatIf: create/validate the SiteAssets document library and WISEEndpointIntelligence folder.'
    Write-Output "WhatIf: ensure SiteAssets/WISEEndpointIntelligence/dashboard.json has the schema-v2 tenant ownership seed."
    Write-Output "WhatIf: create/validate '$listTitle', its writable columns and the single owned summary row."
    Write-Output "WhatIf: create/validate '$pageName' and add web part $componentId."
    if ($PublishPage) { Write-Output 'WhatIf: publish the page.' } else { Write-Output 'WhatIf: leave the page in draft.' }
    if ($SetHomePage) { Write-Output 'WhatIf: set the dashboard page as the site homepage.' }
    return
}
$pnpModule = Get-Module -ListAvailable -Name PnP.PowerShell | Sort-Object Version -Descending | Select-Object -First 1
if (-not $pnpModule) {
    $gallery = Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue
    if (-not $gallery) { throw 'PSGallery is not registered. Configure an approved PowerShell Gallery repository, then rerun this script.' }
    if ($PSCmdlet.ShouldProcess('Current PowerShell user', 'Install PnP.PowerShell from PSGallery')) {
        Install-Module -Name PnP.PowerShell -Repository PSGallery -Scope CurrentUser -Force -AllowClobber
    }
    $pnpModule = Get-Module -ListAvailable -Name PnP.PowerShell | Sort-Object Version -Descending | Select-Object -First 1
    if (-not $pnpModule) { throw 'PnP.PowerShell installation did not complete. Check Gallery access and retry.' }
}
Import-Module PnP.PowerShell -RequiredVersion $pnpModule.Version -ErrorAction Stop

$singleLineFields = @('CollectionTimestamp', 'ReportTitle', 'Tenant', 'GeneratedBy', 'SourceSha256')
$multiLineFields = @('SourceSystems', 'SourceSummaryJson')
$numberFields = @(
    'TotalDevices', 'TotalUsers', 'CompliantDevices', 'NonCompliantDevices',
    'ConfigManagerDevices', 'EncryptedDevices', 'UnencryptedDevices', 'StaleDevices',
    'NonStaleDevices', 'StorageMeasuredDevices', 'CriticalStorageDevices',
    'LowStorageDevices', 'AutopilotRegistered', 'InGracePeriod', 'UnknownDevices',
    'ThreatDevices', 'UnknownThreatDevices', 'OrphanDevices', 'AutopilotEnrolled',
    'AutopilotNotContacted', 'PatchCurrentOrRecent', 'PatchSlightlyBehind',
    'PatchVeryOutdated', 'PatchCritical'
)

function Get-ExistingPage {
    param([string]$Identity, $Connection)
    try {
        return Get-PnPPage -Identity $Identity -Connection $Connection -ErrorAction Stop
    } catch {
        if ($_.Exception.Message -match '(?i)(404|not found|does not exist)') { return $null }
        throw
    }
}

function Get-ExistingDashboardText {
    param([string]$ServerRelativeUrl, $Connection)
    try {
        return Get-PnPFile -Url $ServerRelativeUrl -AsString -Connection $Connection -ErrorAction Stop
    } catch {
        if ($_.Exception.Message -match '(?i)(404|file not found|does not exist)') { return $null }
        throw
    }
}

$connection = Connect-PnPOnline -Url $SiteUrl -ClientId $PnPClientId -Tenant $TenantId -Interactive -ReturnConnection
$web = Get-PnPWeb -Includes EffectiveBasePermissions, ServerRelativeUrl -Connection $connection
if ($web.Url.TrimEnd('/') -ine $SiteUrl) {
    throw 'PnP authenticated to a different SharePoint site than SiteUrl.'
}
if (-not $web.EffectiveBasePermissions.Has([Microsoft.SharePoint.Client.PermissionKind]::ManageWeb)) {
    throw 'The signed-in operator needs site ManageWeb permissions. App catalog publishing also requires SharePoint administrator permissions.'
}

$siteAssetsPath = "$($web.ServerRelativeUrl.TrimEnd('/'))/SiteAssets"
$allLists = @(Get-PnPList -Includes RootFolder -Connection $connection)
$siteAssetsLibraries = @($allLists | Where-Object {
    $_.BaseTemplate -eq 101 -and $_.RootFolder.ServerRelativeUrl.TrimEnd('/') -ieq $siteAssetsPath
})
if ($siteAssetsLibraries.Count -gt 1) { throw 'SiteAssets library path is ambiguous; refusing to provision.' }
if (-not $siteAssetsLibraries.Count) {
    $siteAssetsConflict = @($allLists | Where-Object { $_.RootFolder.ServerRelativeUrl.TrimEnd('/') -ieq $siteAssetsPath })
    if ($siteAssetsConflict.Count) { throw 'A non-document library already occupies the SiteAssets path.' }
    if ($PSCmdlet.ShouldProcess($SiteUrl, 'Create the Site Assets document library at SiteAssets')) {
        New-PnPList -Title 'Site Assets' -Template DocumentLibrary -Url 'SiteAssets' -Connection $connection | Out-Null
    }
}
$verifiedLibraries = @(Get-PnPList -Includes RootFolder -Connection $connection | Where-Object {
    $_.BaseTemplate -eq 101 -and $_.RootFolder.ServerRelativeUrl.TrimEnd('/') -ieq $siteAssetsPath
})
if (-not $verifiedLibraries.Count -and -not $WhatIfPreference) { throw 'SiteAssets document library creation/verification failed.' }

$adminUrl = 'https://' + ($parsedSite.Host -replace '\.sharepoint\.com$', '-admin.sharepoint.com')
$adminConnection = Connect-PnPOnline -Url $adminUrl -ClientId $PnPClientId -Tenant $TenantId -Interactive -ReturnConnection
$catalogUrl = Get-PnPTenantAppCatalogUrl -Connection $adminConnection
if (-not $catalogUrl) {
    if (-not $ProvisionTenantAppCatalog) {
        throw 'No tenant app catalog exists. Rerun with -ProvisionTenantAppCatalog and provide TenantAppCatalogUrl, TenantAppCatalogOwner, and TenantAppCatalogTimeZoneId. This is a tenant-wide SharePoint admin operation.'
    }
    if ([string]::IsNullOrWhiteSpace($TenantAppCatalogUrl) -or
        [string]::IsNullOrWhiteSpace($TenantAppCatalogOwner) -or
        $null -eq $TenantAppCatalogTimeZoneId) {
        throw 'Tenant app catalog creation requires TenantAppCatalogUrl, TenantAppCatalogOwner, and TenantAppCatalogTimeZoneId.'
    }
    $parsedCatalogUrl = $null
    if (-not [uri]::TryCreate($TenantAppCatalogUrl, [UriKind]::Absolute, [ref]$parsedCatalogUrl) -or
        $parsedCatalogUrl.Scheme -ne 'https' -or
        $parsedCatalogUrl.Host -ine $parsedSite.Host -or
        $parsedCatalogUrl.AbsolutePath -notmatch '^/sites/[^/]+/?$' -or
        $parsedCatalogUrl.Query -or $parsedCatalogUrl.Fragment) {
        throw 'TenantAppCatalogUrl must be an HTTPS /sites/name URL on the same tenant host as SiteUrl.'
    }
    $TenantAppCatalogUrl = $TenantAppCatalogUrl.TrimEnd('/')
    if ($TenantAppCatalogUrl -ieq $SiteUrl) { throw 'Tenant app catalog URL must be different from the WISE destination site URL.' }
    if ($PSCmdlet.ShouldProcess($TenantAppCatalogUrl, 'Create and register the tenant-wide SharePoint app catalog')) {
        $adminCatalogUrl = Get-PnPTenantAppCatalogUrl -Connection $adminConnection
        if ($adminCatalogUrl) {
            throw "A tenant app catalog was registered while setup was running: $adminCatalogUrl. Rerun provisioning to use the existing catalog."
        }
        Register-PnPAppCatalogSite -Url $TenantAppCatalogUrl -Owner $TenantAppCatalogOwner -TimeZoneId $TenantAppCatalogTimeZoneId.Value -Connection $adminConnection
        $catalogUrl = Get-PnPTenantAppCatalogUrl -Connection $adminConnection
        if ($catalogUrl.TrimEnd('/') -ine $TenantAppCatalogUrl) {
            throw 'Tenant app catalog registration did not resolve to the requested URL.'
        }
    }
    if (-not $catalogUrl -and $WhatIfPreference) { $catalogUrl = $TenantAppCatalogUrl }
    if (-not $catalogUrl) { throw 'Tenant app catalog creation was not approved or did not complete.' }
}

$siteServerRelative = $web.ServerRelativeUrl.TrimEnd('/')
$dashboardServerRelative = "$siteServerRelative/$folderSiteRelativeUrl/$dashboardName"
$dashboardText = Get-ExistingDashboardText -ServerRelativeUrl $dashboardServerRelative -Connection $connection
if ($dashboardText) {
    try { $dashboardExisting = $dashboardText | ConvertFrom-Json -Depth 20 } catch { throw 'Existing dashboard.json is not valid JSON; refusing to overwrite it.' }
    if ($dashboardExisting.schemaVersion -ne 2 -or ([string]$dashboardExisting.metadata.tenant).ToLowerInvariant() -ne $TenantId.ToLowerInvariant()) {
        throw 'Existing dashboard.json has a different schema or tenant owner; refusing to overwrite it.'
    }
}

$summaryList = Get-PnPList -Identity $listTitle -Connection $connection -ErrorAction SilentlyContinue
if ($summaryList -and $summaryList.Description -ne $listMarker) {
    throw "A list named '$listTitle' exists but lacks the WISE ownership description; refusing to modify it."
}

$page = Get-ExistingPage -Identity $pageName -Connection $connection
if ($page) {
    if ($page.PageTitle -ne $pageTitle) { throw "A page named '$pageName' exists with a different title; refusing to modify it." }
    $foreignControls = @($page.Controls | Where-Object { $_.WebPartId -and [guid]$_.WebPartId -ne $componentId })
    if ($foreignControls.Count) { throw "The existing '$pageName' page has other web parts; refusing to change it." }
    if (@($page.Controls | Where-Object { [guid]$_.WebPartId -eq $componentId }).Count -gt 1) {
        throw 'The existing WISE page contains duplicate WISE web parts; resolve duplicates before provisioning.'
    }
}

if ($PSCmdlet.ShouldProcess($catalogUrl, 'Upload and publish the WISE SPFx solution to the tenant app catalog')) {
    $catalogConnection = Connect-PnPOnline -Url $catalogUrl -ClientId $PnPClientId -Tenant $TenantId -Interactive -ReturnConnection
    $null = Add-PnPApp -Path $PackagePath -Scope Tenant -Overwrite -Connection $catalogConnection
    Publish-PnPApp -Identity $solutionId -Scope Tenant -Connection $catalogConnection
}

$installedApp = Get-PnPApp -Identity $solutionId -Scope Tenant -Connection $connection -ErrorAction SilentlyContinue
if (-not $installedApp -or -not $installedApp.InstalledVersion) {
    if ($PSCmdlet.ShouldProcess($SiteUrl, 'Install the WISE SPFx solution on this site')) {
        Install-PnPApp -Identity $solutionId -Scope Tenant -Connection $connection
    }
} elseif ($PSCmdlet.ShouldProcess($SiteUrl, 'Update the installed WISE SPFx solution')) {
    Update-PnPApp -Identity $solutionId -Scope Tenant -Connection $connection
}

$folder = Resolve-PnPFolder -SiteRelativePath $folderSiteRelativeUrl -Connection $connection
if (-not $dashboardText -and $PSCmdlet.ShouldProcess($dashboardServerRelative, 'Create tenant-owned dashboard seed file')) {
    $seed = [ordered]@{ schemaVersion = 2; metadata = [ordered]@{ tenant = $TenantId } } | ConvertTo-Json -Compress
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($seed)
    $stream = [System.IO.MemoryStream]::new($bytes)
    try {
        $null = Add-PnPFile -Stream $stream -FileName $dashboardName -Folder $folder.ServerRelativeUrl -Connection $connection
    } finally { $stream.Dispose() }
}

if (-not $summaryList -and $PSCmdlet.ShouldProcess($SiteUrl, "Create the '$listTitle' summary list")) {
    $summaryList = New-PnPList -Title $listTitle -Template GenericList -Connection $connection
    Set-PnPList -Identity $listTitle -Description $listMarker -Connection $connection | Out-Null
}
if (-not $summaryList -and $WhatIfPreference) {
    Write-Output 'WhatIf: list and row creation were previewed; no further changes were made.'
    return
}

$fields = @(Get-PnPField -List $listTitle -Connection $connection)
foreach ($fieldName in $singleLineFields) {
    $field = @($fields | Where-Object InternalName -eq $fieldName | Select-Object -First 1)
    if ($field.Count -and $field[0].TypeAsString -ne 'Text') { throw "Existing field '$fieldName' has type '$($field[0].TypeAsString)', expected Text." }
    if (-not $field.Count -and $PSCmdlet.ShouldProcess($listTitle, "Create text column '$fieldName'")) {
        Add-PnPField -List $listTitle -DisplayName $fieldName -InternalName $fieldName -Type Text -Connection $connection | Out-Null
    }
}
foreach ($fieldName in $multiLineFields) {
    $field = @($fields | Where-Object InternalName -eq $fieldName | Select-Object -First 1)
    if ($field.Count -and $field[0].TypeAsString -ne 'Note') { throw "Existing field '$fieldName' has type '$($field[0].TypeAsString)', expected Note." }
    if (-not $field.Count -and $PSCmdlet.ShouldProcess($listTitle, "Create plain-text multi-line column '$fieldName'")) {
        Add-PnPField -List $listTitle -DisplayName $fieldName -InternalName $fieldName -Type Note -Connection $connection | Out-Null
    }
}
foreach ($fieldName in $numberFields) {
    $field = @($fields | Where-Object InternalName -eq $fieldName | Select-Object -First 1)
    if ($field.Count -and $field[0].TypeAsString -ne 'Number') { throw "Existing field '$fieldName' has type '$($field[0].TypeAsString)', expected Number." }
    if (-not $field.Count -and $PSCmdlet.ShouldProcess($listTitle, "Create zero-decimal number column '$fieldName'")) {
        Add-PnPField -List $listTitle -DisplayName $fieldName -InternalName $fieldName -Type Number -Connection $connection | Out-Null
        Set-PnPField -List $listTitle -Identity $fieldName -Values @{ Decimals = 0; Required = $false } -Connection $connection | Out-Null
    }
}

$fields = @(Get-PnPField -List $listTitle -Connection $connection)
$requiredFieldNames = @('Title') + $singleLineFields + $multiLineFields + $numberFields
$writableNames = @($fields | Where-Object { -not $_.ReadOnlyField } | Select-Object -ExpandProperty InternalName)
$missingFields = @($requiredFieldNames | Where-Object { $_ -notin $writableNames })
if ($missingFields.Count) { throw "Summary list is missing writable fields: $($missingFields -join ', ')." }

$rows = @(Get-PnPListItem -List $listTitle -Fields 'ID', 'Title', 'Tenant' -PageSize 2 -Connection $connection)
if ($rows.Count -gt 1) { throw 'Summary list has multiple rows; expected exactly one tenant-owned WISE row.' }
if ($rows.Count -eq 1) {
    if ($rows[0]['Title'] -ne $rowTitle -or ([string]$rows[0]['Tenant']).ToLowerInvariant() -ne $TenantId.ToLowerInvariant()) {
        throw 'The existing summary row is not the expected tenant-owned WISE row; refusing to modify it.'
    }
} elseif ($PSCmdlet.ShouldProcess($listTitle, 'Create the initial tenant-owned WISE summary row')) {
    $null = Add-PnPListItem -List $listTitle -Values @{ Title = $rowTitle; Tenant = $TenantId } -Connection $connection
}

$page = Get-ExistingPage -Identity $pageName -Connection $connection
if (-not $page) {
    if ($PSCmdlet.ShouldProcess($SiteUrl, "Create draft page '$pageTitle'")) {
        $page = Add-PnPPage -Name $pageName -Title $pageTitle -LayoutType SingleWebPartAppPage -Connection $connection
    }
}
if ($page -and @($page.Controls | Where-Object { [guid]$_.WebPartId -eq $componentId }).Count -eq 0) {
    $component = @($page.AvailablePageComponents() | Where-Object { [guid]$_.Id -eq $componentId })
    if ($component.Count -ne 1) { throw 'WISE web part is unavailable on this site. Check that the solution was approved and installed.' }
    if ($PSCmdlet.ShouldProcess($pageName, 'Add the WISE dashboard web part')) {
        $webPart = $page.NewWebPart($component[0])
        $webPart.PropertiesJson = '{"colorScheme":"teal"}'
        $page.AddControl($webPart)
        $null = $page.Save()
    }
}

if ($PublishPage -and $PSCmdlet.ShouldProcess($pageName, 'Publish the dashboard page')) {
    Set-PnPPage -Identity $pageName -Publish -Connection $connection | Out-Null
}
if ($SetHomePage -and $PSCmdlet.ShouldProcess($pageName, 'Set the dashboard as the site homepage')) {
    Set-PnPHomePage -RootFolderRelativeUrl "SitePages/$pageName" -Connection $connection
}

$verifiedDashboard = Get-ExistingDashboardText -ServerRelativeUrl $dashboardServerRelative -Connection $connection
if ($verifiedDashboard) {
    $verified = $verifiedDashboard | ConvertFrom-Json -Depth 20
    if ($verified.schemaVersion -ne 2 -or ([string]$verified.metadata.tenant).ToLowerInvariant() -ne $TenantId.ToLowerInvariant()) {
        throw 'Dashboard seed ownership verification failed.'
    }
}
$verifiedPage = Get-ExistingPage -Identity $pageName -Connection $connection
if ($verifiedPage -and @($verifiedPage.Controls | Where-Object { [guid]$_.WebPartId -eq $componentId }).Count -ne 1) {
    throw 'WISE dashboard web part verification failed.'
}

Write-Output "Provisioned SharePoint assets for $SiteUrl."
Write-Output "Dashboard seed: $dashboardServerRelative (ownership only; first Function run replaces it)."
Write-Output "Summary list: $listTitle with one owned row."
Write-Output "Page: $SiteUrl/SitePages/$pageName (published: $([bool]$PublishPage))."
if (-not $PublishPage) { Write-Output 'The page remains a draft. Publish it only after confirming site reader permissions and a successful first data refresh.' }