# Keeps a direct Wi-Fi link up for BridgeDrop until stdin closes (which also happens if BridgeDrop dies).
# Reports progress as one JSON object per line. Wi-Fi Direct credentials come from BD_SSID / BD_PASS.
param([ValidateSet('direct', 'hotspot')][string]$Mode = 'direct')

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function Await($operation, [type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait(30000) | Out-Null
    $task.Result
}

function Emit($message) {
    [Console]::Out.WriteLine(($message | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

function Find-LinkAddress {
    $adapters = Get-NetAdapter -IncludeHidden | Where-Object { $_.InterfaceDescription -like '*Wi-Fi Direct*' }
    foreach ($attempt in 1..15) {
        foreach ($adapter in $adapters) {
            $address = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred' } | Select-Object -First 1
            if ($address) { return $address.IPAddress }
        }
        Start-Sleep -Seconds 1
    }
    '192.168.137.1'
}

function Test-WifiOn {
    $null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
    $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
    $wifi = @($radios | Where-Object { $_.Kind -eq 'WiFi' })
    $wifi.Count -gt 0 -and $wifi[0].State -eq 'On'
}

$stdin = [Console]::OpenStandardInput()
$stopSignal = $stdin.ReadAsync((New-Object byte[] 1), 0, 1)

function Start-DirectGroup {
    $null = [Windows.Devices.WiFiDirect.WiFiDirectAdvertisementPublisher, Windows.Devices.WiFiDirect, ContentType = WindowsRuntime]
    $null = [Windows.Devices.WiFiDirect.WiFiDirectConnectionListener, Windows.Devices.WiFiDirect, ContentType = WindowsRuntime]
    $null = [Windows.Security.Credentials.PasswordCredential, Windows.Security.Credentials, ContentType = WindowsRuntime]

    $publisher = New-Object Windows.Devices.WiFiDirect.WiFiDirectAdvertisementPublisher
    $publisher.Advertisement.ListenStateDiscoverability = [Windows.Devices.WiFiDirect.WiFiDirectAdvertisementListenStateDiscoverability]::Normal
    $publisher.Advertisement.IsAutonomousGroupOwnerEnabled = $true
    $legacy = $publisher.Advertisement.LegacySettings
    $legacy.IsEnabled = $true
    $legacy.Ssid = $env:BD_SSID
    $credential = New-Object Windows.Security.Credentials.PasswordCredential
    $credential.Password = $env:BD_PASS
    $legacy.Passphrase = $credential
    $listener = New-Object Windows.Devices.WiFiDirect.WiFiDirectConnectionListener

    $publisher.Start()
    foreach ($attempt in 1..20) {
        if ($publisher.Status -eq 'Started') { break }
        if ($publisher.Status -eq 'Aborted') { break }
        Start-Sleep -Milliseconds 500
    }
    if ($publisher.Status -ne 'Started') {
        Emit @{ status = 'error'; code = 'start-failed' }
        return
    }
    Emit @{ status = 'on'; mode = 'direct'; ip = (Find-LinkAddress); ssid = $env:BD_SSID; passphrase = $env:BD_PASS }
    try {
        while (-not $stopSignal.IsCompleted) {
            Start-Sleep -Seconds 1
            if ($publisher.Status -ne 'Started') {
                Emit @{ status = 'error'; code = 'link-lost' }
                return
            }
        }
    } finally {
        if ($publisher.Status -eq 'Started') { $publisher.Stop() }
        [GC]::KeepAlive($listener)
    }
}

function Start-Hotspot {
    $null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
    $resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if (-not $profile) {
        Emit @{ status = 'error'; code = 'no-internet' }
        return
    }
    $manager = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
    $config = $manager.GetCurrentAccessPointConfiguration()
    # A hotspot the user turned on themselves stays on when BridgeDrop stops.
    $owned = $manager.TetheringOperationalState -eq 'Off'
    if ($owned) {
        $result = Await ($manager.StartTetheringAsync()) $resultType
        if ($result.Status -ne 'Success') {
            Emit @{ status = 'error'; code = 'start-failed' }
            return
        }
    }
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
    $dns = @()
    if ($route) { $dns = @((Get-DnsClientServerAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4).ServerAddresses) }
    Emit @{ status = 'on'; mode = 'hotspot'; ip = (Find-LinkAddress); ssid = $config.Ssid; passphrase = $config.Passphrase; clients = $manager.ClientCount; dns = $dns }
    $clients = $manager.ClientCount
    try {
        while (-not $stopSignal.IsCompleted) {
            Start-Sleep -Seconds 1
            if ($manager.TetheringOperationalState -eq 'Off') {
                $owned = $false
                Emit @{ status = 'error'; code = 'link-lost' }
                return
            }
            if ($manager.ClientCount -ne $clients) {
                $clients = $manager.ClientCount
                Emit @{ clients = $clients }
            }
        }
    } finally {
        if ($owned) { $null = Await ($manager.StopTetheringAsync()) $resultType }
    }
}

try {
    if (-not (Test-WifiOn)) {
        Emit @{ status = 'error'; code = 'wifi-off' }
    } elseif ($Mode -eq 'direct') {
        Start-DirectGroup
    } else {
        Start-Hotspot
    }
} catch {
    Emit @{ status = 'error'; code = 'start-failed'; detail = $_.Exception.Message }
}
