SELECT
    c.object_id AS objectId,
    c.column_id AS ordinal,
    c.name,
    t.name AS typeName,
    c.max_length AS maxLength,
    c.precision,
    c.scale,
    c.is_nullable AS nullable,
    c.is_identity AS identityColumn,
    c.is_computed AS computedColumn,
    c.collation_name AS collation
FROM sys.columns AS c
INNER JOIN sys.types AS t ON c.user_type_id = t.user_type_id
INNER JOIN sys.objects AS o ON c.object_id = o.object_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF')
ORDER BY c.object_id, c.column_id;
