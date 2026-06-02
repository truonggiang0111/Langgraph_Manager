param(
  [int]$Port = 3342,
  [string]$Bind = "http://127.0.0.1",
  [string]$ArtifactDir = ""
)

$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Window {
  [DllImport("user32.dll")]
  public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")]
  public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")]
  public static extern bool SetProcessDPIAware();
}
public struct RECT {
  public int Left;
  public int Top;
  public int Right;
  public int Bottom;
}
"@

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if ([string]::IsNullOrWhiteSpace($ArtifactDir)) {
  $ArtifactDir = Join-Path (Split-Path $PSScriptRoot -Parent) "data\browser_artifacts"
}
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
try { [Win32Window]::SetProcessDPIAware() | Out-Null } catch {}

function Write-JsonResponse($response, [int]$status, $body) {
  $json = $body | ConvertTo-Json -Depth 12 -Compress
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $response.StatusCode = $status
  $response.ContentType = "application/json; charset=utf-8"
  $response.Headers["Access-Control-Allow-Origin"] = "*"
  $response.OutputStream.Write($bytes, 0, $bytes.Length)
  $response.OutputStream.Close()
}

function Read-JsonBody($request) {
  $reader = New-Object System.IO.StreamReader($request.InputStream, $request.ContentEncoding)
  $raw = $reader.ReadToEnd()
  if ([string]::IsNullOrWhiteSpace($raw)) { return @{} }
  return $raw | ConvertFrom-Json
}

function Get-BodyValue($body, [string]$key, $default = "") {
  if ($null -eq $body) { return $default }
  if ($body -is [hashtable] -and $body.ContainsKey($key) -and $null -ne $body[$key]) {
    return $body[$key]
  }
  $prop = $body.PSObject.Properties[$key]
  if ($null -ne $prop -and $null -ne $prop.Value) {
    return $prop.Value
  }
  return $default
}

function Find-BrowserExe($browser) {
  $names = switch ($browser.ToLowerInvariant()) {
    "edge" { @("msedge.exe") }
    "msedge" { @("msedge.exe") }
    "firefox" { @("firefox.exe") }
    default { @("chrome.exe") }
  }
  $roots = @(
    "$env:ProgramFiles\Google\Chrome\Application",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application",
    "$env:LOCALAPPDATA\Google\Chrome\Application",
    "$env:ProgramFiles\Microsoft\Edge\Application",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application",
    "$env:ProgramFiles\Mozilla Firefox",
    "${env:ProgramFiles(x86)}\Mozilla Firefox"
  )
  foreach ($root in $roots) {
    foreach ($name in $names) {
      $path = Join-Path $root $name
      if (Test-Path -LiteralPath $path) { return $path }
    }
  }
  return $names[0]
}

function Get-BrowserProcesses {
  $processNames = @("chrome", "msedge", "firefox", "brave", "vivaldi", "opera")
  $items = foreach ($proc in Get-Process -ErrorAction SilentlyContinue | Where-Object { $processNames -contains $_.ProcessName }) {
    $visible = $false
    $startTime = ""
    $processPath = ""
    if ($proc.MainWindowHandle -and $proc.MainWindowHandle -ne 0) {
      $visible = [Win32Window]::IsWindowVisible($proc.MainWindowHandle)
    }
    try { $startTime = $proc.StartTime.ToString("o") } catch { $startTime = "" }
    try { $processPath = $proc.Path } catch { $processPath = "" }
    [pscustomobject]@{
      pid = $proc.Id
      name = $proc.ProcessName
      title = $proc.MainWindowTitle
      main_window_handle = [string]$proc.MainWindowHandle
      visible = $visible
      start_time = $startTime
      path = $processPath
    }
  }
  @($items)
}

function Open-Url($body) {
  $url = [string](Get-BodyValue $body "url" "")
  if ([string]::IsNullOrWhiteSpace($url)) { throw "url is required" }
  $browser = [string](Get-BodyValue $body "browser" "")
  if ([string]::IsNullOrWhiteSpace($browser)) {
    Start-Process $url
    return @{ opened = $url; browser = "default" }
  }
  $exe = Find-BrowserExe $browser
  Start-Process -FilePath $exe -ArgumentList @($url)
  return @{ opened = $url; browser = $browser; executable = $exe }
}

