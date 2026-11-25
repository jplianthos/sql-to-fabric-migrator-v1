SELECT
    name,
    compatibility_level AS compatibilityLevel,
    collation_name AS collation,
    state_desc AS state,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION') AS canViewDefinition,
    HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DATABASE STATE') AS canViewState
FROM sys.databases
WHERE name = DB_NAME();
