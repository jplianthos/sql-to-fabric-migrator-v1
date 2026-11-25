# Installation and setup

## Prerequisites

Use Windows with:

| Requirement | Purpose |
| --- | --- |
| GitHub Copilot CLI with Agent Plugins 1.0 and native agent-scoped hooks | Interactive agent and approval handling |
| PowerShell 7 (`pwsh`) | Product wrappers and hooks |
| Python 3.11 or later | Local CLI, ODBC reader and Parquet support |
| Node.js 22 or later and npm | Bundled read-only Fabric MCP runtime |
| .NET 8 SDK | Build the pinned SQL Server 2016 syntax guard |
| Microsoft ODBC Driver 18 for SQL Server | Encrypted source and target SQL connections |
| Azure CLI | Your existing interactive Fabric user identity |

Review the [permissions and limitations](permissions-and-safety.md) with your
SQL Server and Fabric administrators before attempting data movement.
Network access must allow your source SQL Server, Microsoft identity services,
Fabric REST, OneLake and the Fabric SQL analytics endpoint. SQL endpoint
connectivity requires the appropriate SQL network access, not just HTTPS.

## Prepare the plugin folder

Place this entire folder in a location you control. Do not copy connection
helpers, credentials, another user's configuration or runtime state into it.
Run these commands **from the plugin folder**, before starting the guarded
Copilot session:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
npm ci --ignore-scripts --no-audit --no-fund
dotnet restore tools\S2F.ScriptDom\S2F.ScriptDom.csproj --locked-mode
dotnet build tools\S2F.ScriptDom\S2F.ScriptDom.csproj `
  --configuration Release --no-restore
```

Use your organization's approved package sources and installation procedures.
Do not install different dependency versions to work around an unexplained
failure. The plugin prefers the `.venv` in its own folder.

## Prepare identity and a separate migration project

Sign in to GitHub Copilot and, separately, Azure CLI using your organization's
approved interactive process. Select the intended Azure CLI user, tenant and
subscription yourself, outside the guarded agent session. The plugin checks
that these match your explicit selections; it will not sign in, switch accounts
or change subscriptions for you. Azure CLI user authentication is the supported
Fabric authentication route; recording another catalog choice does not
implement it.

Create an empty migration project **outside the plugin installation**. The
project stores approved configuration, plans, Parquet and audit outputs.
Keep the plugin's host-managed private data directory separate as well.
Use ordinary, non-linked Windows folders without single quotes in their paths;
do not use junctions, symbolic links or a shared project for concurrent pilots.

From your migration project, start an interactive session, replacing the
placeholder with the actual absolute plugin folder:

```powershell
copilot --plugin-dir '<absolute-plugin-folder>' `
  --agent sql-to-fabric-migrator:migration-lead
```

Review the folder-trust prompt and trust only code and folders you have
reviewed. At the Copilot prompt, select manual permissions:

```text
/permissions manual
```

Do not use allow-all, assisted approval or unattended automation for a migration.
Use the selected `migration-lead`; another agent's name is not write authority.

## First prompt

```text
Interview me for a one-table SQL Server 2016 to Fabric bronze migration.
Use only my explicit answers; do not choose resources, names or budgets.
Use the interview pathways for configuration and movement requests.
Explain any provisioning request file I must review and save separately.
Explain the supported route and its limits, check prerequisites, then walk me
through separate configuration, provisioning, extraction, upload, load and
reconciliation approvals. Stop on any unsupported choice or failed check.
```

The session-start hook supplies the exact local command prefix. Let the agent
use that prefix unchanged; do not invent session identifiers, private state
paths or grant arguments.

The [walkthrough](user-guide.md) explains what you should see. When updating the
package, restore its pinned dependencies and start a fresh session with the
updated folder; an older installed/cached revision may lack these commands.
