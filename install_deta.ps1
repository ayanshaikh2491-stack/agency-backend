# Install Deta Space CLI
try {
    Write-Host "Installing Deta Space CLI..."
    pip install deta-space-cli
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Deta Space CLI installed successfully!"
    } else {
        Write-Host "✗ Failed to install Deta Space CLI"
    }
}