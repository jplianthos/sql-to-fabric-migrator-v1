SELECT
    o.object_id AS objectId,
    s.name AS schemaName,
    o.name,
    o.type AS objectType,
    HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'SELECT') AS canSelect
FROM sys.objects AS o
INNER JOIN sys.schemas AS s ON o.schema_id = s.schema_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF')
ORDER BY s.name, o.name;
