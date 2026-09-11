try {
    & (Join-Path $PSScriptRoot 'start.ps1')
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message, '拾光启动失败') | Out-Null
}
