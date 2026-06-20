# Deploying PixelPivot as a Windows Service

For production deployments on Windows Server, the FastAPI backend should run as a persistent Windows Service. This ensures that the application automatically starts when the server boots and restarts automatically if it crashes.

We recommend using **NSSM (Non-Sucking Service Manager)** to wrap the Python/Uvicorn process.

---

## Prerequisites

1. Download NSSM from [nssm.cc](https://nssm.cc/download).
2. Extract the `nssm.exe` binary (use the `win64` version for 64-bit systems) to a known location, e.g., `C:\PixelPivot\bin\nssm.exe`.

---

## Installation via PowerShell

We have provided a script to automate the installation of the service: [install_windows_service.ps1](file:///F:/dev/pixelpivot_batch/scripts/install_windows_service.ps1).

Run the following command in an **Elevated PowerShell** (Run as Administrator) from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_service.ps1 -NssmPath "C:\path\to\nssm.exe"
```

### Manual Installation

If you prefer to configure the service manually via the NSSM GUI:

1. Open an Administrator command prompt.
2. Run:
   ```cmd
   C:\path\to\nssm.exe install PixelPivotBatchEngine
   ```
3. In the GUI that opens, set the following fields:
   - **Path**: `C:\PixelPivot\python-3.14.5-embed-amd64\python.exe`
   - **Startup directory**: `C:\PixelPivot`
   - **Arguments**: `-m uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000`
4. Go to the **Environment** tab and add any required environment variables:
   ```text
   PIXELPIVOT_DB_PATH=C:\PixelPivot\data\pixelpivot.db
   ```
5. Click **Install service**.

---

## Service Management

You can manage the service using standard Windows tools or command line:

- **Start**: `Start-Service PixelPivotBatchEngine` (or `nssm start PixelPivotBatchEngine`)
- **Stop**: `Stop-Service PixelPivotBatchEngine` (or `nssm stop PixelPivotBatchEngine`)
- **Status**: `Get-Service PixelPivotBatchEngine` (or `nssm status PixelPivotBatchEngine`)
- **Uninstall**: `nssm remove PixelPivotBatchEngine`