function Launch-DebugBrowser($body) {
  $browser = [string](Get-BodyValue $body "browser" "chrome")
  $url = [string](Get-BodyValue $body "url" "about:blank")
  $port = [int](Get-BodyValue $body "port" 9222)
  $profile = [string](Get-BodyValue $body "profile_dir" "$env:TEMP\langgraph-browser-debug-$browser-$port")
  New-Item -ItemType Directory -Force -Path $profile | Out-Null
  $exe = Find-BrowserExe $browser
  $args = @(
    "--remote-debugging-port=$port",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--new-window",
    $url
  )
  Start-Process -FilePath $exe -ArgumentList $args
  return @{
    browser = $browser
    executable = $exe
    url = $url
    remote_debugging_url = "http://127.0.0.1:$port"
    profile_dir = $profile
  }
}

function Get-WindowRectForCapture($hwnd) {
  $rect = New-Object RECT
  if ($hwnd -and $hwnd -ne [IntPtr]::Zero -and [Win32Window]::GetWindowRect($hwnd, [ref]$rect)) {
    $width = [Math]::Max(1, $rect.Right - $rect.Left)
    $height = [Math]::Max(1, $rect.Bottom - $rect.Top)
    return @{ X = $rect.Left; Y = $rect.Top; Width = $width; Height = $height }
  }
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  return @{ X = $bounds.X; Y = $bounds.Y; Width = $bounds.Width; Height = $bounds.Height }
}

function Capture-Screenshot($body) {
  $targetPid = [int](Get-BodyValue $body "pid" 0)
  $fullScreen = [bool](Get-BodyValue $body "full_screen" $false)
  $name = [string](Get-BodyValue $body "name" "")
  $targetDir = [string](Get-BodyValue $body "artifact_dir" $ArtifactDir)
  if ([string]::IsNullOrWhiteSpace($targetDir)) { $targetDir = $ArtifactDir }
  New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

  $hwnd = [IntPtr]::Zero
  $title = ""
  if (-not $fullScreen -and $targetPid -gt 0) {
    $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle -and $proc.MainWindowHandle -ne 0) {
      $hwnd = $proc.MainWindowHandle
      $title = $proc.MainWindowTitle
    }
  }
  if (-not $fullScreen -and $hwnd -eq [IntPtr]::Zero) {
    $hwnd = [Win32Window]::GetForegroundWindow()
  }
  if ($fullScreen) {
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $rectInfo = @{ X = $bounds.X; Y = $bounds.Y; Width = $bounds.Width; Height = $bounds.Height }
  } else {
    $rectInfo = Get-WindowRectForCapture $hwnd
  }

  if ([string]::IsNullOrWhiteSpace($name)) {
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $name = "host_screenshot_$stamp.png"
  }
  $safeName = ($name -replace '[^a-zA-Z0-9_.-]+', '_')
  if (-not $safeName.ToLowerInvariant().EndsWith(".png")) { $safeName = "$safeName.png" }
  $path = Join-Path $targetDir $safeName

  $bitmap = New-Object System.Drawing.Bitmap $rectInfo.Width, $rectInfo.Height
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($rectInfo.X, $rectInfo.Y, 0, 0, $bitmap.Size)
    $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }
  return @{
    path = $path
    title = $title
    pid = $targetPid
    full_screen = $fullScreen
    rect = $rectInfo
  }
}

$listener = [System.Net.HttpListener]::new()
$prefix = "$Bind`:$Port/"
$listener.Prefixes.Add($prefix)
$listener.Start()
Write-Host "Host browser bridge listening on $prefix"

while ($listener.IsListening) {
  $ctx = $listener.GetContext()
  try {
    $path = $ctx.Request.Url.AbsolutePath.TrimEnd("/")
    if ($ctx.Request.HttpMethod -eq "OPTIONS") {
      Write-JsonResponse $ctx.Response 200 @{ ok = $true }
      continue
    }
    switch ($path) {
      "/health" {
        Write-JsonResponse $ctx.Response 200 @{ ok = $true; service = "host-browser-bridge"; port = $Port }
      }
      "/browsers" {
        Write-JsonResponse $ctx.Response 200 @{ ok = $true; browsers = @(Get-BrowserProcesses) }
      }
      "/open-url" {
        $body = Read-JsonBody $ctx.Request
        Write-JsonResponse $ctx.Response 200 @{ ok = $true; result = Open-Url $body }
      }
      "/launch-debug" {
        $body = Read-JsonBody $ctx.Request
        Write-JsonResponse $ctx.Response 200 @{ ok = $true; result = Launch-DebugBrowser $body }
      }
      "/screenshot" {
        $body = Read-JsonBody $ctx.Request
        Write-JsonResponse $ctx.Response 200 @{ ok = $true; result = Capture-Screenshot $body }
      }
      default {
        Write-JsonResponse $ctx.Response 404 @{ ok = $false; error = "not found"; path = $path }
      }
    }
  } catch {
    Write-JsonResponse $ctx.Response 500 @{ ok = $false; error = $_.Exception.Message }
  }
}
