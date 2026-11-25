# SQL to Fabric Migrator

An approval-first GitHub Copilot CLI plugin for a bounded SQL Server 2016 to
Microsoft Fabric bronze migration pilot. The agent collects your choices,
shows exact plans and requests your approval before each write.

 Start with an
authorized, isolated canary. Successful connectivity or a completed load is not
proof that the migrated values match.

## What you can do

- Answer an interactive configuration interview; no hand-written JSON is needed
  for configuration or the extraction/upload/load stages.
- Inspect explicitly selected source databases and supported table metadata.
- Create a new, capacity-backed workspace and Lakehouse through separate plans.
- Extract one supported table to typed Parquet, upload a new OneLake file, load
  a new Lakehouse table and compare actual target rows with the artifact and a
  fresh source read.
- Review plan, operation, ownership and comparison results.

Each write requires its own native permission approval. 

## Start here

1. Follow [installation and setup](docs/getting-started.md).
2. Follow the [first migration walkthrough](docs/user-guide.md).
3. Review [permissions, supported scope and safety](docs/permissions-and-safety.md).
4. Use [troubleshooting](docs/troubleshooting.md) if a step is blocked.

This folder contains the product source, pinned dependency definitions,
schemas, runtime hooks, agent instructions and customer documentation. Install
dependencies locally before use. It contains no migration configuration,
credentials, customer data, private runtime state or development test material.
The `references` folder is required agent operating guidance, not optional
reading to remove from an installation.

 Source and target privileges must be supplied by your
organization; the plugin never grants them.
